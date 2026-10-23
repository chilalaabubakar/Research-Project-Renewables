"""Tests for the wind module. No network: ERA5 files are faked with xarray."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from topside import wind
from topside.config import load_config, param

CFG = load_config()
PC = wind.load_power_curve(param(CFG, "wind_turbine.power_curve_file"))
RATED = param(CFG, "wind_turbine.rated_power")


def tp(ws):
    return wind.turbine_power(ws, PC, cut_in=3.0, cut_out=25.0, rated=RATED)


def test_power_curve_loaded_in_watts():
    assert PC["power"].max() == pytest.approx(15e6, rel=1e-4)
    assert PC["wind_speed"].is_monotonic_increasing


def test_turbine_power_regions():
    p = tp([0.0, 2.9, 3.0, 8.0, 10.66, 15.0, 25.0, 25.1, 30.0])
    assert p[0] == 0 and p[1] == 0          # below cut-in
    assert 0 < p[2] < 0.1e6                  # just at cut-in
    assert 5e6 < p[3] < 8e6                  # partial load
    assert p[4] == pytest.approx(RATED, rel=1e-3)
    assert p[5] == pytest.approx(RATED, rel=1e-3)
    assert p[6] == pytest.approx(RATED, rel=1e-3)
    assert p[7] == 0 and p[8] == 0          # above cut-out
    assert np.all(p <= RATED)


def test_power_law_and_profile_shear():
    # 1/7 law: going from 100 m to 150 m with alpha 0.14 gives ~5.8 % more wind
    assert wind.power_law(10.0, 100.0, 150.0, 0.14) == pytest.approx(10 * 1.5**0.14)
    # Recover a known exponent from a 10 m / 100 m pair
    alpha = wind.shear_exponent_from_profile(5.0, 5.0 * 10**0.2)
    assert alpha == pytest.approx(0.2)
    # Clipping
    assert wind.shear_exponent_from_profile(1.0, 100.0, bounds=(0, 0.4)) == 0.4


def test_farm_power_losses():
    p = wind.farm_power(np.array([10e6]), n_turbines=4, wake_loss=0.05, electrical_loss=0.02, availability=0.95)
    assert p[0] == pytest.approx(10e6 * 4 * 0.95 * 0.98 * 0.95)


def test_duration_curve_sorted_descending():
    dc = wind.duration_curve([1, 5, 3, 0])
    assert list(dc) == [5, 3, 1, 0]


def _fake_era5_file(path, start, hours, ws=9.0):
    t = pd.date_range(start, periods=hours, freq="h")
    shape = (hours, 1, 1)
    u = np.full(shape, ws * 0.6)
    v = np.full(shape, ws * 0.8)
    ds = xr.Dataset(
        {name: (("valid_time", "latitude", "longitude"), arr) for name, arr in
         {"u100": u, "v100": v, "u10": u * 0.8, "v10": v * 0.8}.items()},
        coords={"valid_time": t, "latitude": [61.0], "longitude": [2.0]},
    )
    ds.to_netcdf(path)
    return path


def test_load_era5_and_full_chain(tmp_path):
    f1 = _fake_era5_file(tmp_path / "a.nc", "2023-01-01", 48)
    f2 = _fake_era5_file(tmp_path / "b.nc", "2023-01-03", 24, ws=30.0)
    df = wind.load_era5([f1, f2])
    assert len(df) == 72 and df.index.freq is None
    assert df["ws100"].iloc[0] == pytest.approx(9.0)

    ts = wind.wind_farm_timeseries(df, CFG)
    assert (ts["p_farm_W"].iloc[48:] == 0).all()      # 30 m/s storm -> cut-out
    assert (ts["p_farm_W"].iloc[:48] > 0).all()
    s = wind.summary(ts, CFG)
    assert s["hours"] == 72 and s["hours_above_cut_out"] == 24


def test_load_era5_detects_gap(tmp_path):
    f1 = _fake_era5_file(tmp_path / "a.nc", "2023-01-01", 24)
    f2 = _fake_era5_file(tmp_path / "b.nc", "2023-01-03", 24)  # 2 Jan missing
    with pytest.raises(ValueError, match="missing hours"):
        wind.load_era5([f1, f2])


def test_plots_written(tmp_path):
    f = _fake_era5_file(tmp_path / "a.nc", "2023-01-01", 24 * 10)
    ts = wind.wind_farm_timeseries(wind.load_era5([f]), CFG)
    assert wind.plot_timeseries(ts, CFG, tmp_path / "ts.png").stat().st_size > 0
    assert wind.plot_duration_curve(ts, CFG, tmp_path / "dc.png").stat().st_size > 0
