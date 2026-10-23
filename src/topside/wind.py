"""Wind module: ERA5 hourly wind -> hub-height wind -> wind farm power.

Pipeline
--------
1. ``download_era5``      fetch one year of hourly ERA5 wind at one grid point
                          from the Copernicus CDS (one file per month, cached).
2. ``load_era5``          read the NetCDF files into a pandas DataFrame
                          (100 m and 10 m wind speed, UTC hourly index).
3. ``to_hub_height``      shear-correct 100 m wind to turbine hub height.
4. ``turbine_power``      apply the IEA 15 MW power curve.
5. ``farm_power``         scale by number of turbines and losses.
6. ``plot_*``             time series and duration curve.

Run the whole thing with::

    python -m topside.wind                 # uses config/base_case.yaml
    python -m topside.wind --config path/to/other.yaml

Simplifications (documented so they can be revisited):
- Air density is taken as the power-curve reference (1.225 kg/m3). Cold North
  Sea air is slightly denser, so output is slightly underestimated.
- All turbines see the same wind; wake effects are a single loss factor.
- Cut-out is a hard step at 25 m/s (no hysteresis / storm control).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from topside.config import DEFAULT_CONFIG, load_config, param, repo_path

# --------------------------------------------------------------------------
# 1-2. ERA5 download and loading
# --------------------------------------------------------------------------


def era5_month_file(raw_dir: Path, year: int, month: int, lat: float, lon: float) -> Path:
    return raw_dir / f"era5_wind_{lat:g}N_{lon:g}E_{year}_{month:02d}.nc"


def download_era5(cfg: dict, overwrite: bool = False) -> list[Path]:
    """Download one year of hourly ERA5 wind for the site, one file per month.

    Needs a CDS account and an API key in ``~/.cdsapirc`` (see README).
    Months already on disk are skipped, so an interrupted download can simply
    be restarted.
    """
    import cdsapi  # imported here so the rest of the module works without it

    year = int(param(cfg, "era5.year"))
    lat = float(param(cfg, "site.latitude"))
    lon = float(param(cfg, "site.longitude"))
    raw_dir = repo_path(param(cfg, "era5.raw_dir"))
    raw_dir.mkdir(parents=True, exist_ok=True)

    client = cdsapi.Client()
    files = []
    for month in range(1, 13):
        target = era5_month_file(raw_dir, year, month, lat, lon)
        files.append(target)
        if target.exists() and not overwrite:
            continue
        request = {
            "product_type": ["reanalysis"],
            "variable": list(param(cfg, "era5.variables")),
            "year": [str(year)],
            "month": [f"{month:02d}"],
            "day": [f"{d:02d}" for d in range(1, 32)],  # CDS ignores invalid days
            "time": [f"{h:02d}:00" for h in range(24)],
            # Area is [North, West, South, East]; a zero-size box returns the
            # nearest grid point. ERA5 is on a 0.25 deg grid, so 61N 2E is exact.
            "area": [lat, lon, lat, lon],
            "data_format": "netcdf",
            "download_format": "unarchived",
        }
        print(f"Requesting ERA5 {year}-{month:02d} ...")
        client.retrieve(param(cfg, "era5.dataset"), request, str(target))
    return files


def load_era5(files: list[Path]) -> pd.DataFrame:
    """Read ERA5 NetCDF files into a DataFrame with wind speeds in m/s.

    Returns columns ``ws100`` and (if present) ``ws10``, indexed by UTC hour.
    """
    import xarray as xr

    frames = []
    for f in sorted(files):
        with xr.open_dataset(f) as ds:
            # The new CDS (2024+) names the time axis 'valid_time'; old files use 'time'.
            if "valid_time" in ds.dims:
                ds = ds.rename({"valid_time": "time"})
            ds = ds.squeeze(drop=True)  # drop the 1x1 latitude/longitude dims
            df = pd.DataFrame(index=pd.DatetimeIndex(ds["time"].values, name="time_utc"))
            df["ws100"] = np.hypot(ds["u100"].values, ds["v100"].values)
            if "u10" in ds and "v10" in ds:
                df["ws10"] = np.hypot(ds["u10"].values, ds["v10"].values)
            frames.append(df)
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    _check_hourly(out)
    return out


def _check_hourly(df: pd.DataFrame) -> None:
    expected = pd.date_range(df.index[0], df.index[-1], freq="h")
    missing = expected.difference(df.index)
    if len(missing):
        raise ValueError(f"ERA5 series has {len(missing)} missing hours, first: {missing[0]}")
    if df.isna().any().any():
        raise ValueError("ERA5 series contains NaN values")


# --------------------------------------------------------------------------
# 3. Hub-height correction
# --------------------------------------------------------------------------


def shear_exponent_from_profile(ws10, ws100, bounds=(0.0, 0.4)):
    """Hourly power-law exponent from two heights: alpha = ln(U100/U10) / ln(100/10)."""
    ws10 = np.maximum(np.asarray(ws10, dtype=float), 0.1)  # avoid log(0) in calm hours
    ws100 = np.maximum(np.asarray(ws100, dtype=float), 0.1)
    alpha = np.log(ws100 / ws10) / np.log(100.0 / 10.0)
    return np.clip(alpha, *bounds)


def power_law(ws_ref, z_ref: float, z_target: float, alpha):
    """Power-law wind profile: U(z) = U(z_ref) * (z / z_ref)^alpha."""
    return np.asarray(ws_ref, dtype=float) * (z_target / z_ref) ** np.asarray(alpha)


def to_hub_height(era5: pd.DataFrame, cfg: dict) -> pd.Series:
    """Correct ERA5 100 m wind speed to hub height, using the method in the config."""
    method = param(cfg, "wind_shear.method")
    z_ref = float(param(cfg, "wind_shear.reference_height"))
    z_hub = float(param(cfg, "wind_turbine.hub_height"))
    if method == "power_law":
        alpha = float(param(cfg, "wind_shear.alpha"))
    elif method == "era5_profile":
        alpha = shear_exponent_from_profile(
            era5["ws10"], era5["ws100"], tuple(param(cfg, "wind_shear.alpha_bounds"))
        )
    else:
        raise ValueError(f"Unknown wind_shear.method '{method}'")
    return pd.Series(power_law(era5["ws100"], z_ref, z_hub, alpha), index=era5.index, name="ws_hub")


# --------------------------------------------------------------------------
# 4-5. Power curve and wind farm
# --------------------------------------------------------------------------


def load_power_curve(path: str | Path) -> pd.DataFrame:
    """Read the turbine power curve. Returns columns wind_speed [m/s], power [W]."""
    pc = pd.read_csv(repo_path(path), comment="#")
    return pd.DataFrame(
        {"wind_speed": pc["wind_speed_m_s"].to_numpy(), "power": pc["power_MW"].to_numpy() * 1e6}
    )


def turbine_power(ws_hub, power_curve: pd.DataFrame, cut_in: float, cut_out: float, rated: float):
    """Power of one turbine [W] for hub-height wind speeds [m/s].

    Linear interpolation between tabulated points; zero below cut-in and above
    cut-out; never above rated power.
    """
    ws = np.asarray(ws_hub, dtype=float)
    p = np.interp(ws, power_curve["wind_speed"], power_curve["power"], left=0.0, right=0.0)
    p = np.where((ws < cut_in) | (ws > cut_out), 0.0, p)
    return np.clip(p, 0.0, rated)


def farm_power(p_turbine, n_turbines: int, wake_loss: float, electrical_loss: float, availability: float):
    """Net wind farm power delivered to the platform [W]."""
    factor = n_turbines * (1 - wake_loss) * (1 - electrical_loss) * availability
    return np.asarray(p_turbine) * factor


def wind_farm_timeseries(era5: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Full chain from ERA5 to net farm power. All outputs in SI units."""
    ws_hub = to_hub_height(era5, cfg)
    pc = load_power_curve(param(cfg, "wind_turbine.power_curve_file"))
    p_turb = turbine_power(
        ws_hub,
        pc,
        cut_in=float(param(cfg, "wind_turbine.cut_in_speed")),
        cut_out=float(param(cfg, "wind_turbine.cut_out_speed")),
        rated=float(param(cfg, "wind_turbine.rated_power")),
    )
    p_farm = farm_power(
        p_turb,
        n_turbines=int(param(cfg, "wind_farm.n_turbines")),
        wake_loss=float(param(cfg, "wind_farm.wake_loss")),
        electrical_loss=float(param(cfg, "wind_farm.electrical_loss")),
        availability=float(param(cfg, "wind_farm.availability")),
    )
    out = era5.copy()
    out["ws_hub"] = ws_hub
    out["p_turbine_W"] = p_turb
    out["p_farm_W"] = p_farm
    return out


def duration_curve(power) -> np.ndarray:
    """Power sorted from highest to lowest (x-axis is then 'hours exceeded')."""
    return np.sort(np.asarray(power, dtype=float))[::-1]


def summary(ts: pd.DataFrame, cfg: dict) -> dict:
    n = int(param(cfg, "wind_farm.n_turbines"))
    installed = n * float(param(cfg, "wind_turbine.rated_power"))
    p = ts["p_farm_W"]
    hours = len(p)  # hourly data, so each row is 1 h
    return {
        "hours": hours,
        "installed_capacity_MW": installed / 1e6,
        "mean_ws100_m_s": ts["ws100"].mean(),
        "mean_ws_hub_m_s": ts["ws_hub"].mean(),
        "mean_farm_power_MW": p.mean() / 1e6,
        "annual_energy_GWh": p.sum() / 1e9,  # W * 1 h summed -> Wh -> GWh
        "net_capacity_factor": p.mean() / installed,
        "hours_zero_output": int((p <= 0).sum()),
        "hours_at_rated_turbine": int((ts["p_turbine_W"] >= 0.999 * float(param(cfg, "wind_turbine.rated_power"))).sum()),
        "hours_above_cut_out": int((ts["ws_hub"] > float(param(cfg, "wind_turbine.cut_out_speed"))).sum()),
    }


# --------------------------------------------------------------------------
# 6. Figures
# --------------------------------------------------------------------------

# Plot colours
_SERIES = "#2a78d6"
_SERIES_LIGHT = "#a9c8ee"
_INK = "#0b0b0b"
_INK_2 = "#52514e"
_GRID = "#e4e3df"
_SURFACE = "#fcfcfb"


def _style_axes(ax):
    ax.set_facecolor(_SURFACE)
    ax.grid(True, color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_INK_2)
    ax.tick_params(colors=_INK_2)
    ax.xaxis.label.set_color(_INK_2)
    ax.yaxis.label.set_color(_INK_2)
    ax.title.set_color(_INK)


def plot_timeseries(ts: pd.DataFrame, cfg: dict, path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    p_mw = ts["p_farm_W"] / 1e6
    daily = p_mw.resample("D").mean()
    installed = int(param(cfg, "wind_farm.n_turbines")) * float(param(cfg, "wind_turbine.rated_power")) / 1e6

    fig, ax = plt.subplots(figsize=(10, 4), facecolor=_SURFACE)
    ax.plot(p_mw.index, p_mw.values, color=_SERIES_LIGHT, linewidth=0.5, label="Hourly")
    ax.plot(daily.index + pd.Timedelta(hours=12), daily.values, color=_SERIES, linewidth=2, label="Daily mean")
    ax.axhline(installed, color=_INK_2, linewidth=1, linestyle="--")
    ax.text(p_mw.index[0], installed, f" installed {installed:.0f} MW", va="bottom", color=_INK_2, fontsize=9)
    ax.set_ylim(0, installed * 1.1)
    ax.set_xlim(p_mw.index[0], p_mw.index[-1])
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.set_ylabel("Net wind farm power [MW]")
    year = int(param(cfg, "era5.year"))
    ax.set_title(f"Wind farm power, {year} (ERA5, {param(cfg, 'site.latitude')}N {param(cfg, 'site.longitude')}E)", loc="left")
    _style_axes(ax)
    ax.legend(frameon=False, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, labelcolor=_INK_2)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, facecolor=_SURFACE)
    plt.close(fig)
    return path


def plot_duration_curve(ts: pd.DataFrame, cfg: dict, path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dc = duration_curve(ts["p_farm_W"]) / 1e6
    hours = np.arange(1, len(dc) + 1)
    s = summary(ts, cfg)

    fig, ax = plt.subplots(figsize=(7, 4.5), facecolor=_SURFACE)
    ax.plot(hours, dc, color=_SERIES, linewidth=2)
    ax.set_xlim(0, len(dc))
    ax.set_ylim(0, s["installed_capacity_MW"] * 1.1)
    ax.set_xlabel("Hours per year at or above this power [h]")
    ax.set_ylabel("Net wind farm power [MW]")
    ax.set_title("Wind farm power duration curve", loc="left")
    ax.text(
        0.98, 0.95,
        f"Capacity factor {s['net_capacity_factor']:.0%}\n"
        f"Mean {s['mean_farm_power_MW']:.1f} MW\n"
        f"Zero output {s['hours_zero_output']} h",
        transform=ax.transAxes, ha="right", va="top", color=_INK_2, fontsize=9,
    )
    _style_axes(ax)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, facecolor=_SURFACE)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# Command line entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="ERA5 -> wind farm power time series")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--no-download", action="store_true", help="only use ERA5 files already on disk")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    year = int(param(cfg, "era5.year"))
    lat, lon = float(param(cfg, "site.latitude")), float(param(cfg, "site.longitude"))
    raw_dir = repo_path(param(cfg, "era5.raw_dir"))

    if args.no_download:
        files = [era5_month_file(raw_dir, year, m, lat, lon) for m in range(1, 13)]
        missing = [f for f in files if not f.exists()]
        if missing:
            raise FileNotFoundError(f"{len(missing)} ERA5 month files missing, e.g. {missing[0]}")
    else:
        files = download_era5(cfg)

    ts = wind_farm_timeseries(load_era5(files), cfg)

    out_dir = repo_path(param(cfg, "outputs.processed_dir"))
    out_dir.mkdir(parents=True, exist_ok=True)
    csv = out_dir / f"wind_farm_power_{year}.csv"
    ts.to_csv(csv)

    fig_dir = repo_path(param(cfg, "outputs.figure_dir"))
    f1 = plot_timeseries(ts, cfg, fig_dir / f"wind_power_timeseries_{year}.png")
    f2 = plot_duration_curve(ts, cfg, fig_dir / f"wind_power_duration_curve_{year}.png")

    for k, v in summary(ts, cfg).items():
        print(f"{k:28s} {v:.3f}" if isinstance(v, float) else f"{k:28s} {v}")
    print(f"Saved {csv}\nSaved {f1}\nSaved {f2}")


if __name__ == "__main__":
    main()
