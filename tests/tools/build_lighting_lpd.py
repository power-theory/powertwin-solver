"""Build solver/upload/reference_data/comstock_lighting_lpd.json: the average installed building-level lighting power
density (W/ft2) by ComStock building type and lighting generation, transcribed from

  Van Sant, CaraDonna, Parker. End-Use Savings Shapes Measure Documentation: LED Lighting. NREL/TP-5500-86100,
  December 2023, Table 2 "Average Building-Level Lighting Power Densities (W/ft2) by Lighting Generation and
  Building Type". https://www.nrel.gov/docs/fy24osti/86100.pdf

Parsed from the report's text layer (pdftotext -layout); the PDF's sha256 is pinned so a different file is refused.
The resolver multiplies nothing: it picks a building's generation from ComStock's county distribution and reads its
LPD here, and the set_lighting_power_density measure scales the building's interior lighting to it.

  python3 tests/tools/build_lighting_lpd.py [--pdf PATH]
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, "solver", "upload", "reference_data", "comstock_lighting_lpd.json")
PDF = "/home/missionloyd/dev/flow-ssd/tmp_clsf/comstock_cache/nrel_86100_led_lighting.pdf"
PDF_SHA256 = "8571300be9139a57b75768d87cdcf942a7295de9419011f858f11f7526fd4e5c"
# Table 2's row names -> in.comstock_building_type (ComStock 2024.1 baseline.parquet)
COMSTOCK = {"full_service_restaurant": "FullServiceRestaurant", "hospital": "Hospital", "large_hotel": "LargeHotel",
            "large_office": "LargeOffice", "medium_office": "MediumOffice", "outpatient": "Outpatient",
            "primary_school": "PrimarySchool", "quick_service_restaurant": "QuickServiceRestaurant",
            "retail": "RetailStandalone", "secondary_school": "SecondarySchool", "small_hotel": "SmallHotel",
            "small_office": "SmallOffice", "strip_mall": "RetailStripmall", "warehouse": "Warehouse"}
GENS = ["gen1", "gen2", "gen3", "gen4", "gen5"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=PDF)
    a = ap.parse_args()
    sha = hashlib.sha256(open(a.pdf, "rb").read()).hexdigest()
    assert sha == PDF_SHA256, f"{a.pdf} sha256 {sha} is not the pinned NREL/TP-5500-86100"
    text = subprocess.run(["pdftotext", "-layout", a.pdf, "-"], capture_output=True, text=True, check=True).stdout
    i = text.rindex("Table 2. Average Building-Level Lighting Power Densities (W/ft2) by Lighting Generation and")  # past the list of tables
    rows = {}
    for line in text[i:i + 3000].splitlines():
        m = re.match(r"\s*([a-z_]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$", line)
        if m and m.group(1) in COMSTOCK:
            rows[COMSTOCK[m.group(1)]] = dict(zip(GENS, (float(x) for x in m.groups()[1:])))
    assert set(rows) == set(COMSTOCK.values()), f"Table 2 rows missing: {sorted(set(COMSTOCK.values()) - set(rows))}"
    doc = {
        "_source": {
            "name": "Van Sant, CaraDonna, Parker (NREL), End-Use Savings Shapes Measure Documentation: LED Lighting, "
                    "NREL/TP-5500-86100, December 2023, Table 2",
            "url": "https://www.nrel.gov/docs/fy24osti/86100.pdf",
            "pdf_sha256": sha,
            "units": "W/ft2, average installed building-level LPD",
            "generations": "ComStock in.interior_lighting_generation gen1_t12_incandescent, gen2_t8_halogen, "
                           "gen3_t5_cfl, gen4_led, gen5_led",
            "extracted": datetime.date.today().isoformat(),
            "regen": "tests/tools/build_lighting_lpd.py",
        },
        "lpd_w_per_ft2": rows,
    }
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {os.path.relpath(OUT, REPO)}: {len(rows)} building types x {len(GENS)} generations")
    print("SmallOffice", rows["SmallOffice"], "Warehouse", rows["Warehouse"])


if __name__ == "__main__":
    main()
