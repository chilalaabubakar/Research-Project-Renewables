# Project brief

Working title: Energy Demand Mapping and Flexible Operation of Offshore Topside
Systems under Variable Renewable Power Supply

## Aim

Model the power system of an offshore oil and gas platform supplied by an offshore
wind farm and a smaller gas turbine, and test whether flexible operation of topside
consumers reduces fuel gas use and CO2 emissions.

Research question: how should platform power demand be scheduled to follow variable
wind supply, minimising CO2 without large production losses?

Most published studies treat the platform load as fixed. Here the consumers that can
be turned down, deferred or switched off are identified and scheduled.

## Scope

- Base case: Snorre field (Tampen area, Norwegian North Sea).
- Comparison case: hypothetical offshore gas production platform, Tanzania.
- One year of hourly data.
- Steady-state operating points with ramp limits. No transient process simulation.
- Submission around 2026-12-18.

## Cases

1. Gas turbines only (baseline)
2. Wind + small gas turbine, fixed demand
3. Wind + small gas turbine, flexible demand
4. Case 3 + battery storage

Outputs: fuel gas use, CO2, lost production, gas turbine hours at low load.
Sensitivity on wind farm size, CO2 price, gas price and battery size.

## Data (all public)

- Wind: ERA5 hourly wind at 100 m (Copernicus CDS).
- Wind turbine: IEA 15 MW reference turbine (IEAWindTask37/IEA-15-240-RWT).
- Gas turbine: part-load efficiency from published literature.
- Validation: fuel use and CO2 from the Norwegian Environment Agency
  (norskeutslipp.no), production from the Norwegian Offshore Directorate (Sodir).

## Conventions

- All parameters live in `config/*.yaml` as value, unit and source, and are read
  with `topside.config.param()`. No numbers hard-coded in modules.
- In YAML write `15.0e+6`, not `15.0e6` (PyYAML reads the second as a string).
- SI units internally (W, kg, s, Sm3). Convert only for plots.
- Each module has pytest tests that run without internet access.
