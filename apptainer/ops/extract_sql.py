"""Monthly cooling and heating DELIVERED per building from each kept eplusout.sql:
cooling = sum over every 'Cooling Coil Total Cooling Rate' (W, hourly mean -> Wh) -> ton-hr;
heating = Heating:EnergyTransfer meter (J) -> MMBtu. EnergyPlus Time rows carry the month of
the hour's own day (hour 24 belongs to its day), so grouping by Time.Month is the local month.
    python extract_sql.py <urbanopt_simulation dir> <enduse_monthly.csv in> <out csv>"""
import csv, glob, os, sqlite3, sys, collections
root, src, out = sys.argv[1:4]
cool = collections.defaultdict(float); heat = collections.defaultdict(float); done = set()
for f in sorted(glob.glob(os.path.join(root, "batch_*", "*", "eplusout.sql"))):
    aid = f.split(os.sep)[-2]
    c = sqlite3.connect(f)
    rows = c.execute('select ReportVariableDataDictionaryIndex from ReportVariableDataDictionary where VariableName="Cooling Coil Total Cooling Rate" and ReportingFrequency="Hourly"').fetchall()
    if rows:
        idx = ",".join(str(r[0]) for r in rows)
        for m, v in c.execute(f"select t.Month, sum(d.VariableValue) from ReportVariableData d join Time t on d.TimeIndex=t.TimeIndex where d.ReportVariableDataDictionaryIndex in ({idx}) group by t.Month"):
            cool[(aid, int(m))] = v / 3517.0
    for m, v in c.execute('select t.Month, sum(d.VariableValue) from ReportMeterData d join ReportMeterDataDictionary md on d.ReportMeterDataDictionaryIndex=md.ReportMeterDataDictionaryIndex join Time t on d.TimeIndex=t.TimeIndex where md.VariableName="Heating:EnergyTransfer" and md.ReportingFrequency="Hourly" group by t.Month'):
        heat[(aid, int(m))] = v / 1.055056e9
    done.add(aid); c.close()
rows = list(csv.DictReader(open(src)))
for r in rows:
    k = (r["asset_id"], int(r["month"]))
    r["cooling_delivered_tonhr"] = round(cool.get(k, 0.0), 3)
    r["heating_delivered_mmbtu"] = round(heat.get(k, 0.0), 4)
with open(out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(f"{len(done)} sql files; cooling for {len({a for a,_ in cool})} buildings, heating for {len({a for a,_ in heat})} -> {out}")
