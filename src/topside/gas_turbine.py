"""Gas turbine module: electric load -> fuel gas -> CO2.

Fuel model
----------
Fuel energy flow is linear in power with a no-load term (Willans line):

    F(P) = a * F_rated * on + (1 - a) * F_rated * P / P_rated,   F_rated = P_rated / eta_design

so efficiency falls at part load as  eta / eta_design = L / (a + (1 - a) * L),  L = P / P_rated.
The same two coefficients are used directly in the MILP scheduler (no piecewise linearisation).

Fuel gas
--------
LHV (ISO 6976, via NeqSim), standard molar volume and CO2 per Sm3 (carbon balance) are all
computed from one composition in the config, so they are consistent with each other.

Run ``python -m topside.gas_turbine`` to print fuel properties and plot efficiency and
specific CO2 against load.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from topside.config import DEFAULT_CONFIG, load_config, param, repo_path

M_CO2 = 44.0095e-3  # kg/mol
R = 8.314462618  # J/(mol K)

# Carbon atoms per molecule, for the CO2 balance (complete combustion)
CARBON_ATOMS = {
    "nitrogen": 0, "CO2": 1, "H2S": 0, "water": 0, "oxygen": 0, "helium": 0, "argon": 0,
    "methane": 1, "ethane": 2, "propane": 3, "i-butane": 4, "n-butane": 4,
    "i-pentane": 5, "n-pentane": 5, "n-hexane": 6, "n-heptane": 7, "n-octane": 8,
}


# --------------------------------------------------------------------------
# Fuel gas properties
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FuelGas:
    lhv_J_per_Sm3: float
    mol_per_Sm3: float
    carbon_per_mol: float

    @property
    def co2_kg_per_Sm3(self) -> float:
        return self.carbon_per_mol * self.mol_per_Sm3 * M_CO2

    @property
    def co2_kg_per_J(self) -> float:
        return self.co2_kg_per_Sm3 / self.lhv_J_per_Sm3


def carbon_per_mol(composition: dict[str, float]) -> float:
    """Moles of carbon per mole of gas. Composition is normalised first."""
    unknown = set(composition) - set(CARBON_ATOMS)
    if unknown:
        raise KeyError(f"No carbon count for components: {sorted(unknown)}")
    total = sum(composition.values())
    return sum(x * CARBON_ATOMS[c] for c, x in composition.items()) / total


def fuel_gas_from_config(cfg: dict) -> FuelGas:
    """LHV and standard molar volume from NeqSim (ISO 6976, real gas), CO2 from carbon balance."""
    from neqsim import jneqsim

    comp = dict(param(cfg, "fuel_gas.composition"))
    t_ref = float(param(cfg, "fuel_gas.reference_temperature"))
    p_ref = float(param(cfg, "fuel_gas.reference_pressure"))

    fluid = jneqsim.thermo.system.SystemSrkEos(t_ref, p_ref / 1e5)
    for name, x in comp.items():
        fluid.addComponent(name, float(x))
    fluid.setMixingRule("classic")
    fluid.init(0)  # required before ISO 6976, otherwise it returns zeros
    t_c = t_ref - 273.15
    iso = jneqsim.standards.gasquality.Standard_ISO6976(fluid, t_c, t_c, "volume")
    iso.setReferencePressure(p_ref / 1e3)
    iso.calculate()
    lhv = float(iso.getValue("InferiorCalorificValue")) * 1e3  # kJ/m3 -> J/Sm3
    z = float(iso.getValue("CompressionFactor"))
    if lhv <= 0:
        raise RuntimeError("ISO 6976 calculation returned no heating value")
    return FuelGas(lhv_J_per_Sm3=lhv, mol_per_Sm3=p_ref / (z * R * t_ref), carbon_per_mol=carbon_per_mol(comp))


# --------------------------------------------------------------------------
# Part-load curve
# --------------------------------------------------------------------------


def willans_efficiency_ratio(load_fraction, a: float):
    """eta / eta_design for a linear fuel curve with no-load fuel fraction a."""
    load = np.asarray(load_fraction, dtype=float)
    return load / (a + (1 - a) * load)


def fit_no_load_fraction(load_fraction, efficiency_ratio) -> float:
    """Least-squares a in  F/F_rated = a + (1 - a) L  from a tabulated part-load curve."""
    load = np.asarray(load_fraction, dtype=float)
    fuel_ratio = load / np.asarray(efficiency_ratio, dtype=float)
    w = 1 - load
    return float(np.sum((fuel_ratio - load) * w) / np.sum(w * w))


def load_part_load_curve(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(repo_path(path), comment="#")


# --------------------------------------------------------------------------
# Gas turbine
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GasTurbine:
    name: str
    rated_power_W: float
    design_efficiency: float
    no_load_fuel_fraction: float
    min_load_fraction: float
    max_ramp_fraction_per_hour: float

    @classmethod
    def from_config(cls, cfg: dict, key: str) -> "GasTurbine":
        base = f"gas_turbines.{key}"
        return cls(
            name=cfg["gas_turbines"][key].get("name", key),
            rated_power_W=float(param(cfg, f"{base}.rated_power")),
            design_efficiency=float(param(cfg, f"{base}.design_efficiency")),
            no_load_fuel_fraction=float(param(cfg, f"{base}.no_load_fuel_fraction")),
            min_load_fraction=float(param(cfg, f"{base}.min_load_fraction")),
            max_ramp_fraction_per_hour=float(param(cfg, f"{base}.max_ramp_fraction_per_hour")),
        )

    @property
    def rated_fuel_W(self) -> float:
        return self.rated_power_W / self.design_efficiency

    @property
    def min_power_W(self) -> float:
        return self.min_load_fraction * self.rated_power_W

    def milp_fuel_coefficients(self) -> tuple[float, float]:
        """(no-load fuel [W], incremental fuel per W of power [-]) for F = c0 * on + c1 * P."""
        a = self.no_load_fuel_fraction
        return a * self.rated_fuel_W, (1 - a) * self.rated_fuel_W / self.rated_power_W

    def fuel_W(self, power_W, on=None):
        """Fuel energy flow [W] at electric power [W]. Off (on = 0 or P = 0) means zero fuel."""
        p = np.asarray(power_W, dtype=float)
        if np.any(p < -1e-6) or np.any(p > self.rated_power_W * (1 + 1e-9)):
            raise ValueError(f"{self.name}: power outside 0..rated")
        on = p > 0 if on is None else np.asarray(on, dtype=bool)
        c0, c1 = self.milp_fuel_coefficients()
        return np.where(on, c0 + c1 * p, 0.0)

    def efficiency(self, power_W):
        p = np.asarray(power_W, dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(p > 0, p / self.fuel_W(p), 0.0)


def fuel_and_co2(power_W, turbine: GasTurbine, fuel: FuelGas, dt_s: float = 3600.0, on=None) -> pd.DataFrame:
    """Fuel volume [Sm3] and CO2 [kg] per time step for a power series [W]."""
    energy_J = turbine.fuel_W(power_W, on) * dt_s
    out = pd.DataFrame({"fuel_J": energy_J, "fuel_Sm3": energy_J / fuel.lhv_J_per_Sm3})
    out["co2_kg"] = out["fuel_Sm3"] * fuel.co2_kg_per_Sm3
    if isinstance(power_W, pd.Series):
        out.index = power_W.index
    return out


# --------------------------------------------------------------------------
# Figure and command line
# --------------------------------------------------------------------------


def plot_part_load(turbines: list[GasTurbine], fuel: FuelGas, curve: pd.DataFrame, path: Path) -> Path:
    from topside.plotting import INK_2, SERIES, SURFACE, style_axes, use_agg

    use_agg()
    import matplotlib.pyplot as plt

    load = np.linspace(0.1, 1.0, 200)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2), facecolor=SURFACE)

    for gt, color in zip(turbines, SERIES):
        p = load * gt.rated_power_W
        eta = gt.efficiency(p)
        co2_kg_per_MWh = fuel.co2_kg_per_J * gt.fuel_W(p) / p * 3.6e9
        below = load < gt.min_load_fraction
        label = f"{gt.name}, {gt.rated_power_W / 1e6:.0f} MW"
        for ax, y in ((ax1, eta * 100), (ax2, co2_kg_per_MWh)):
            ax.plot(load[~below] * 100, y[~below], color=color, linewidth=2, label=label)
            ax.plot(load[below] * 100, y[below], color=color, linewidth=2, linestyle=":")

    gt0 = turbines[0]
    ax1.plot(curve["load_fraction"] * 100, curve["efficiency_ratio"] * gt0.design_efficiency * 100,
             "o", color=INK_2, markersize=5, label="Indicative curve (scaled to large GT)")
    ax1.set_ylabel("Efficiency, LHV [%]")
    ax1.set_title("Efficiency vs load", loc="left")
    ax2.set_ylabel("Specific CO2 [kg/MWh]")
    ax2.set_title("Specific CO2 vs load", loc="left")
    for ax in (ax1, ax2):
        ax.set_xlabel("Load [% of rated]")
        ax.set_xlim(0, 100)
        style_axes(ax)
    ax1.set_ylim(0, None)
    ax2.set_ylim(0, None)
    ax1.legend(frameon=False, fontsize=8, labelcolor=INK_2, loc="lower right")
    fig.text(0.01, 0.01, "Dotted: below minimum stable load", color=INK_2, fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Gas turbine part-load, fuel and CO2")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args(argv)
    cfg = load_config(args.config)

    fuel = fuel_gas_from_config(cfg)
    lo, hi = param(cfg, "fuel_gas.reported_co2_factor_range")
    print(f"Fuel gas LHV            {fuel.lhv_J_per_Sm3 / 1e6:.2f} MJ/Sm3")
    print(f"CO2 factor              {fuel.co2_kg_per_Sm3:.3f} kg/Sm3 (reported NCS range {lo}-{hi})")
    print(f"CO2 per energy          {fuel.co2_kg_per_J * 1e9:.1f} kg/GJ")

    curve = load_part_load_curve(param(cfg, "gas_turbines.part_load_curve_file"))
    a_fit = fit_no_load_fraction(curve["load_fraction"], curve["efficiency_ratio"])
    print(f"No-load fuel fraction fitted to part-load curve: {a_fit:.3f}")

    turbines = [GasTurbine.from_config(cfg, k) for k in ("large", "small")]
    for gt in turbines:
        c0, c1 = gt.milp_fuel_coefficients()
        print(f"{gt.name}: rated {gt.rated_power_W / 1e6:.1f} MW, eta {gt.design_efficiency:.1%}, "
              f"eta at min load {gt.efficiency(gt.min_power_W):.1%}, no-load fuel {c0 / 1e6:.1f} MW, slope {c1:.3f}")

    fig = plot_part_load(turbines, fuel, curve, repo_path(param(cfg, "outputs.figure_dir")) / "gas_turbine_part_load.png")
    print(f"Saved {fig}")


if __name__ == "__main__":
    main()
