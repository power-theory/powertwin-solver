"""Monthly end uses per building from the kept URBANopt default_feature_reports.csv files.

Timestamps are EnergyPlus end-of-hour local standard time (the EPW's zone); the 1-second
shift moves each label to its hour's start so the last hour of a month stays in that
month (the same translation consolidate_sensor_logs.py applies). Cooling delivered comes
from Cooling Coil Total Cooling Rate (W, hourly mean) -> Wh -> ton-hr (3517 Wh per ton-hr).

    python extract_enduse.py <user_files/<sim>/urbanopt_simulation> <out.csv>
"""
import csv, glob, os, sys, collections
root, out = sys.argv[1], sys.argv[2]
COLS = {
    "elec_kwh": "Electricity:Facility",
    "cooling_elec_kwh": "Cooling:Electricity",
    "heatrej_elec_kwh": "HeatRejection:Electricity",
    "heating_elec_kwh": "Heating:Electricity",
    "fans_elec_kwh": "Fans:Electricity",
    "pumps_elec_kwh": "Pumps:Electricity",
    "gas_kbtu": "NaturalGas:Facility",
    "heating_gas_kbtu": "Heating:NaturalGas",
    "cooling_rate_w": "Cooling Coil Total Cooling Rate",
}
rows = []
files = sorted(glob.glob(os.path.join(root, "batch_*", "*", "*default_feature_reports", "default_feature_reports.csv")))
for f in files:
    aid = f.split(os.sep)[-3]
    with open(f) as fh:
        r = csv.reader(fh)
        header = next(r)
        idx = {}
        for key, prefix in COLS.items():
            for i, h in enumerate(header):
                if h.split("(")[0] == prefix:
                    idx[key] = i
                    break
        dt_i = header.index("Datetime")
        acc = collections.defaultdict(lambda: collections.defaultdict(float))
        n = collections.Counter()
        for line in r:
            ts = line[dt_i]                       # 'YYYY/MM/DD HH:MM:SS', end of hour
            day, hms = ts.split(" ")
            y, m, d = day.split("/")
            hh = int(hms[:2])
            if hh == 0 and hms[3:5] == "00":      # 00:00 end-of-hour belongs to the previous day
                import datetime
                prev = datetime.date(int(y), int(m), int(d)) - datetime.timedelta(days=1)
                m = f"{prev.month:02d}"
            month = int(m)
            n[month] += 1
            for key, i in idx.items():
                v = line[i]
                acc[month][key] += float(v) if v not in ("", "NA") else 0.0
        for month in sorted(acc):
            a = acc[month]
            rows.append({"asset_id": aid, "month": month, "hours": n[month],
                         "elec_kwh": round(a["elec_kwh"], 3), "cooling_elec_kwh": round(a["cooling_elec_kwh"], 3),
                         "heatrej_elec_kwh": round(a["heatrej_elec_kwh"], 3), "heating_elec_kwh": round(a["heating_elec_kwh"], 3),
                         "fans_elec_kwh": round(a["fans_elec_kwh"], 3), "pumps_elec_kwh": round(a["pumps_elec_kwh"], 3),
                         "gas_kbtu": round(a["gas_kbtu"], 3), "heating_gas_kbtu": round(a["heating_gas_kbtu"], 3),
                         "cooling_delivered_tonhr": round(a["cooling_rate_w"] / 3517.0, 3)})
with open(out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"{len(files)} buildings, {len(rows)} building-months -> {out}")
