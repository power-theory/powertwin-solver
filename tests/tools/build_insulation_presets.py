"""Build solver/upload/reference_data/insulation_presets.json: the insulation R-value presets the defaults preview
offers per building (Insulation Presets Plan, 2026-09-29).

Residential: IECC prescriptive minimums by climate zone, transcribed from the code text (fetched 2026-09-29):
  - IECC 2009 Table 402.1.1 (https://up.codes/viewer/pennsylvania/iecc-2009/chapter/4/residential-energy-efficiency)
  - IECC 2021 Table R402.1.3 (https://up.codes/viewer/connecticut/iecc-2021/chapter/RE_4/re-residential-energy-efficiency)
  Ceiling R is the house's roof_r_value (ceiling insulation); the wall value is the cavity-only option of each entry,
  matching the solver's cavity-R wall_r_value (PowerTwinRefs.wall_assembly_r derates it for HPXML).

Commercial: ASHRAE 90.1-2007 and 90.1-2019 from openstudio-standards 0.7.1, the library that builds the prototypes
(the image's gem, lib/openstudio-standards/standards/ashrae_90_1). For each DOE prototype its construction set gives
the wall and roof construction types; construction_properties gives the maximum assembly U by climate zone; the
openstudio-ee IncreaseInsulation measures set the construction's one insulation layer, so
    insulation R = 1 / U_max - R(the construction's other layers) - R(air films)
with the films of openstudio-standards Constructions.film_coefficients_r_value (90.1-2010 A9.4.1): exterior wall
0.17 + 0.68, exterior roof 0.17 + 0.61 (ft2-F-h/Btu), since every such U includes both films.

    python tests/tools/build_insulation_presets.py <dir with the openstudio-standards 0.7.1 data files>
"""
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, 'solver', 'upload', 'reference_data', 'insulation_presets.json')

# IECC residential minimums by climate zone number; 'M4' is Marine 4, which both editions group with zone 5
IECC_2009 = {'ceiling': {1: 30, 2: 30, 3: 30, 4: 38, 5: 38, 'M4': 38, 6: 49, 7: 49, 8: 49},
             'wall_cavity': {1: 13, 2: 13, 3: 13, 4: 13, 5: 20, 'M4': 20, 6: 20, 7: 21, 8: 21}}
IECC_2021 = {'ceiling': {0: 30, 1: 30, 2: 49, 3: 49, 4: 60, 5: 60, 'M4': 60, 6: 60, 7: 60, 8: 60},
             'wall_cavity': {0: 13, 1: 13, 2: 13, 3: 20, 4: 30, 5: 30, 'M4': 30, 6: 30, 7: 30, 8: 30}}
FILM = {'ExteriorWall': 0.17 + 0.68, 'ExteriorRoof': 0.17 + 0.61}
EDITIONS = ('2007', '2019')
USED_FILES = ({'ashrae_90_1.materials.json', 'ashrae_90_1.constructions.json'}
              | {f'ashrae_90_1_{e}.construction_properties.json' for e in EDITIONS})   # completed below with the templates


def _rows(path):
    return list(json.load(open(path)).values())[0]


def _layer_r(m):
    if m.get('resistance') not in (None, ''):
        return float(m['resistance'])
    return float(m['thickness']) / float(m['conductivity'])


TEMPLATES = {'DOE Ref Pre-1980': 'doe_ref_pre_1980', 'DOE Ref 1980-2004': 'doe_ref_1980_2004', '90.1-2004': 'ashrae_90_1_2004',
             '90.1-2007': 'ashrae_90_1_2007', '90.1-2010': 'ashrae_90_1_2010', '90.1-2013': 'ashrae_90_1_2013'}
USED_FILES |= ({f'{stem}.construction_sets.json' for stem in TEMPLATES.values()}
               | {f'{stem}.construction_properties.json' for stem in TEMPLATES.values()})


def _insulation_r(props, other_r):
    """{'<surface>|<construction type>|<category>': {zone: insulation-layer R}} meeting each row's maximum U."""
    out = {}
    for r in props:
        surf = r['intended_surface_type']
        if surf not in FILM or r.get('assembly_maximum_u_value') in (None, '') or not str(r.get('climate_zone_set', '')).startswith('ClimateZone '):
            continue
        assert r['u_value_includes_interior_film_coefficient'] and r['u_value_includes_exterior_film_coefficient'], r
        zone = r['climate_zone_set'].split()[1]
        key = f"{'wall' if surf == 'ExteriorWall' else 'roof'}|{r['standards_construction_type']}|{r['building_category']}"
        # a U looser than the bare construction needs no insulation at all (warm zones): 0, not negative
        out.setdefault(key, {})[zone] = max(0.0, round(1.0 / float(r['assembly_maximum_u_value']) - other_r(r['construction']) - FILM[surf], 2))
    return dict(sorted(out.items()))


def build(std_dir):
    """Commercial: which construction a building is built with depends on its template (PowerTwin.rb
    lookup_template_by_year_built) and the construction set of its lookup name (openstudio-standards
    model_get_lookup_name: every office prototype is 'Office'); create_typical asks model_add_construction_set for
    that set with no space type, and hotels are built residential. The code presets then give, per edition, the
    insulation-layer R that meets 90.1's maximum U for that construction type and category."""
    files = sorted(f for f in os.listdir(std_dir) if f in USED_FILES)
    missing = sorted(USED_FILES - set(files))
    assert not missing, f'missing standards files: {missing}'
    materials = {m['name']: m for m in _rows(os.path.join(std_dir, 'ashrae_90_1.materials.json'))}
    constructions = {c['name']: c for c in _rows(os.path.join(std_dir, 'ashrae_90_1.constructions.json'))}

    def other_r(name):
        c = constructions[name]
        return sum(_layer_r(materials[l]) for l in c['materials'] if l != c['insulation_layer'])

    sets, residential_sets, template_r = {}, {}, {}
    for label, stem in TEMPLATES.items():
        per, res = {}, set()
        for s in _rows(os.path.join(std_dir, f'{stem}.construction_sets.json')):
            bt = s.get('building_type')
            if not bt or bt == 'Any' or s.get('space_type') not in (None, '') or s.get('exterior_wall_standards_construction_type') in (None, ''):
                continue
            row = {'wall': [s['exterior_wall_standards_construction_type'], s['exterior_wall_building_category']],
                   'roof': [s['exterior_roof_standards_construction_type'], s['exterior_roof_building_category']]}
            if s.get('is_residential') == 'Yes':
                res.add(bt)           # create_typical asks residential first for hotels; used only when it exists
                per.setdefault(bt, row)
                continue
            per[bt] = row
        sets[label] = dict(sorted(per.items()))
        residential_sets[label] = sorted(res)
        # the insulation layer the template itself builds: the openstudio-ee measures skip a construction whose
        # insulation layer is R-1 or less ("does not appear to have an insulation layer"), so no preset applies there
        template_r[label] = _insulation_r(_rows(os.path.join(std_dir, f'{stem}.construction_properties.json')), other_r)
    insulation = {f'90.1-{ed}': _insulation_r(_rows(os.path.join(std_dir, f'ashrae_90_1_{ed}.construction_properties.json')), other_r)
                  for ed in EDITIONS}
    digest = {f: hashlib.md5(open(os.path.join(std_dir, f), 'rb').read()).hexdigest() for f in files}
    return {
        '_source': {
            'residential': 'IECC 2009 Table 402.1.1 and IECC 2021 Table R402.1.3 (code text via up.codes, fetched 2026-09-29); '
                           'ceiling and cavity-only wood-frame wall minimums',
            'commercial': 'openstudio-standards 0.7.1 (construction_sets of the six templates PowerTwin.rb selects by year built; '
                          '90.1-2007 / 90.1-2019 construction_properties, constructions, materials); '
                          'insulation R = 1/U_max - other layers - films (0.85 wall, 0.78 roof)',
            'standards_files_md5': digest,
            'builder': 'tests/tools/build_insulation_presets.py',
        },
        'residential': {'IECC 2009': {k: {str(z): v for z, v in t.items()} for k, t in IECC_2009.items()},
                        'IECC 2021': {k: {str(z): v for z, v in t.items()} for k, t in IECC_2021.items()}},
        'commercial': {'construction_sets': sets, 'residential_sets': residential_sets, 'template_insulation_r': template_r,
                       'insulation_r': insulation},
    }


if __name__ == '__main__':
    data = build(sys.argv[1])
    with open(OUT, 'w') as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write('\n')
    print('wrote', OUT, {t: len(v) for t, v in data['commercial']['construction_sets'].items()})
