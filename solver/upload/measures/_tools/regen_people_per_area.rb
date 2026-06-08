# Regenerate openstudio_standards_people_per_area.json with area-weighted
# whole-building densities by walking each ASHRAE 90.1-2013 prototype OSM.
#
# Run inside the solver container (needs the openstudio gem):
#   docker exec powertwin-solver-flask ruby /solver/upload/measures/_tools/regen_people_per_area.rb
#   # then copy /tmp/proto_densities.json out and merge into the JSON.
#
# The Office and Warehouse rows are intentionally NOT overwritten by this
# script -- their original values were already area-weighted by a different
# (correct) methodology and are preserved as-is.

require 'openstudio'
require 'json'

DATA_DIR = '/usr/local/lib/ruby/gems/3.2.2/gems/openstudio-standards-0.7.1/lib/openstudio-standards/standards/ashrae_90_1/ashrae_90_1_2013/data'
GEOM_DIR = '/usr/local/lib/ruby/gems/3.2.2/gems/openstudio-standards-0.7.1/data/geometry'

spc_typ_rows = JSON.parse(File.read("#{DATA_DIR}/ashrae_90_1_2013.spc_typ.json"))['space_types']
density_lookup = {}
by_st_only = Hash.new { |h, k| h[k] = [] }
spc_typ_rows.each do |r|
  bt, st, v = r['building_type'], r['space_type'], r['occupancy_per_area']
  next if bt.nil? || st.nil? || v.nil?
  density_lookup[[bt, st]] = v.to_f
  by_st_only[st] << [bt, v.to_f]
end

BT_ALIAS = {
  'SmallOffice' => 'Office', 'MediumOffice' => 'Office', 'LargeOffice' => 'Office',
  'SmallOfficeDetailed' => 'Office', 'MediumOfficeDetailed' => 'Office',
  'LargeOfficeDetailed' => 'Office',
  'RetailStandalone' => 'Retail', 'RetailStripmall' => 'StripMall',
}

def try_match(density_lookup, by_st_only, bt_key, name)
  [[bt_key, name],
   [bt_key, name.sub(/^#{Regexp.escape(bt_key)} /, '')],
   [bt_key, name.sub(/^#{Regexp.escape(bt_key)} - /, '')],
   [bt_key, "#{bt_key} - #{name}"],
   [bt_key, "#{bt_key} - #{name.sub(/^#{Regexp.escape(bt_key)} /, '')}"]
  ].each do |k|
    return density_lookup[k] if density_lookup.key?(k)
  end
  hits = by_st_only[name]
  hits.size == 1 ? hits[0][1] : nil
end

proto_rows = JSON.parse(File.read("#{DATA_DIR}/ashrae_90_1_2013.prototype_inputs.json"))['prototype_inputs']
vt = OpenStudio::OSVersion::VersionTranslator.new
out = {}

proto_rows.each do |r|
  bt = r['building_type']
  geom = r['geometry_osm']
  next if geom.nil? || geom == 'None'
  path = "#{GEOM_DIR}/#{geom}"
  next unless File.exist?(path)
  m = vt.loadModel(OpenStudio::Path.new(path))
  next if m.empty?
  m = m.get
  bt_key = BT_ALIAS[bt] || bt

  total_area_m2 = 0.0
  total_people = 0.0
  unmatched_m2 = 0.0
  unmatched_names = []

  m.getSpaces.each do |sp|
    area_m2 = sp.floorArea.to_f
    next if area_m2 <= 0
    total_area_m2 += area_m2
    next unless sp.spaceType.is_initialized
    st_full = sp.spaceType.get.name.to_s
    d = try_match(density_lookup, by_st_only, bt_key, st_full)
    if d.nil?
      unmatched_m2 += area_m2
      unmatched_names << st_full
      next
    end
    area_ft2 = area_m2 * 10.7639
    total_people += d * area_ft2 / 1000.0
  end
  next if total_area_m2 <= 0
  total_area_ft2 = total_area_m2 * 10.7639
  bldg_avg = total_people / total_area_ft2 * 1000.0
  out[bt] = {
    'total_area_ft2' => total_area_ft2.round,
    'total_people' => total_people.round(1),
    'building_avg_per_1000ft2' => bldg_avg.round(2),
    'unmatched_area_pct' => (unmatched_m2 / total_area_m2 * 100).round(1),
    'bt_used_for_lookup' => bt_key,
  }
end

File.write('/tmp/proto_densities.json', JSON.pretty_generate(out))
puts "wrote /tmp/proto_densities.json (#{out.size} prototypes)"
