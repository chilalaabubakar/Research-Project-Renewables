# Implementation plan

Start 2026-10-23, submission around 2026-12-18 (8 weeks).
Base case Snorre, comparison case hypothetical gas platform offshore Tanzania.

## Scope priorities

| Priority | Items |
|---|---|
| Must | ERA5 wind series (2023), gas turbine part-load model, Snorre demand map, flexibility classification, MILP scheduler, Cases 1-4, sensitivity on wind farm size and CO2 price, Case 1 validation against reported Snorre data |
| Should | Tanzania case, sensitivity on battery size, gas price and reserve rule, compressor charts instead of a minimum-flow rule |
| Could | Several wind years, ERA5 bias check against station data, forecast error, NeqSim for liquids, CH4/NOx, air density correction |

Consumer power:
- Gas side in NeqSim: separator split at each stage pressure and all gas compression
  (recompression, export, injection). At injection pressure the hand formula was
  10-40 % off (see decisions log).
- Pumps (water injection, seawater lift, oil export): P = Q·Δp/η.
- Utilities: typical values from literature.
- No full flowsheet.

Methods text is written each week for the module finished that week.

## Model structure

```
ERA5 -> wind.py -> available wind power ----------------------+
Snorre profile -> process.py -> P(flow) per consumer          |
consumers.py -> class and flexibility limits -----------------+--> scheduler.py (Pyomo MILP)
gas_turbine.py -> fuel(P), min load, CO2 ---------------------+        |
                                                                      v
                                              cases.py: Cases 1-4, sensitivity
                                                                      |
                                    fuel, CO2, lost production, GT low-load hours,
                                    curtailment, GT starts -> validation, figures
```

Data exchange between modules: hourly pandas DataFrames, UTC index, SI units, unit
suffix in column names.

| Module | Columns | Unit |
|---|---|---|
| wind.py | p_farm_W | W |
| process.py / consumers.py | p_<consumer>_W | W |
| scheduler.py | p_gt<i>_W, on_gt<i>, p_wind_used_W, p_curtail_W, p_<consumer>_W, p_bat_ch_W, p_bat_dis_W, soc_J | W, 0/1, J |
| gas_turbine.py | fuel_gt<i>_Sm3, co2_kg | Sm3, kg |

Each results folder stores a copy of the config used.

## Schedule

### Week 1 (Oct 23-29): wind, gas turbine, Snorre data

- [x] Repo, config, wind module, tests
- [ ] Download ERA5 2023 (needs CDS key)
- [ ] Move the ERA5 point to the Snorre location (Sodir factpages, nearest 0.25° grid point)
- [ ] Check mean wind speed and capacity factor against published North Sea values
- [x] GitHub Actions running pytest
- [x] gas_turbine.py (values for the Snorre units still placeholders)
  - [ ] Turbine types: Snorre setup if public (Equinor, Sodir, Snorre Expansion PDO), otherwise a typical ~30 MW aeroderivative for Case 1 and a smaller unit for Cases 2-4
  - [ ] Part-load efficiency curve from literature, stored in data/reference/ (indicative curve in place, needs OEM or peer-reviewed replacement)
  - [x] Fuel = P/(η·LHV); linear with no-load term (Willans line), same form in the MILP
  - [x] Minimum stable load, ramp rate in config (placeholders)
  - [x] CO2 factor and LHV from fuel gas composition (ISO 6976 via NeqSim, carbon balance)
  - [ ] Snorre fuel gas composition or reported CO2 factor
  - [x] Tests; plot of efficiency and specific CO2 vs load
- [ ] Snorre data: production and injection (Sodir), CO2 and fuel gas (norskeutslipp.no), Hywind Tampen figures (Equinor)
- [ ] Write methods: wind, gas turbine

### Week 2 (Oct 30-Nov 5): Snorre demand map

- [ ] Platform definition in config: oil, gas and water rates, gas and water injection (WAG), export, pressures
- [ ] Reservoir fluid for NeqSim (published Snorre-like composition or generic North Sea oil); choice of EoS (SRK/PR, GERG-2008 for dry gas)
- [ ] process.py, gas side (NeqSim)
  - [ ] Separation train, gas/oil/water rates per stage
  - [ ] Compressor trains (recompression, export, injection): stage pressures, intercooling, polytropic efficiency from config
  - [ ] Throughput sweep 40-110 % of design -> shaft power vs flow
  - [ ] Turndown: below minimum flow (surge margin) power stays at the minimum-flow value (anti-surge recycle)
- [ ] process.py, liquids and utilities
  - [ ] Water injection and seawater lift pumps, oil export pumps
  - [ ] Utilities, HVAC, living quarters, heating
  - [ ] Drilling as a separate scenario or excluded (state reason)
- [ ] Fit P(flow) per consumer and save tables to data/processed/ (scheduler does not call NeqSim)
- [ ] Tests: NeqSim vs hand calculation at low pressure, power increases with flow and pressure ratio, turndown floor
- [ ] Check total demand against Hywind Tampen share-of-demand figures
- [ ] Plot: stacked demand by consumer
- [ ] Write methods: platform and demand mapping

### Week 3 (Nov 6-12): flexibility and scheduler

- [ ] consumers.py, classes stored in config:

| Class | Description | Snorre examples | Model constraints |
|---|---|---|---|
| Must-run | fixed | safety, control, living quarters, metering | P = P_ref |
| Deferrable | reduced now, made up later | water injection, gas injection | power range, ramp, cumulative volume within a window |
| Curtailable | reduced with production loss | export compression, oil export | power range, ramp, lost production as function of power reduction |
| Sheddable | switched off for limited time | electric heating, desalination, non-critical HVAC | on/off, max hours off per day |

- [ ] Deferred and lost production reported separately
- [ ] Flexibility level parameter (low/medium/high)
- [ ] scheduler.py (Pyomo + HiGHS)
  - [ ] Variables: GT on/off and power, wind used and curtailed, consumer levels
  - [ ] Objective: fuel cost + CO2 cost + lost production + start penalty
  - [ ] Constraints: power balance, wind availability, GT min/max load and piecewise fuel, GT ramp, spinning reserve (rule set in config), consumer limits
  - [ ] Rolling horizon: 7-day window, 1-day step, perfect foresight
- [ ] Small test cases (3-6 h): no wind, excess wind, load shift, reserve forcing a GT on
- [ ] Write: flexibility classification, optimisation model

### Week 4 (Nov 13-19): battery and Cases 1-4

- [ ] Battery: SOC, efficiencies, power and energy limits
- [ ] cases.py: Case 1 (GT only, N+1), Case 2 (wind + small GT, fixed demand), Case 3 (flexible demand), Case 4 (Case 3 + battery)
- [ ] Results per case: fuel, CO2, lost and deferred production, GT low-load hours, GT starts, curtailment, wind share
- [ ] Consistency tests: Case 3 without flexibility = Case 2; Case 4 with zero battery = Case 3
- [ ] Solve time; reduce binaries or window length if needed

### Week 5 (Nov 20-26): sensitivity and validation

- [ ] Sweep runner (YAML list of runs, one folder per run, restartable)
- [ ] Must: wind farm size (0 to about 2× demand), CO2 price
- [ ] Should: battery size, gas price, reserve rule, flexibility level
- [ ] Case 1 fuel and CO2 vs norskeutslipp.no, scaled by production; discuss differences (flaring, drilling, other sources)
- [ ] Wind case vs published Hywind Tampen expectations
- [ ] Write: validation

### Week 6 (Nov 27-Dec 3): Tanzania

- [ ] ERA5 point at the deepwater gas blocks (coordinates from public licence maps)
- [ ] config/tanzania_gas.yaml: gas platform without oil export or water injection; export compression, TEG dehydration, produced water, utilities
- [ ] Cases 1-4 and farm size sweep, comparison with Snorre
- [ ] Note in text: planned Tanzanian developments are subsea to shore (LNG), so the platform is hypothetical
- [ ] Reduced version if behind: wind comparison and Cases 1 and 3 only

### Week 7 (Dec 4-10): figures and results

Figures (generated from saved results):
1. Snorre demand by consumer and class
2. Wind power time series and duration curve, Snorre and Tanzania
3. GT efficiency and specific CO2 vs load
4. Example week, Cases 2-4
5. Annual CO2 and fuel per case
6. GT load distribution per case
7. CO2 reduction vs wind farm size
8. CO2 reduction vs lost production for different CO2 prices

- [ ] Write: results and discussion

### Week 8 (Dec 11-18): finishing

- [ ] Introduction, literature review, conclusions, abstract
- [ ] Tag code version used for the thesis results
- [ ] README instructions to reproduce figures
- [ ] Supervisor feedback, buffer

## Git workflow

- One branch per module, merged to main when tests pass
- pytest on every push, no network in tests
- Downloaded data not committed; data/reference/ only small files with sources
- Tag the version used for thesis results

## Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| ERA5 access delayed | Weeks 4-6 blocked | Priority in week 1; scheduler developed on small test inputs meanwhile |
| NeqSim API issues | Week 2 delayed | Installation verified; start from notebooks/neqsim_vs_hand_compressor.py; NeqSim results checked against hand calculations |
| Incomplete Snorre data | Uncertain demand map | Scale to reported annual totals, typical NCS values with sources, stated assumptions |
| Flexibility limits hard to justify | Main result questioned | Literature and operating practice where available; flexibility level in sensitivity |
| Reserve rule drives results | Conclusions depend on one assumption | Configurable rule, at least two rules compared |
| MILP too slow for a full year | No sensitivity runs | Rolling horizon, fewer binaries |
| Perfect foresight | Benefit overestimated | Stated as limitation |
| Delays | Incomplete thesis | Priorities above, week 8 buffer, reduced Tanzania case |

## Open questions

1. Exact submission date; thesis only or also a paper?
2. Spinning reserve rule for the base case (check with supervisor).

## Decisions log

| Date | Decision | Reason |
|---|---|---|
| 2026-10-23 | ERA5 base year 2023 | Latest complete non-leap year (8760 h) |
| 2026-10-23 | Power law shear, alpha = 0.14 (IEC 61400-3-1); hourly ERA5 profile as option | Standard approach; option for stability effects |
| 2026-10-23 | Wind farm 4 × 15 MW, losses 5 % wake, 2 % electrical, 95 % availability | Placeholders until demand is known |
| 2026-10-23 | 8-week timeline, submission about 2026-12-18 | Deadline |
| 2026-10-23 | Base case Snorre | Tampen area, supplied by Hywind Tampen, public emission and production data |
| 2026-10-23 | Tanzania case as hypothetical gas platform | Offshore discoveries in Tanzania are gas |
| 2026-10-23 | NeqSim for gas side, hand formulas for pumps, literature values for utilities | Single-stage test (10 kg/s associated gas, η_p = 0.75, SRK): hand formula vs NeqSim +2 % at 3→12 bar, +9 % at 30→150 bar, +27 % at 150→400 bar with NeqSim k, M and Z. Gas injection is a large Snorre consumer. Liquids nearly incompressible |
| 2026-10-23 | NeqSim as core dependency (Java required) | Needed from week 2 |
| 2026-10-23 | Compressor turndown as minimum-flow power floor | Simple; compressor charts are a Should item |
| 2026-10-23 | GT fuel model: linear fuel vs power with no-load fraction a (Willans line) | Fits the indicative part-load curve within 1 % from 20 to 100 % load with a = 0.21; maps directly to the MILP without piecewise segments |
| 2026-10-23 | Fuel gas LHV, Sm3 and CO2 factor computed from composition (ISO 6976 via NeqSim, carbon balance) | Keeps the three consistent. CO2 per GJ varies little with composition, so CO2 results depend mainly on fuel energy. Placeholder composition gives 2.38 kg/Sm3; reported NCS factors 2.34 (old average) to 2.67-2.69 (Edvard Grieg, Yme 2024) |
| 2026-10-23 | GT ratings 30 MW (Case 1) and 13 MW (Cases 2-4), design efficiency 36 % and 34 %, minimum load 30 % | Placeholders until Snorre turbine data is found; calibrated later against Case 1 validation |
