import numpy as np
import pandas as pd
import pytest

from topside import gas_turbine as gtm
from topside.config import load_config, param

CFG = load_config()
GT = gtm.GasTurbine.from_config(CFG, "large")


@pytest.fixture(scope="module")
def fuel():
    return gtm.fuel_gas_from_config(CFG)


def test_carbon_balance():
    assert gtm.carbon_per_mol({"methane": 1.0}) == 1.0
    assert gtm.carbon_per_mol({"methane": 0.5, "ethane": 0.5}) == pytest.approx(1.5)
    assert gtm.carbon_per_mol({"methane": 2, "CO2": 2}) == pytest.approx(1.0)  # normalised
    with pytest.raises(KeyError):
        gtm.carbon_per_mol({"unobtainium": 1.0})


def test_pure_methane_properties():
    cfg = load_config()
    cfg["fuel_gas"]["composition"]["value"] = {"methane": 1.0}
    f = gtm.fuel_gas_from_config(cfg)
    # ISO 6976 methane LHV at 15/15 degC is about 34.0 MJ/m3
    assert f.lhv_J_per_Sm3 == pytest.approx(34.0e6, rel=0.01)
    # Methane CO2 intensity about 55 kg/GJ
    assert f.co2_kg_per_J * 1e9 == pytest.approx(55, rel=0.02)


def test_fuel_gas_in_reported_range(fuel):
    lo, hi = param(CFG, "fuel_gas.reported_co2_factor_range")
    assert 0.9 * lo < fuel.co2_kg_per_Sm3 < 1.1 * hi
    assert 54 < fuel.co2_kg_per_J * 1e9 < 62


def test_willans_fit_recovers_parameter():
    load = np.array([1.0, 0.8, 0.6, 0.4, 0.2])
    ratio = gtm.willans_efficiency_ratio(load, 0.25)
    assert gtm.fit_no_load_fraction(load, ratio) == pytest.approx(0.25)


def test_config_value_matches_curve_fit():
    curve = gtm.load_part_load_curve(param(CFG, "gas_turbines.part_load_curve_file"))
    a = gtm.fit_no_load_fraction(curve["load_fraction"], curve["efficiency_ratio"])
    assert GT.no_load_fuel_fraction == pytest.approx(a, abs=0.005)
    model = gtm.willans_efficiency_ratio(curve["load_fraction"], a)
    assert np.allclose(model, curve["efficiency_ratio"], rtol=0.02)


def test_turbine_fuel_and_efficiency():
    p = np.array([0.3, 0.5, 0.8, 1.0]) * GT.rated_power_W
    assert GT.efficiency(GT.rated_power_W) == pytest.approx(GT.design_efficiency)
    fuel = GT.fuel_W(p)
    assert np.all(np.diff(fuel) > 0)                 # more power, more fuel
    assert np.all(np.diff(fuel / p) < 0)             # but less fuel per MWh
    assert GT.fuel_W(0.0) == 0.0                     # off
    c0, c1 = GT.milp_fuel_coefficients()
    assert np.allclose(fuel, c0 + c1 * p)
    with pytest.raises(ValueError):
        GT.fuel_W(1.1 * GT.rated_power_W)


def test_fuel_and_co2_series(fuel):
    idx = pd.date_range("2023-01-01", periods=3, freq="h")
    p = pd.Series([0.0, 15e6, 30e6], index=idx)
    out = gtm.fuel_and_co2(p, GT, fuel)
    assert out.index.equals(idx)
    assert out["co2_kg"].iloc[0] == 0
    assert out["co2_kg"].iloc[2] == pytest.approx(out["fuel_Sm3"].iloc[2] * fuel.co2_kg_per_Sm3)
    # Full load for one hour: fuel energy = 30 MW / eta * 3600 s
    assert out["fuel_J"].iloc[2] == pytest.approx(30e6 / GT.design_efficiency * 3600)


def test_plot(tmp_path, fuel):
    curve = gtm.load_part_load_curve(param(CFG, "gas_turbines.part_load_curve_file"))
    gts = [GT, gtm.GasTurbine.from_config(CFG, "small")]
    assert gtm.plot_part_load(gts, fuel, curve, tmp_path / "gt.png").stat().st_size > 0
