"""Extract weekday + weekend operating hours per DOE Reference Building
prototype from openstudio-standards' ashrae_90_1.schedules.json.

Source: /usr/local/lib/ruby/gems/3.2.2/gems/openstudio-standards-0.7.1/lib/
        openstudio-standards/standards/ashrae_90_1/data/ashrae_90_1.schedules.json
A flat list of {name, category, day_types, values[24]} records defining the
prototype building schedules used by openstudio-standards'
create_typical_building_from_model measure.

We pick each prototype's building-level occupancy schedule and derive an
operating window as the longest contiguous block of hours where the
fraction >= 0.5. The 0.5 threshold matches DOE's "occupied" convention.

day_types vocabulary observed: 'Default' (weekday Mon-Fri), 'Sat', 'Sun',
'Wkdy', 'Wknd', 'SmrDsn', 'WntrDsn', and compound forms like
'Default|Wknd|Hol' or 'Default|WntrDsn|SmrDsn' (the latter implies a
schedule that applies to all day types, ie 24/7 buildings like apartments).

Run inside the solver container:
    docker exec powertwin-solver-flask python3 \
        /solver/upload/measures/_tools/regen_operating_schedules.py
    docker cp powertwin-solver-flask:/tmp/proto_operating_schedules.json \
        solver/upload/reference_data/openstudio_standards_operating_schedules.json
"""

import json
from pathlib import Path

SRC = Path(
    '/usr/local/lib/ruby/gems/3.2.2/gems/openstudio-standards-0.7.1/'
    'lib/openstudio-standards/standards/ashrae_90_1/data/ashrae_90_1.schedules.json'
)
OUT = Path('/tmp/proto_operating_schedules.json')
THRESHOLD = 0.5

# Map an openstudio-standards schedule name to a canonical DOE Reference
# Building name used in our resolver (matches `_doe_ref_name` callers in
# sim_params_spec.py). Order matters: first match wins for prototypes with
# multiple variants. Each canonical name maps to ONE schedule.
SCHEDULE_MAP = [
    # (schedule_name, canonical_building_type)
    ('OfficeMedium BLDG_OCC_SCH',         'Office'),
    ('RetailStandalone BLDG_OCC_SCH',     'Retail'),
    ('StripMall Bldg Occ',                'StripMall'),
    ('SchoolPrimary BLDG_OCC_SCH',        'PrimarySchool'),
    ('SchoolSecondary BLDG_OCC_SCH',      'SecondarySchool'),
    ('College BLDG_OCC_SCH',              'College'),
    ('Hospital BLDG_OCC_SCH',             'Hospital'),
    ('OutPatientHealthCare BLDG_OCC_SCH', 'Outpatient'),
    ('SmallHotel Bldg Occ',               'SmallHotel'),
    ('HotelLarge BLDG_OCC_SCH',           'LargeHotel'),
    ('Warehouse BLDG_OCC_SCH',            'Warehouse'),
    ('RestaurantSitDown BLDG_OCC_SCH',    'FullServiceRestaurant'),
    ('RestaurantFastFood BLDG_OCC_SCH',   'QuickServiceRestaurant'),
    ('MidriseApartment Apartment Occ',    'MidriseApartment'),
    ('ApartmentHighRise OCC_APT_SCH',     'HighriseApartment'),
    ('SuperMarket Bldg Occ',              'SuperMarket'),
    ('College BLDG_Lab_OCC_SCH',          'Laboratory'),  # closest standalone lab schedule
]


def operating_window(values):
    """Longest contiguous run where value >= 50% of the day's peak fraction,
    returned as (start_hr, duration_hr). Peak-relative threshold lets us
    handle prototypes like Laboratory whose absolute peak is 0.25 the same
    way we handle Office whose peak is 0.95. All-1 returns (0, 24).
    All-0 returns (None, None)."""
    if not values or len(values) != 24:
        return (None, None)
    floats = [float(v) for v in values]
    peak = max(floats)
    if peak <= 0:
        return (None, None)
    cutoff = peak * THRESHOLD
    mask = [1 if v >= cutoff else 0 for v in floats]
    if all(m == 1 for m in mask):
        return (0, 24)
    if all(m == 0 for m in mask):
        return (None, None)
    best_start = 0
    best_len = 0
    i = 0
    while i < 24:
        if mask[i] == 0:
            i += 1
            continue
        start = i
        while i < 24 and mask[i] == 1:
            i += 1
        run = i - start
        if run > best_len:
            best_len = run
            best_start = start
    return (best_start, best_len)


def fmt(hr):
    if hr is None:
        return ''
    return f'{int(hr):02d}:00'


def tags(day_types_str):
    return day_types_str.split('|') if day_types_str else []


# Three observed conventions in ashrae_90_1.schedules.json:
#   (A) Wkdy + Sat + Default (off-hours fallback). Office, Hospital, Retail.
#   (B) Default + Sat + Sun. Default IS weekday. SmallHotel, StripMall.
#   (C) Default-only (occasionally 'Default|WntrDsn|SmrDsn'). 24/7 case.
#       Apartments, MidriseApartment.
# Heuristic: a 'Wkdy' rule marks convention A; otherwise Default is the
# weekday. For weekend, only an explicit Sun/Sat/Wknd row counts unless
# the schedule has no weekday-specific rule at all (convention C).
WEEKDAY_PRIORITY = ['Wkdy']
WEEKEND_PRIORITY = ['Sun', 'Sat', 'Wknd']


def find_day_row(rows_for_name, tag):
    for r in rows_for_name:
        if tag in tags(r.get('day_types', '')):
            return r.get('values', [])
    return None


def find_values(rows_for_name, target):
    priority = WEEKDAY_PRIORITY if target == 'weekday' else WEEKEND_PRIORITY
    for want in priority:
        vals = find_day_row(rows_for_name, want)
        if vals is not None:
            return vals
    has_wkdy = find_day_row(rows_for_name, 'Wkdy') is not None
    if target == 'weekday':
        # Default is the weekday under convention (B); 24/7 schedule under (C).
        return find_day_row(rows_for_name, 'Default')
    # Weekend: Default is the off-hours fallback under (A), so don't use it
    # there. Only fall back when convention (C) (no Wkdy anywhere) applies.
    if not has_wkdy:
        return find_day_row(rows_for_name, 'Default')
    return None


def main():
    rows = json.loads(SRC.read_text())['schedules']
    # Index by name -> list of rows (one per day_types). When schedules repeat
    # across ASHRAE template years (2004, 2007, 2010, 2013), the values for a
    # given (name, day_types) pair are identical for our purpose; last wins.
    by_name = {}
    for r in rows:
        by_name.setdefault(r.get('name', ''), []).append(r)

    out = {}
    missing = []
    for sched_name, canonical in SCHEDULE_MAP:
        if canonical in out:
            continue
        recs = by_name.get(sched_name, [])
        if not recs:
            missing.append((sched_name, canonical))
            continue
        wkdy = find_values(recs, 'weekday')
        wknd = find_values(recs, 'sun') or find_values(recs, 'sat')
        if not wkdy:
            missing.append((sched_name, canonical))
            continue
        wkdy_start, wkdy_dur = operating_window(wkdy)
        wknd_start, wknd_dur = operating_window(wknd) if wknd else (wkdy_start, wkdy_dur)
        if wkdy_start is None:
            # Unoccupied weekday: should not happen for building-level
            # occupancy. Skip with a note rather than emit blank.
            missing.append((sched_name, canonical))
            continue
        out[canonical] = {
            'weekday_start_time': fmt(wkdy_start),
            'weekday_duration':   fmt(wkdy_dur),
            'weekend_start_time': fmt(wknd_start),
            'weekend_duration':   fmt(wknd_dur),
            'source_schedule':    sched_name,
        }

    wrapped = {
        '_source': {
            'name': 'DOE Reference Building operating hours from openstudio-standards 0.7.1 BLDG_OCC_SCH schedules',
            'publisher': 'National Renewable Energy Laboratory',
            'url': 'https://github.com/NREL/openstudio-standards/blob/master/lib/openstudio-standards/standards/ashrae_90_1/data/ashrae_90_1.schedules.json',
            'extracted': '2026-06-15',
            'methodology': (
                'Per-building-type weekday/weekend operating window derived from '
                'each prototype building level occupancy schedule (BLDG_OCC_SCH or '
                'equivalent). Window = longest contiguous run of hours where the '
                'occupancy fraction stays >= 50% of the day peak. Peak-relative '
                'threshold handles low-density prototypes (Laboratory peak 0.25) '
                'the same way as high-density (Office peak 0.95). Schedule '
                'rule-resolution follows EnergyPlus Schedule:Ruleset semantics: '
                'Wkdy rule beats Default for weekdays, Sun/Sat/Wknd rule beats '
                'Default for weekends, Default-only rows imply 24/7 schedules.'
            ),
            'units': 'HH:MM 24-hour clock for start_time; HH:MM duration for duration. Empty string = no occupied window in the prototype (e.g. weekend for K-12 schools).',
            'consumed_by': 'powertwin-solver sim_params_spec.py _resolve_operating_schedule, exposed via POST /api/simulation/resolve-defaults as building_type_only provenance for the 4 time-typed sim params.',
        },
        'operating_hours_by_building_type': out,
    }
    OUT.write_text(json.dumps(wrapped, indent=2, sort_keys=True))
    print(f'wrote {OUT} ({len(out)} building types)')
    for bt in sorted(out):
        r = out[bt]
        print(f"  {bt:<24} wkdy {r['weekday_start_time']}+{r['weekday_duration']}  "
              f"wknd {r['weekend_start_time']}+{r['weekend_duration']}  ({r['source_schedule']})")
    if missing:
        print(f'\nmissing ({len(missing)}):')
        for sched, bt in missing:
            print(f'  {bt:<24} {sched}')


if __name__ == '__main__':
    main()
