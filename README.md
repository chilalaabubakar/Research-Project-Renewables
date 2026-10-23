# Flexible topside power with offshore wind

MSc project: Energy Demand Mapping and Flexible Operation of Offshore Topside Systems
under Variable Renewable Power Supply.

Model of an offshore platform power system with wind, gas turbines and flexible
consumers. See `docs/project_brief.md` for scope and `docs/implementation_plan.md`
for the plan.

## Installation

Requires Python 3.11+ and Java 11+ (for NeqSim, check with `java -version`).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

### ERA5 access

1. Register at https://cds.climate.copernicus.eu.
2. Accept the licence on the Download tab of "ERA5 hourly data on single levels from
   1940 to present".
3. Put the API key from your CDS profile in `~/.cdsapirc`:
   ```
   url: https://cds.climate.copernicus.eu/api
   key: <personal-access-token>
   ```

## Wind module

```bash
python -m topside.wind                # download if needed, compute, plot
python -m topside.wind --no-download  # use files already in data/raw/era5/
```

Outputs:
- `data/processed/wind_farm_power_<year>.csv` (hourly wind speed and power in W)
- `results/figures/wind_power_timeseries_<year>.png`
- `results/figures/wind_power_duration_curve_<year>.png`

Method:
1. ERA5 u and v components at 100 m and 10 m are downloaded month by month for the
   site grid point. Existing months are skipped.
2. Wind speed = sqrt(u² + v²), hourly, UTC.
3. Shear correction from 100 m to the 150 m hub height with the power law,
   alpha = 0.14 (IEC 61400-3-1). Alternatively alpha can be computed hourly from the
   ERA5 10 m and 100 m winds (`wind_shear.method: era5_profile`), clipped to the
   bounds in the config.
4. IEA-15-240-RWT power curve, linear interpolation, zero below 3 m/s and above
   25 m/s.
5. Farm power = turbine power × number of turbines × (1 − wake loss) ×
   (1 − electrical loss) × availability.

Simplifications: constant air density (1.225 kg/m³), same wind at all turbines, hard
cut-out. Farm size and loss factors in `config/base_case.yaml` are still placeholders.

## Gas turbine module

```bash
python -m topside.gas_turbine         # fuel gas properties, part-load figure
```

Fuel energy is linear in power with a no-load term (Willans line):
F = a·F_rated·on + (1 − a)·F_rated·P/P_rated, with F_rated = P_rated/η_design.
The no-load fraction a is fitted to a tabulated part-load curve
(`data/reference/gt_part_load_indicative.csv`). The same two coefficients are used in
the scheduler.

LHV and standard molar volume of the fuel gas come from NeqSim (ISO 6976), CO2 per
Sm3 from a carbon balance, all from the composition in the config. Turbine ratings,
efficiencies, minimum load and the fuel gas composition are placeholders until
Snorre-specific values are found.

## Layout

```
config/            parameters (value, unit, source)
data/reference/    small reference data under version control
data/raw/          downloads (not in git)
data/processed/    derived time series (not in git)
docs/              project brief and plan
notebooks/         checks and exploration
src/topside/       model code
tests/             pytest
results/figures/   figures
```
