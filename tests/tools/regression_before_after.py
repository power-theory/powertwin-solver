"""Before/after regression test for Q/A audit fixes.
Exercises the changed code paths using the ASU demo dataset.

Run inside the container:
  python3 /tmp/regression_before_after.py
"""
import csv
import json
import math
import os
import sys
import tempfile
import traceback

sys.path.insert(0, '/solver/app')

DEMO_DIR = '/solver/upload/demo_data'
GEOJSON = os.path.join(DEMO_DIR, 'asu_asset_geometries.geojson')
METADATA_CSV = os.path.join(DEMO_DIR, 'asu_metadata.csv')

results = {}


def test_sensor_id_matching():
    """Verify sensor ID matching logic in clean_report.py.
    Old code used substring ('in'), new uses equality ('!=').
    """
    print("\n=== TEST 1: Sensor ID Matching ===")

    from modules.simulation.clean_report import clean_single_report
    import inspect
    src = inspect.getsource(clean_single_report)

    # Detect which logic is in play
    if 'not in str(' in src or "not in str(" in src:
        logic = "SUBSTRING (old/buggy)"
    elif '!= str(' in src or "!= str(" in src:
        logic = "EQUALITY (new/fixed)"
    else:
        logic = "UNKNOWN"
    print(f"  Active logic: {logic}")

    # Simulate both on ASU metadata
    with open(METADATA_CSV, 'r') as f:
        rows = list(csv.DictReader(f))

    all_ids = set()
    for row in rows:
        try:
            props = json.loads(row['asset_geometries_properties'])
            all_ids.add(str(props.get('id', '')))
        except (json.JSONDecodeError, KeyError):
            pass

    substring_collisions = 0
    affected_buildings = []
    for target in sorted(all_ids):
        old_matches = set()
        for row in rows:
            try:
                props = json.loads(row['asset_geometries_properties'])
                rid = str(props.get('id', ''))
            except (json.JSONDecodeError, KeyError):
                continue
            if target in rid:
                old_matches.add(rid)
        if len(old_matches) > 1:
            substring_collisions += 1
            affected_buildings.append(target)

    print(f"  Total unique building IDs: {len(all_ids)}")
    print(f"  Buildings with substring collisions (old bug): {substring_collisions}")
    if affected_buildings[:5]:
        print(f"  Examples: {affected_buildings[:5]}")

    results['sensor_id'] = {
        'logic': logic,
        'total_ids': len(all_ids),
        'collisions': substring_collisions,
    }


def test_geometry_functions():
    """Test shape factor computation and MultiPolygon handling."""
    print("\n=== TEST 2: Geometry (shape factor / MultiPolygon) ===")

    try:
        from modules.simulation.generateFeatureFile import _ring_area_unsigned, _ring_perimeter
        has_geo_funcs = True
    except ImportError:
        has_geo_funcs = False
        print("  _ring_area_unsigned / _ring_perimeter NOT FOUND (old code)")

    from modules.simulation.generateFeatureFile import flatten_geometry
    import copy

    with open(GEOJSON) as f:
        gj = json.load(f)

    features = gj.get('features', [])
    poly_count = multi_count = 0
    shape_factors = []

    for feat in features:
        geom = feat.get('geometry', {})
        gt = geom.get('type', '')
        if gt == 'MultiPolygon':
            multi_count += 1
        elif gt == 'Polygon':
            poly_count += 1

        if has_geo_funcs and gt == 'Polygon':
            outer = (geom.get('coordinates') or [None])[0]
            if outer and len(outer) >= 4:
                area = _ring_area_unsigned(outer)
                perim = _ring_perimeter(outer)
                if area > 0:
                    sq_perim = 4 * math.sqrt(area)
                    shape_factors.append(perim / sq_perim)

    print(f"  Polygon: {poly_count}, MultiPolygon: {multi_count}")

    if has_geo_funcs and shape_factors:
        print(f"  Shape factors: min={min(shape_factors):.3f}, "
              f"max={max(shape_factors):.3f}, "
              f"mean={sum(shape_factors)/len(shape_factors):.3f}")
        non_sq = sum(1 for sf in shape_factors if sf > 1.05)
        print(f"  Non-square buildings (SF > 1.05): {non_sq}/{len(shape_factors)}")
    elif not has_geo_funcs:
        print("  Shape factor: N/A (old code uses square assumption)")

    # Test MultiPolygon handling
    for feat in features:
        geom = feat.get('geometry', {})
        if geom.get('type') == 'MultiPolygon':
            test_geom = copy.deepcopy(geom)
            n_polys = len(test_geom['coordinates'])
            flatten_geometry(test_geom)
            new_type = test_geom['type']
            new_rings = len(test_geom['coordinates'])
            print(f"  MultiPolygon flatten: {n_polys} polys → {new_type} with {new_rings} rings")
            break

    results['geometry'] = {
        'has_shape_factor': has_geo_funcs,
        'polygons': poly_count,
        'multipolygons': multi_count,
        'shape_factors': {
            'min': round(min(shape_factors), 3) if shape_factors else None,
            'max': round(max(shape_factors), 3) if shape_factors else None,
            'mean': round(sum(shape_factors)/len(shape_factors), 3) if shape_factors else None,
            'non_square_count': sum(1 for sf in shape_factors if sf > 1.05) if shape_factors else 0,
        }
    }


def test_metadata_read():
    """Test read_metadata function: counters and skip logging."""
    print("\n=== TEST 3: read_metadata() ===")

    from modules.simulation.generateFeatureFile import read_metadata
    import inspect
    src = inspect.getsource(read_metadata)

    has_counters = 'skip_counts' in src or 'missing_area' in src
    print(f"  Has skip counters: {has_counters}")

    # Actually call it
    result = read_metadata(METADATA_CSV)

    # Old code returns 7 dicts; new code returns (7 dicts + row_count) or same 7.
    if isinstance(result, tuple):
        lookup = result[0]
        total_rows = result[-1] if isinstance(result[-1], int) else None
        print(f"  Returns tuple of {len(result)} items, first dict: {len(lookup)} entries"
              f"{f', total_rows={total_rows}' if total_rows else ''}")
    else:
        lookup = result
        total_rows = None
        print(f"  Returns single value: {len(lookup)} entries")

    results['metadata'] = {
        'has_counters': has_counters,
        'lookup_size': len(lookup),
        'total_rows': total_rows,
    }


def test_residential_units():
    """Test residential unit count logic."""
    print("\n=== TEST 4: Residential Unit Counts ===")

    from modules.simulation.generateFeatureFile import process_feature
    import inspect
    src = inspect.getsource(process_feature)

    has_meta_units = 'number_of_units' in src or 'meta_units' in src
    has_sqft_const = 'SQFT_PER_BEDROOM' in src
    print(f"  Uses metadata unit count: {has_meta_units}")
    print(f"  Uses SQFT_PER_BEDROOM constant: {has_sqft_const}")

    results['residential'] = {
        'metadata_units': has_meta_units,
        'sqft_const': has_sqft_const,
    }


def test_sim_params():
    """Test sim_params_spec constants."""
    print("\n=== TEST 5: sim_params_spec Constants ===")

    try:
        from modules.simulation.sim_params_spec import SQFT_PER_BEDROOM
        print(f"  SQFT_PER_BEDROOM: {SQFT_PER_BEDROOM}")
        results['sim_params'] = {'SQFT_PER_BEDROOM': SQFT_PER_BEDROOM}
    except ImportError:
        print("  SQFT_PER_BEDROOM: NOT EXPORTED (old code uses hardcoded 600)")
        # Check what the old code uses
        from modules.simulation.sim_params_spec import _resolve_occupants
        import inspect
        src = inspect.getsource(_resolve_occupants)
        if '/ 600' in src:
            val = 600
        elif '/ 800' in src:
            val = 800
        else:
            val = 'unknown'
        print(f"  Inferred bedroom density divisor: {val}")
        results['sim_params'] = {'SQFT_PER_BEDROOM': val, 'exported': False}


def test_feature_generation_e2e():
    """End-to-end: generate feature files for first N buildings and capture key fields."""
    print("\n=== TEST 6: Feature File Generation (E2E sample) ===")

    from modules.simulation.generateFeatureFile import (
        read_metadata, process_feature
    )

    with open(GEOJSON) as f:
        gj = json.load(f)
    features = gj.get('features', [])

    md_result = read_metadata(METADATA_CSV)
    # read_metadata returns 7 dicts as a tuple
    if isinstance(md_result, tuple) and len(md_result) >= 7:
        (building_area, building_type_list, building_name_list,
         building_weather_list, building_climate_zone_list,
         building_year_list, building_metadata_list) = md_result[:7]
    else:
        print("  Unexpected read_metadata return format")
        results['features'] = {'error': 'unexpected return format'}
        return

    sample = {}
    count = 0
    for feat in features:
        props = feat.get('properties', {})
        bid = str(props.get('id', ''))
        if bid not in building_area:
            continue

        try:
            result = process_feature(
                feat, building_area, building_type_list,
                building_name_list, building_weather_list,
                building_climate_zone_list, building_year_list,
                building_metadata_list
            )
            if result is None:
                continue
            # process_feature returns (json_dict, building_id, name) or just a dict
            if isinstance(result, tuple):
                feature_json = result[0]
            else:
                feature_json = result
            # Properties are nested under features[0].properties
            feat_list = feature_json.get('features', [])
            rprops = feat_list[0].get('properties', {}) if feat_list else feature_json.get('properties', {})
            key_fields = {}
            for k in ['floor_area', 'number_of_stories', 'footprint_area',
                       'window_to_wall_ratio', 'floor_height',
                       'number_of_occupants', 'building_type',
                       'number_of_residential_units', 'number_of_bedrooms']:
                v = rprops.get(k)
                if v is not None:
                    key_fields[k] = v
            # Window area from nested windows list
            windows = rprops.get('windows', [])
            if windows and isinstance(windows, list):
                key_fields['window_area'] = windows[0].get('window_area')
            # Wall/roof R-values from constructions
            constructions = rprops.get('constructions', {})
            if constructions:
                wall = constructions.get('wall', {})
                if wall.get('r_value') is not None:
                    key_fields['wall_r_value'] = wall['r_value']
                roof = constructions.get('roof', {})
                if roof.get('r_value') is not None:
                    key_fields['roof_r_value'] = roof['r_value']
            sample[bid] = key_fields
            count += 1
            if count >= 10:
                break
        except Exception as e:
            print(f"  Building {bid}: {e}")

    print(f"  Generated {count} feature files")
    for bid, fields in sorted(sample.items())[:5]:
        print(f"  Building {bid}:")
        for k, v in sorted(fields.items()):
            print(f"    {k}: {v}")

    results['features'] = sample


def main():
    print("=" * 70)
    print("PowerTwin Solver - Regression Test")
    print("=" * 70)

    tests = [
        test_sensor_id_matching,
        test_geometry_functions,
        test_metadata_read,
        test_residential_units,
        test_sim_params,
        test_feature_generation_e2e,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"  ERROR: {e}")
            traceback.print_exc()
            results[t.__name__] = {'error': str(e)}

    out = '/tmp/regression_results.json'
    with open(out, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out}")
    print("=" * 70)


if __name__ == '__main__':
    main()
