"""solver/patches/patch_openstudio_standards.py: every fix lands once in each gem copy, a re-run changes
nothing, and a gem whose code moved fails loudly instead of being skipped."""
import importlib.util
import json
import os
import sys

import pytest

_P = os.path.join(os.path.dirname(__file__), '..', 'solver', 'patches', 'patch_openstudio_standards.py')
_spec = importlib.util.spec_from_file_location('patch_openstudio_standards', _P)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def _gem(root, version='0.7.1', moved=False):
    lib = os.path.join(root, 'gems', f'openstudio-standards-{version}', 'lib', 'openstudio-standards')
    for rel, edits in P.PATCHES:
        path = os.path.join(lib, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = 'before\n' + ''.join(bad + 'middle\n' for bad, _ in edits) + 'after\n'
        if moved:
            body = body.replace('122.0', '121.0').replace('#{wknd_op_hrs_start}', '#{x}')
        open(path, 'w').write(body)
    for rel, _edit in P.JSON_PATCHES['openstudio-standards']:     # 90.1 space-type data (2026-10-01)
        path = os.path.join(lib, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = [{'template': 't', 'building_type': 'SecondarySchool', 'space_type': 'Gym',
                 'heating_setpoint_schedule': 'H', 'cooling_setpoint_schedule': 'C'},
                {'template': 't', 'building_type': 'SecondarySchool', 'space_type': 'Classroom'}]
        json.dump({'space_types': rows}, open(path, 'w'))
    _geojson(root)
    _articulation(root)
    _main_file(root)
    _common_measures(root)
    return lib


def _main_file(root, version='0.7.1'):
    path = os.path.join(root, 'gems', f'openstudio-standards-{version}', 'lib', 'openstudio-standards.rb')
    open(path, 'w').write("require 'singleton'\n" + P._ORDER_SHIM_ANCHOR + "module OpenstudioStandards\nend\n")
    return path


def _common_measures(root, version='0.11.1'):
    path = os.path.join(root, 'gems', f'openstudio-common-measures-{version}', 'lib', 'measures',
                        'add_ems_emissions_reporting', 'measure.rb')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').write('    building = model.getBuilding\n' + P._EMS_AREA + '    ems_prgm = 1\n')
    return path


def _articulation(root, version='0.11.1'):
    """openstudio-model-articulation's story blend measure, iterating spaces in handle order (2026-10-07)."""
    path = os.path.join(root, 'gems', f'openstudio-model-articulation-{version}', 'lib', 'measures',
                        'blended_space_type_from_model', 'measure.rb')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').write('before\n' + P._TYPE_SPACE_TYPES + 'middle\n' + P._STORY_SPACES + 'after\n')
    return path


def _geojson(root, version='1.0.1', moved=False):
    """urbanopt-geojson's building.rb with the fixed zoning floor-to-floor line (2026-09-30)."""
    path = os.path.join(root, 'gems', f'urbanopt-geojson-{version}', 'lib', 'urbanopt', 'geojson', 'building.rb')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = P._FLOOR_FIXED.replace('3.6', '3.5') if moved else P._FLOOR_FIXED
    open(path, 'w').write('if zoning\n  floor_to_floor_height = other_building[:properties][:floor_to_floor_height]\nend\n' + line + 'after\n')
    zp = os.path.join(root, 'gems', f'urbanopt-geojson-{version}', 'lib', 'urbanopt', 'geojson', 'zoning.rb')
    open(zp, 'w').write('before\n' + P._ZONING_CORE + 'middle\n' + P._ZONING_RETURN + 'after\n')    # divide_floor_print
    for m in ('urban_geometry_creation_zoning', 'urban_geometry_creation'):     # the vacant-PV hack (2026-10-01)
        mp = os.path.join(root, 'gems', f'urbanopt-geojson-{version}', 'lib', 'measures', m, 'measure.rb')
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        open(mp, 'w').write("      building_type = feature.building_type\n" + P._VACANT_PV + "\n      # make other buildings\n")
    return path


def test_patches_once_and_is_idempotent(tmp_path, capsys):
    lib = _gem(str(tmp_path))
    P.main([str(tmp_path)])
    hvac = open(os.path.join(lib, P.PATCHES[1][0])).read()
    assert hvac.count('powertwin patch') == 2
    assert hvac.count("unless hot_water_loop.nil? || dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] < hw_temp_c") == 2
    assert '#{wknd_op_hrs_start_time}' in open(os.path.join(lib, P.PATCHES[0][0])).read()
    snapshot = {rel: open(os.path.join(lib, rel)).read() for rel, _ in P.PATCHES}
    capsys.readouterr()
    P.main([str(tmp_path)])
    assert capsys.readouterr().out.count('already patched') == sum(len(files) for _lib, files in P.GEM_PATCHES.values()) + sum(len(v) for v in P.JSON_PATCHES.values())  # every patched file
    assert snapshot == {rel: open(os.path.join(lib, rel)).read() for rel, _ in P.PATCHES}


def test_cap_keeps_upstream_value_on_hot_loops():
    """The injected rule only fires when the loop cannot supply the 122 F air: boiler loops keep it."""
    _, fix = P.PATCHES[1][1][0]
    assert "= 122.0\n" in fix and fix.index('= 122.0') < fix.index('powertwin patch')
    assert "standard_design_sizing_temperatures['zn_htg_dsgn_sup_air_temp_c']" in fix


def test_moved_code_fails_loudly(tmp_path):
    _gem(str(tmp_path), moved=True)
    with pytest.raises(SystemExit):
        P.main([str(tmp_path)])


def test_no_gem_fails(tmp_path):
    with pytest.raises(SystemExit):
        P.main([str(tmp_path)])


def test_writes_are_atomic_and_keep_the_mode(tmp_path):
    lib = _gem(str(tmp_path))
    path = os.path.join(lib, P.PATCHES[1][0])
    os.chmod(path, 0o640)
    P.main([str(tmp_path)])
    assert oct(os.stat(path).st_mode & 0o777) == '0o640'
    assert not [f for f in os.listdir(os.path.dirname(path)) if f.endswith('.tmp')]


def test_parallel_patchers_on_one_gem_home_agree(tmp_path):
    """Cluster batches patch one shared gem home at once: every file ends patched exactly once."""
    import subprocess as sp
    lib = _gem(str(tmp_path))
    procs = [sp.Popen([sys.executable, _P, str(tmp_path)], stdout=sp.DEVNULL, stderr=sp.PIPE) for _ in range(8)]
    assert all(p.wait() == 0 for p in procs)
    hvac = open(os.path.join(lib, P.PATCHES[1][0])).read()
    assert hvac.count('powertwin patch') == 2


def test_cluster_batches_patch_the_jobs_gem_home(tmp_path, monkeypatch):
    """ensure_standards_patched (direct_runner batch and recovery start) patches GEM_HOME / BUNDLE_PATH itself,
    so a campaign launched from a job script that predates the patch is still covered."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'solver', 'app'))
    os.environ.setdefault('POWERTWIN_LOG_DIR', '/tmp')
    from modules.simulation import initialize_UOsim as U
    monkeypatch.delenv('BUNDLE_PATH', raising=False)
    monkeypatch.delenv('GEM_HOME', raising=False)
    assert U.ensure_standards_patched() is False
    lib = _gem(str(tmp_path / 'shared_gems_1'))
    monkeypatch.setenv('GEM_HOME', str(tmp_path / 'shared_gems_1'))
    monkeypatch.setenv('BUNDLE_PATH', str(tmp_path / 'shared_gems_1'))
    assert U.ensure_standards_patched() is True
    assert '#{wknd_op_hrs_start_time}' in open(os.path.join(lib, P.PATCHES[0][0])).read()
    _gem(str(tmp_path / 'moved'), moved=True)
    monkeypatch.setenv('GEM_HOME', str(tmp_path / 'moved'))
    monkeypatch.setenv('BUNDLE_PATH', str(tmp_path / 'moved'))
    with pytest.raises(RuntimeError, match='patch failed'):
        U.ensure_standards_patched()


def test_zoning_honours_the_features_story_height(tmp_path, capsys):
    """urbanopt-geojson built every commercial story at 3.6 m, discarding the floor_to_floor_height it had just read."""
    _gem(str(tmp_path))
    P.main([str(tmp_path)])
    rb = open(_geojson_path(tmp_path)).read()
    assert P._FLOOR_FIXED not in rb and "OpenStudio.convert(floor_to_floor_height.to_f, 'ft', 'm').get : 3.6) : 3" in rb
    capsys.readouterr()
    P.main([str(tmp_path)])
    assert rb == open(_geojson_path(tmp_path)).read()


def test_moved_geojson_code_fails_loudly(tmp_path):
    _gem(str(tmp_path))
    _geojson(str(tmp_path), moved=True)
    with pytest.raises(SystemExit, match='building.rb: expected the patched code once'):
        P.main([str(tmp_path)])


def _geojson_path(root):
    return os.path.join(str(root), 'gems', 'urbanopt-geojson-1.0.1', 'lib', 'urbanopt', 'geojson', 'building.rb')


def test_vacant_buildings_get_no_phantom_pv(tmp_path, capsys):
    """urbanopt-geojson's 'DLM: temp hack' gave every Vacant building a footprint-sized 30% PV array over its roof
    (a vacant Sacramento building reported 1.16 GWh produced, DEER check 2026-10-01)."""
    _gem(str(tmp_path))
    P.main([str(tmp_path)])
    for m in ('urban_geometry_creation_zoning', 'urban_geometry_creation'):
        rb = open(os.path.join(str(tmp_path), 'gems', 'urbanopt-geojson-1.0.1', 'lib', 'measures', m, 'measure.rb')).read()
        assert 'Helper.create_shading_surfaces(' not in rb and 'powertwin patch' in rb and 'building_type = feature.building_type' in rb
    capsys.readouterr()
    P.main([str(tmp_path)])                                  # idempotent


def test_doe_ref_schools_keep_their_gym_audience_conditioned_under_90_1(tmp_path, capsys):
    """A DOE Ref school's 'Gym - audience' zones got no thermostat and no HVAC when its HVAC template was 90.1
    (one stock: a whole unconditioned story ran past 200 C; 2026-10-01)."""
    lib = _gem(str(tmp_path))
    P.main([str(tmp_path)])
    for rel, _edit in P.JSON_PATCHES['openstudio-standards']:
        rows = json.load(open(os.path.join(lib, rel)))['space_types']
        ga = [r for r in rows if r['space_type'] == 'Gym - audience']
        assert len(ga) == 1 and ga[0]['heating_setpoint_schedule'] == 'H' and ga[0]['cooling_setpoint_schedule'] == 'C'
        assert 'powertwin patch' in ga[0]['notes']
    capsys.readouterr()
    P.main([str(tmp_path)])
    for rel, _edit in P.JSON_PATCHES['openstudio-standards']:
        assert len([r for r in json.load(open(os.path.join(lib, rel)))['space_types'] if r['space_type'] == 'Gym - audience']) == 1


def test_a_collapsed_core_or_untiled_split_keeps_the_floor_whole(tmp_path, capsys):
    """Narrow footprints got degenerate surfaces and sliver zones; the self-intersection check keeps its upstream
    warn-and-divide, since returning there left 27% of Teton's commercial area undivided on footprints that run fine."""
    _gem(str(tmp_path))
    P.main([str(tmp_path)])
    rb = open(os.path.join(str(tmp_path), 'gems', 'urbanopt-geojson-1.0.1', 'lib', 'urbanopt', 'geojson', 'zoning.rb')).read()
    i = rb.index("Self intersecting surface result, will not divide')")
    assert rb.index('end', i) < rb.index('return [floor_print]', i)            # upstream: warns, divides
    assert "core_area.get < 0.1 * floor_area.get" in rb and rb.count('return [floor_print]') == 2
    assert 'does not tile the floor' in rb and rb.rstrip().endswith('return result\nafter')



def test_story_blends_iterate_in_name_order(tmp_path, capsys):
    """OpenStudio returns spaces and space types in random handle order: the blend's tie between equal-area space
    types (which picks the thermostat and HVAC) went either way from run to run (Teton 17126, 2026-10-07)."""
    lib = _gem(str(tmp_path))
    P.main([str(tmp_path)])
    blend = open(os.path.join(lib, 'create_typical', 'space_type_blend.rb')).read()
    assert blend.count('space_types.sort.each do |space_type|') == 2
    assert 'space_array.concat(space_type.spaces.sort)' in blend and 'space_type.spaces.sort.each do |space|' in blend
    measure = open(os.path.join(str(tmp_path), 'gems', 'openstudio-model-articulation-0.11.1', 'lib', 'measures',
                                'blended_space_type_from_model', 'measure.rb')).read()
    assert 'story.spaces.sort.each do |space|' in measure and 'model.getSpaceTypes.sort.each do |space_type|' in measure
    assert P._STORY_SPACES not in measure
    capsys.readouterr()
    P.main([str(tmp_path)])
    assert measure == open(os.path.join(str(tmp_path), 'gems', 'openstudio-model-articulation-0.11.1', 'lib',
                                        'measures', 'blended_space_type_from_model', 'measure.rb')).read()


def test_openstudio_standards_installs_the_name_order_shim(tmp_path):
    """Plural model getters and unordered relationship lists return objects in name order once openstudio-standards
    loads (an infiltration schedule from the first air loop and F-factor floors shared by name, 2026-10-07)."""
    _gem(str(tmp_path))
    P.main([str(tmp_path)])
    main = open(_main_file_path(tmp_path)).read()
    assert main.count('module PowertwinDeterministicOrder') == 1 and 'PowertwinDeterministicOrder.install' in main
    assert main.index(P._ORDER_SHIM_ANCHOR) < main.index('PowertwinDeterministicOrder') < main.index('module OpenstudioStandards')
    P.main([str(tmp_path)])
    assert open(_main_file_path(tmp_path)).read() == main


def _main_file_path(tmp_path):
    return os.path.join(str(tmp_path), 'gems', 'openstudio-standards-0.7.1', 'lib', 'openstudio-standards.rb')


def test_outlier_zone_sort_is_stable_across_runs(tmp_path):
    """Near-equal zone values (same space type) differ in the last digit from run to run; the sort used to pick the
    primary zones, and so the air-loop creation order, followed that noise (2026-10-07)."""
    lib = _gem(str(tmp_path))
    P.main([str(tmp_path)])
    src = open(os.path.join(lib, 'standards', 'Standards.Model.rb')).read()
    assert P._OUTLIER_SORT not in src and "[hsh[key_to_inspect].round(9), hsh['zone'].name.get.to_s]" in src


def test_emissions_floor_area_is_rounded(tmp_path):
    """building.floorArea's last digit followed OpenStudio's random summation order into the EMS program (2026-10-08)."""
    _gem(str(tmp_path))
    P.main([str(tmp_path)])
    rb = open(os.path.join(str(tmp_path), 'gems', 'openstudio-common-measures-0.11.1', 'lib', 'measures',
                           'add_ems_emissions_reporting', 'measure.rb')).read()
    assert P._EMS_AREA not in rb and 'floor_area = (building.floorArea * 10.764).round(6)' in rb


def test_a_heat_pump_never_heats_a_loop_above_140f(tmp_path, capsys):
    """natfan2 (2026-10-21): every sampled 'Coil:WaterHeating:AirToWaterHeatPump:Pumped ... Rated total cooling capacity
    is zero or less' fatal (13 of 40 failures) was a SmallHotel with swh_src HeatPump: model_add_typical_swh gave its
    180 F Laundry loop (the only target above 140 F in every 90.1 edition) a heat pump water heater, whose COP curve
    falls below 1 at 82 C condenser water. That loop takes electric resistance, as the library's booster heaters always
    do; a 140 F loop keeps its heat pump and a requested gas or electric heater is untouched."""
    lib = _gem(str(tmp_path))
    P.main([str(tmp_path)])
    rel = os.path.join('standards', 'Standards.ServiceWaterHeating.rb')
    src = open(os.path.join(lib, rel)).read()
    assert src.count('powertwin patch') == 1
    rule = "loop_water_heater_fuel = water_heater_fuel == 'HeatPump' && service_water_temperature_f > 140.0 ? 'Electricity' : water_heater_fuel"
    assert rule in src and src.index(rule) < src.index('swh_loop = OpenstudioStandards::ServiceWaterHeating.create_service_water_heating_loop(model,')
    call = src[src.index('create_service_water_heating_loop(model,'):]
    assert 'water_heater_fuel: loop_water_heater_fuel,' in call.split('number_of_water_heaters')[0]
    assert 'water_heater_fuel = ' not in src.replace(rule, '')   # the requested fuel itself never changes (it carries
                                                                  # on to the next space type)


def test_a_stat_file_with_one_typical_week_parses(tmp_path, capsys):
    """natfan2 (2026-10-21): 278 of 562 failures were create_typical_building_from_model raising NoMethodError at
    stat_file.rb:349 on two Hawaii stations (Lihue TMY2, Keahole-Kona TMY3): a tropical .stat lists one 'Typical Week
    Period selected' and the parser read a second (the winter week). A missing winter week is 'NA', as the parser
    already records a missing autumn and spring week; the typical weeks are only printed, never used by the model."""
    lib = _gem(str(tmp_path))
    P.main([str(tmp_path)])
    src = open(os.path.join(lib, 'weather', 'stat_file.rb')).read()
    assert src.count('powertwin patch') == 1
    assert "@typical_winter_dry_week = match_data[1].nil? ? 'NA' : Date.parse(" in src
    assert "@typical_winter_dry_week = Date.parse(" not in src
