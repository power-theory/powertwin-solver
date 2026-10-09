#!/usr/bin/env python3
"""Fix upstream defects in the gems a simulation loads: openstudio-standards 0.7.x (1, 2, 4, 6), urbanopt-geojson 1.0.x
(3, 5) and openstudio-model-articulation (6).

1. create_typical.rb, weekend hours past midnight. The midnight-overlap message interpolates
   `wknd_op_hrs_start`, a name that does not exist (the weekday branch correctly uses
   `wkdy_op_hrs_start_time`), so create_typical_building_from_model raises NameError and the building
   produces no report. All 97 failures of the tetonfan1 pilot (2026-09-19, job 17869342) were this:
   commercial variants with weekend start plus duration past 24:00, a combination any user can set.

2. Prototype.hvac_systems.rb, PTAC and PSZ-AC on a central air source heat pump. Both builders hard-code
   the zone heating design supply air at 122 F (50 C) whatever hot water loop the coil is on. The heat
   pump systems ('PTAC with central air source heat pump', 'PSZ-AC with central air source heat pump')
   get the 'LowTemperature' loop, designed for 120 F (48.9 C) supply water, and a water coil cannot heat
   air above its entering water, so EnergyPlus fails 'Autosizing of heating coil UA ... Inadequate water
   side capacity' and the building produces no report (tetonfan3 pilot 2026-09-27: 5 of 5; as a user edit
   on 4 Teton commercial buildings: 8 of 8). Both builders read the loop's design exit temperature
   (`hw_temp_c`) but use it only for the coil's rated water temperatures, never to cap the supply air;
   upstream master is unchanged (checked 2026-09-28). The patch keeps
   122 F whenever the loop supplies hotter water (boiler 180 F and district loops: no change) and
   otherwise takes the library's own zone heating default from standard_design_sizing_temperatures
   (104 F), the value the fan coil and reheat heat pump systems already size to.

3. urbanopt-geojson building.rb, commercial floor-to-floor height. On the zoning path (every commercial building:
   urban_geometry_creation_zoning) create_building reads the feature's floor_to_floor_height and then overwrites it
   with a fixed 3.6 m, so every commercial building was built 11.8 ft per story whatever the input (2026-09-30:
   Teton 14616 volume/area 3.6 m; floor_height 9 -> 14 moved electricity 1% through create_bar only). The patch
   honours the property (feet, as the gem's maximum_roof_height) and keeps 3.6 m without it. generateFeatureFile
   writes it for commercial features: the prototype's typical story height unless the user sets floor_height.

4. openstudio-standards 90.1 space-type data, SecondarySchool 'Gym - audience'. create_bar gives a DOE Ref school a
   'Gym - audience' space type (space_type_ratios.rb, DOE Ref branch only), and only the DOE Ref data define it. The
   mapper's HVAC step 2 runs a newer template for a building whose systems were replaced (the 20-year rule), and under
   a 90.1 template the space type has no data, so its zones get no thermostat, are logged 'unconditioned' and get no
   HVAC: on a 3+ story school that is a whole unconditioned story (one collection: all 9 story-3 zones; Teton 14628 as
   a 6-story school: 15 zones), whose temperature ran past EnergyPlus's 200 C bound at 13 ft stories (2026-10-01).
   The patch adds 'Gym - audience' to each 90.1 edition PowerTwin uses as a copy of that edition's SecondarySchool
   'Gym' (bleacher seating in the same gym: same thermostat, systems and loads basis). It is the only DOE Ref space type
   of a PowerTwin prototype missing from 90.1 apart from SmallHotel 'GuestRoom', and SmallHotel never builds on DOE Ref.

5. urbanopt-geojson zoning.rb divide_floor_print insets each floor 4 m for a core and perimeter zones. A footprint
   narrower than about twice that depth collapses the core or overshoots the inset: degenerate surfaces, sliver zones
   and EnergyPlus temperature or airflow fatals, most of the stocks' standing geometry failures (2026-10-01). The patch
   keeps the floor whole when the core would be under 10% of the floor or the pieces do not tile it (sum within 1%,
   none under 0.5 m2). The upstream self-intersection check still only warns and divides: returning there too (as its
   warning says) left 27% of Teton's commercial floor area undivided on footprints that build and simulate fine.

6. Determinism: openstudio-standards space_type_blend.rb and openstudio-model-articulation's blended_space_type_from_model.
   OpenStudio returns model objects (getSpaces, story.spaces, space_type.spaces ...) in handle order, and handles are
   random per run. The story blend gathers each story's space types in that order and keeps the first of equal-area
   space types as the blend's standards space type, which chooses the thermostat and HVAC: a Teton Laboratory (asset
   17126) got the office setpoints in one run and the lab setpoints in the next, which moved its design loads and coil
   sizes and its summer electricity about 5% (2026-10-07). The patch iterates spaces and space types in name order
   (OpenStudio's own sort), so ties and blended sums resolve the same way every run. openstudio-standards.rb also
   installs PowertwinDeterministicOrder: the model's plural getters and a list of unordered relationships return
   objects in name order (an infiltration schedule from the first air loop and F-factor floors shared by name were
   order-dependent too). Standards.Model.rb model_eliminate_outlier_zones sorts zones on a float whose last digits
   follow OpenStudio's summation order: it now sorts on the value to 1e-9 and then the zone name. openstudio-common-
   measures' add_ems_emissions_reporting rounds the floor area it writes into its EMS program (its last digit followed
   the same order). The SpaceList order in the translated input is fixed by the upload measure powertwin_canonical_idf.

Usage: patch_openstudio_standards.py [GEM_ROOT ...]
Patches every openstudio-standards and urbanopt-geojson copy under the given gem roots (default: the image's gem
directories; the cluster passes its per-job BUNDLE_PATH after bundle install). Idempotent. Fails when a
file is missing or carries neither the defect nor the fix, so a gem upgrade that moves the code is
noticed instead of silently skipped.
"""
import glob
import os
import sys

_LOOP_CAP = """{i}# powertwin patch: a water coil cannot heat air above its entering water; on a loop that cannot
{i}# supply the temperature above (the 120 F heat pump loop) use the library's zone heating default
{i}unless hot_water_loop.nil? || dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] < hw_temp_c
{i}  dsgn_temps['zn_htg_dsgn_sup_air_temp_f'] = standard_design_sizing_temperatures['zn_htg_dsgn_sup_air_temp_f']
{i}  dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] = standard_design_sizing_temperatures['zn_htg_dsgn_sup_air_temp_c']
{i}end
"""


def _cap_after(anchor: str, indent: str) -> tuple[str, str]:
    return anchor, anchor + _LOOP_CAP.format(i=indent)


_PTAC = """    # adjusted zone design temperatures for ptac
    dsgn_temps['zn_htg_dsgn_sup_air_temp_f'] = 122.0
    dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] = OpenStudio.convert(dsgn_temps['zn_htg_dsgn_sup_air_temp_f'], 'F', 'C').get
"""
_PSZ = """      # adjusted design heating temperature for psz_ac
      dsgn_temps['zn_htg_dsgn_sup_air_temp_f'] = 122.0
      dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] = OpenStudio.convert(dsgn_temps['zn_htg_dsgn_sup_air_temp_f'], 'F', 'C').get
"""

_FLOOR_FIXED = "        floor_to_floor_height = zoning ? 3.6 : 3\n"
_FLOOR_FIX = ("        # powertwin patch: zoning read the feature's floor_to_floor_height above and then fixed every commercial\n"
              "        # building at 3.6 m; honour it (ft, like maximum_roof_height) and keep 3.6 m without it\n"
              "        floor_to_floor_height = zoning ? (floor_to_floor_height ? OpenStudio.convert(floor_to_floor_height.to_f, 'ft', 'm').get : 3.6) : 3\n")

_VACANT_PV = ("      if building_type == 'Vacant'\n"
              "        shading_surfaces = URBANopt::GeoJSON::Helper.create_shading_surfaces(feature, model, @origin_lat_lon, @runner, spaces)\n"
              "      end\n")
_VACANT_PV_FIX = ("      # powertwin patch: a 'DLM: temp hack' gave every Vacant building create_shading_surfaces, which builds a\n"
                  "      # footprint-sized PV array (100% active cells, 30% efficiency) 1 m over the roof: vacant buildings reported\n"
                  "      # solar production they do not have and their roofs were shaded (2026-10-01). Removed.\n")

_ZONING_CORE = ("        if self_intersects\n"
                          "          runner.registerWarning('Self intersecting surface result, will not divide')\n"
                          "        end\n")
_ZONING_CORE_FIX = ("        if self_intersects\n"
                              "          runner.registerWarning('Self intersecting surface result, will not divide')\n"
                              "        end\n"
                              "        # powertwin patch: a footprint narrower than twice the perimeter depth collapses the core without\n"
                              "        # self-intersecting (an 8 m wide floor inset 4 m a side): degenerate surfaces and sliver perimeter zones.\n"
                              "        # Keep the floor whole when the core would be under 10% of it (2026-10-01).\n"
                              "        # Not in scale mode, which returns the inset vertices themselves, not a list of floor prints.\n"
                              "        unless scale\n"
                              "          core_area = OpenStudio.getArea(new_vertices)\n"
                              "          floor_area = OpenStudio.getArea(vertices)\n"
                              "          if core_area.empty? || floor_area.empty? || core_area.get < 0.1 * floor_area.get\n"
                              "            runner.registerWarning('Core too small for perimeter zoning, will not divide')\n"
                              "            return [floor_print]\n"
                              "          end\n"
                              "        end\n")

_ZONING_RETURN = ("        end\n"
                  "        return result\n")
_ZONING_RETURN_FIX = ("        end\n"
                      "        # powertwin patch: the inset overshoots on a floor narrower than twice the perimeter depth, and the core\n"
                      "        # can keep a plausible area while perimeter zones collapse (a 3.4 m wide floor: 0.035 m2 zones behind\n"
                      "        # 10 m2 of wall, past EnergyPlus's 200 C bound). The pieces of a sound split tile the floor: keep the floor\n"
                      "        # whole unless they sum to it within 1% and no piece is under 0.5 m2 (2026-10-01).\n"
                      "        unless scale\n"
                      "          floor_area = OpenStudio.getArea(floor_print)\n"
                      "          areas = result.map { |piece| OpenStudio.getArea(piece) }\n"
                      "          if floor_area.empty? || areas.any?(&:empty?) || areas.map(&:get).min < 0.5 ||\n"
                      "             (areas.map(&:get).sum - floor_area.get).abs > 0.01 * floor_area.get\n"
                      "            runner.registerWarning('Perimeter zoning does not tile the floor, will not divide')\n"
                      "            return [floor_print]\n"
                      "          end\n"
                      "        end\n"
                      "        return result\n")

_ORDER_NOTE = "# powertwin patch: iterate in name order; OpenStudio's own order follows random handles (2026-10-07)\n"
_BLEND_ARRAY = ("          space_types.each do |space_type|\n"
                "            space_array.concat(space_type.spaces)\n")
_BLEND_ARRAY_FIX = ("          " + _ORDER_NOTE +
                    "          space_types.sort.each do |space_type|\n"
                    "            space_array.concat(space_type.spaces.sort)\n")
_BLEND_LARGEST = ("        largest_space_type_ratio = 0.00\n"
                  "        space_types.each do |space_type|\n")
_BLEND_LARGEST_FIX = ("        largest_space_type_ratio = 0.00\n"
                      "        # powertwin patch: name order, so the first of equal-area space types (the blend's standards space type,\n"
                      "        # which chooses its thermostat and HVAC) is the same every run; handle order is random (2026-10-07)\n"
                      "        space_types.sort.each do |space_type|\n")
_BLEND_TOTALS = ("          # loop through spaces so I can skip if not included in floor area\n"
                 "          space_type.spaces.each do |space|\n")
_BLEND_TOTALS_FIX = ("          # loop through spaces so I can skip if not included in floor area\n"
                     "          " + _ORDER_NOTE +
                     "          space_type.spaces.sort.each do |space|\n")
_STORY_SPACES = "        story.spaces.each do |space|\n"
_STORY_SPACES_FIX = "        " + _ORDER_NOTE + "        story.spaces.sort.each do |space|\n"
_TYPE_SPACE_TYPES = ("      # loop through spaces and organize by building type\n"
                     "      model.getSpaceTypes.each do |space_type|\n")
_TYPE_SPACE_TYPES_FIX = ("      # loop through spaces and organize by building type\n"
                         "      " + _ORDER_NOTE +
                         "      model.getSpaceTypes.sort.each do |space_type|\n")

_ORDER_SHIM_ANCHOR = "require_relative 'openstudio-standards/version'\n"
_ORDER_SHIM = _ORDER_SHIM_ANCHOR + r"""
# powertwin patch (determinism, 2026-10-07): OpenStudio returns a model's objects in handle order, and handles are
# random per run, so code that iterates model.getX (or a space's loads, a story's spaces ...) and keeps the first match
# or a running sum builds a slightly different model each run (Teton: an infiltration schedule taken from whichever air
# loop came first; an F-factor floor shared by two spaces sized from whichever came first). Return the model's plural
# getters and the unordered relationship lists below in name order (OpenStudio's own sort), from the moment
# openstudio-standards loads. Ordered lists (construction layers, loop components, schedule rules) are untouched.
module PowertwinDeterministicOrder
  UNORDERED = {
    'SpaceType' => %w[spaces people lights luminaires electricEquipment gasEquipment hotWaterEquipment steamEquipment
                      otherEquipment internalMass spaceInfiltrationDesignFlowRates spaceInfiltrationEffectiveLeakageAreas],
    'Space' => %w[surfaces people lights luminaires electricEquipment gasEquipment hotWaterEquipment steamEquipment
                  otherEquipment internalMass spaceInfiltrationDesignFlowRates spaceInfiltrationEffectiveLeakageAreas],
    'BuildingStory' => %w[spaces],
    'ThermalZone' => %w[spaces],
    'Surface' => %w[subSurfaces],
    'AirLoopHVAC' => %w[thermalZones]
  }.freeze

  def self.sorted(result)
    return result unless result.is_a?(Array) && result.size > 1 && result.first.respond_to?(:nameString)

    result.each_with_index.sort_by { |o, i| [o.nameString, o.iddObjectType.valueName, i] }.map(&:first)
  end

  def self.wrap(klass, names)
    names = names.select { |m| klass.method_defined?(m) }
    return if names.empty? || klass.instance_variable_get(:@powertwin_sorted)

    klass.prepend(Module.new do
      names.each do |m|
        define_method(m) { |*args, &blk| PowertwinDeterministicOrder.sorted(super(*args, &blk)) }
      end
    end)
    klass.instance_variable_set(:@powertwin_sorted, true)
  end

  def self.install
    model = OpenStudio::Model::Model
    wrap(model, model.instance_methods.map(&:to_s).select { |m| m.match?(/\Aget[A-Z]\w*s\z/) && !m.end_with?('ByName') })
    UNORDERED.each { |cls, names| wrap(OpenStudio::Model.const_get(cls), names) }
  end
end
PowertwinDeterministicOrder.install if defined?(OpenStudio::Model::Model)
"""

_OUTLIER_SORT = "      array_of_zones = array_of_zones.sort_by { |hsh| hsh[key_to_inspect] }\n"
_OUTLIER_SORT_FIX = ("      # powertwin patch: zones of one space type have near-equal values whose last digits depend on OpenStudio's\n"
                     "      # summation order; sort on the value to 1e-9 and then the zone name, so the primary zones (and the order\n"
                     "      # their air loops are created in) are the same every run (2026-10-07)\n"
                     "      array_of_zones = array_of_zones.sort_by { |hsh| [hsh[key_to_inspect].round(9), hsh['zone'].name.get.to_s] }\n")

_EMS_AREA = "    floor_area = building.floorArea * 10.764 #change from m2 to ft2\n"
_EMS_AREA_FIX = ("    # powertwin patch: building.floorArea sums the spaces in OpenStudio's random handle order, so its last digit\n"
                 "    # changed from run to run in the EMS program text (2026-10-08); round it\n"
                 "    floor_area = (building.floorArea * 10.764).round(6) #change from m2 to ft2\n")

# gem -> (library dir under the gem's lib, [(file under that dir, [(defect, fix), ...]), ...])
GEM_PATCHES = {
    'openstudio-standards': ('openstudio-standards', [
        (os.path.join('create_typical', 'create_typical.rb'),
         [('Weekend start time of #{wknd_op_hrs_start} plus duration',
           'Weekend start time of #{wknd_op_hrs_start_time} plus duration')]),
        (os.path.join('prototypes', 'common', 'objects', 'Prototype.hvac_systems.rb'),
         [_cap_after(_PTAC, '    '), _cap_after(_PSZ, '      ')]),
        (os.path.join('create_typical', 'space_type_blend.rb'),
         [(_BLEND_ARRAY, _BLEND_ARRAY_FIX), (_BLEND_LARGEST, _BLEND_LARGEST_FIX), (_BLEND_TOTALS, _BLEND_TOTALS_FIX)]),
        (os.path.join('..', 'openstudio-standards.rb'), [(_ORDER_SHIM_ANCHOR, _ORDER_SHIM)]),
        (os.path.join('standards', 'Standards.Model.rb'), [(_OUTLIER_SORT, _OUTLIER_SORT_FIX)]),
    ]),
    'urbanopt-geojson': (os.path.join('urbanopt', 'geojson'), [
        ('building.rb', [(_FLOOR_FIXED, _FLOOR_FIX)]),
        ('zoning.rb', [(_ZONING_CORE, _ZONING_CORE_FIX), (_ZONING_RETURN, _ZONING_RETURN_FIX)]),
        (os.path.join('..', '..', 'measures', 'urban_geometry_creation_zoning', 'measure.rb'), [(_VACANT_PV, _VACANT_PV_FIX)]),
        (os.path.join('..', '..', 'measures', 'urban_geometry_creation', 'measure.rb'), [(_VACANT_PV, _VACANT_PV_FIX)]),
    ]),
    'openstudio-common-measures': ('measures', [
        (os.path.join('add_ems_emissions_reporting', 'measure.rb'), [(_EMS_AREA, _EMS_AREA_FIX)]),
    ]),
    'openstudio-model-articulation': ('measures', [
        (os.path.join('blended_space_type_from_model', 'measure.rb'),
         [(_STORY_SPACES, _STORY_SPACES_FIX), (_TYPE_SPACE_TYPES, _TYPE_SPACE_TYPES_FIX)]),
    ]),
}
PATCHES = GEM_PATCHES['openstudio-standards'][1]
# Structured data patches (4): gem -> [(file under the gem's lib dir, edit(data) -> bool changed), ...]
_GYM_AUDIENCE_NOTE = ("powertwin patch: copy of SecondarySchool 'Gym' so a DOE Ref school whose HVAC is replaced under "
                      "this template keeps its gym-audience zones conditioned (2026-10-01)")


def _add_gym_audience(data):
    rows = data['space_types']
    if any(r.get('building_type') == 'SecondarySchool' and r.get('space_type') == 'Gym - audience' for r in rows):
        return False
    gym = [r for r in rows if r.get('building_type') == 'SecondarySchool' and r.get('space_type') == 'Gym']
    if len(gym) != 1:
        sys.exit(f"expected one SecondarySchool 'Gym' row, found {len(gym)}; check the gem version")
    rows.append(dict(gym[0], space_type='Gym - audience', notes=_GYM_AUDIENCE_NOTE))
    return True


JSON_PATCHES = {
    'openstudio-standards': [
        (os.path.join('standards', 'ashrae_90_1', f'ashrae_90_1_{y}', 'data', f'ashrae_90_1_{y}.spc_typ.json'),
         _add_gym_audience) for y in ('2004', '2007', '2010', '2013')],
}
DEFAULT_ROOTS = ['/usr/local/lib/ruby/gems/3.2.2', '/var/oscli/gems/ruby/3.2.0']


def _libs(roots, gem='openstudio-standards'):
    rel = os.path.join('gems', f'{gem}-*', 'lib', GEM_PATCHES[gem][0])
    return sorted({p for r in roots for p in glob.glob(os.path.join(r, rel)) + glob.glob(os.path.join(r, 'ruby', '*', rel))})


def main(roots):
    for gem, (_libdir, patches) in GEM_PATCHES.items():
        libs = _libs(roots, gem)
        if not libs:
            sys.exit(f'no {gem} under {roots}')
        for lib in libs:
            _patch_lib(lib, patches)
            _patch_json(lib, JSON_PATCHES.get(gem, []))


def _patch_json(lib, patches):
    import json
    for rel, edit in patches:
        path = os.path.join(lib, rel)
        if not os.path.isfile(path):
            sys.exit(f'{path}: missing; check the gem version')
        with open(path) as fh:
            data = json.load(fh)
        if not edit(data):
            print(f'already patched: {path}')
            continue
        tmp = f'{path}.{os.getpid()}.tmp'            # atomic, as _patch_lib
        with open(tmp, 'w') as fh:
            json.dump(data, fh, indent=2)
            fh.write('\n')
        os.chmod(tmp, os.stat(path).st_mode)
        os.replace(tmp, path)
        print(f'patched: {path}')


def _patch_lib(lib, patches):
    for rel, edits in patches:
        path = os.path.join(lib, rel)
        if not os.path.isfile(path):
            sys.exit(f'{path}: missing; check the gem version')
        with open(path) as fh:
            src = fh.read()
        out = src
        for bad, good in edits:
            if good in out:
                continue
            if out.count(bad) != 1:
                sys.exit(f'{path}: expected the patched code once, found {out.count(bad)}; check the gem version')
            out = out.replace(bad, good)
        if out == src:
            print(f'already patched: {path}')
            continue
        # atomic: parallel cluster batches patch one shared gem home, and a Ruby process loading the
        # file must see the old or the new content, never a half-written one
        tmp = f'{path}.{os.getpid()}.tmp'
        with open(tmp, 'w') as fh:
            fh.write(out)
        os.chmod(tmp, os.stat(path).st_mode)
        os.replace(tmp, path)
        print(f'patched: {path}')


if __name__ == '__main__':
    main(sys.argv[1:] or DEFAULT_ROOTS)
