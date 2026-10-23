# Why NeqSim for the gas side: one compressor stage, same gas, NeqSim (SRK EOS) vs hand
# polytropic formula. Illustrative composition and pressures, NOT Snorre data.
# Result logged in docs/implementation_plan.md (Decisions log, 2026-10-23).
# Run: python notebooks/neqsim_vs_hand_compressor.py
from neqsim import jneqsim

comp = {"nitrogen": 0.01, "CO2": 0.02, "methane": 0.80, "ethane": 0.09,
        "propane": 0.05, "i-butane": 0.01, "n-butane": 0.015, "n-pentane": 0.005}
eta_p, m, T_in, R = 0.75, 10.0, 303.15, 8.314

def fluid(p):
    f = jneqsim.thermo.system.SystemSrkEos(T_in, p)
    for c, x in comp.items():
        f.addComponent(c, x)
    f.setMixingRule("classic")
    return f

def neqsim_power(p1, p2):
    s = jneqsim.process.equipment.stream.Stream("in", fluid(p1)); s.setFlowRate(m, "kg/sec"); s.run()
    c = jneqsim.process.equipment.compressor.Compressor("c", s)
    c.setOutletPressure(p2); c.setUsePolytropicCalc(True); c.setPolytropicEfficiency(eta_p); c.run()
    g_in = s.getFluid().getPhase(0); g_out = c.getOutletStream().getFluid().getPhase(0)
    return c.getPower(), g_in, g_out

def hand_power(p1, p2, k, M, z=1.0):
    n_exp = (k - 1) / (k * eta_p)                              # (n-1)/n for polytropic path
    head = z * R / M * T_in / n_exp * ((p2 / p1) ** n_exp - 1)  # polytropic head, J/kg
    return m * head / eta_p                                    # shaft power, W

print(f"{'stage':28s} {'NeqSim':>7s} {'A: guessed k,M,Z=1':>19s} {'B: NeqSim k,M,Z=1':>18s} {'C: + avg Z':>11s}  Z in/out")
for label, p1, p2 in [("Recompression   3 ->  12 bar", 3, 12),
                      ("Export         30 -> 150 bar", 30, 150),
                      ("Injection     150 -> 400 bar", 150, 400)]:
    pn, gi, go = neqsim_power(p1, p2)
    k, M, z1, z2 = gi.getGamma(), gi.getMolarMass(), gi.getZ(), go.getZ()
    a = hand_power(p1, p2, 1.3, 0.0198)
    b = hand_power(p1, p2, k, M)
    c = hand_power(p1, p2, k, M, (z1 + z2) / 2)
    pct = lambda x: f"{x/1e6:5.2f} ({100*(x-pn)/pn:+3.0f}%)"
    print(f"{label:28s} {pn/1e6:6.2f}  {pct(a):>19s} {pct(b):>18s} {pct(c):>11s}  {z1:.2f}/{z2:.2f}  k_in={k:.2f}")
