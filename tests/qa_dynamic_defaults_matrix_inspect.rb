# Layer-4 in.osm inspector for qa_dynamic_defaults_matrix.py.
# Loads an OpenStudio model and dumps a JSON object whose keys mirror the
# programmable params from SIM_PARAM_DEFAULTS, so the Python driver can
# compare them against in.osw measure args (layer 3) and feature.json
# (layer 2).
#
# Usage (called from the Python driver via docker exec):
#   ruby /tmp/qa_matrix_inspect.rb /path/to/in.osm
#
# Output: JSON on stdout. On any load failure: a JSON object with a
# "_error" key. No multi-line logging on stdout (rest goes to stderr).

require "json"
require "openstudio"

def load_model(path)
  vt = OpenStudio::OSVersion::VersionTranslator.new
  m  = vt.loadModel(OpenStudio::Path.new(path))
  raise "loadModel returned empty for #{path}" if m.empty?
  m.get
end

def total_people(model)
  total = 0.0
  model.getSpaces.each do |sp|
    total += sp.numberOfPeople
  end
  total
end

def avg_r_value_for_surface_type(model, surface_type)
  # surface_type is 'Wall' or 'RoofCeiling'.
  rs = []
  model.getSurfaces.each do |s|
    next unless s.surfaceType == surface_type
    next unless s.outsideBoundaryCondition == "Outdoors"
    c = s.construction
    next if c.empty?
    constr = c.get
    # construction.thermalConductance => W/m^2-K. Inverse * IP scaler.
    tc = constr.to_LayeredConstruction
    next if tc.empty?
    lc = tc.get
    # Use thermalResistance if available; otherwise sum layer thermalResistance.
    r_si = 0.0
    lc.layers.each do |layer|
      mat = layer.to_OpaqueMaterial
      next if mat.empty?
      r_si += mat.get.thermalResistance.to_f
    end
    # SI m^2-K/W to IP hr-ft^2-F/Btu: divide by 0.176110
    rs << (r_si / 0.176110)
  end
  rs.empty? ? nil : (rs.inject(0.0, :+) / rs.length)
end

def avg_window_u_factor(model)
  us = []
  model.getSubSurfaces.each do |ss|
    next unless %w[FixedWindow OperableWindow GlassDoor Skylight].include?(ss.subSurfaceType)
    c = ss.construction
    next if c.empty?
    constr = c.get
    # SimpleGlazingSystem: uFactor in W/m^2-K
    sg = constr.to_LayeredConstruction
    next if sg.empty?
    sg.get.layers.each do |layer|
      simp = layer.to_SimpleGlazing
      unless simp.empty?
        us << simp.get.uFactor.to_f
      end
    end
  end
  us.empty? ? nil : (us.inject(0.0, :+) / us.length)
end

def window_to_wall_ratio(model)
  total_wall = 0.0
  total_window = 0.0
  model.getSurfaces.each do |s|
    next unless s.surfaceType == "Wall"
    next unless s.outsideBoundaryCondition == "Outdoors"
    total_wall += s.grossArea
    s.subSurfaces.each do |ss|
      next unless %w[FixedWindow OperableWindow GlassDoor].include?(ss.subSurfaceType)
      total_window += ss.grossArea
    end
  end
  return nil if total_wall <= 0
  total_window / total_wall
end

def average_floor_height(model)
  # Simple proxy: building-story z-extents.
  hts = []
  model.getBuildingStorys.each do |st|
    st.nominalZCoordinate.tap { |z| nil }  # unused
    h = st.nominalFloortoFloorHeight
    next if h.empty?
    hts << (h.get.to_f / 0.3048) # m -> ft
  end
  hts.empty? ? nil : (hts.inject(0.0, :+) / hts.length)
end

def building_type(model)
  bt = model.getBuilding.standardsBuildingType
  bt.empty? ? nil : bt.get
end

def main
  path = ARGV.first
  if path.nil? || path.empty?
    puts JSON.dump({ "_error" => "usage: ruby inspect.rb <in.osm>" })
    exit 1
  end
  unless File.exist?(path)
    puts JSON.dump({ "_error" => "file not found: #{path}" })
    exit 1
  end
  begin
    m = load_model(path)
  rescue => e
    puts JSON.dump({ "_error" => "model load failed: #{e.message}" })
    exit 1
  end
  out = {}
  out["number_of_occupants"]    = total_people(m).round(1)
  out["wall_r_value"]           = avg_r_value_for_surface_type(m, "Wall")&.round(2)
  out["roof_r_value"]           = avg_r_value_for_surface_type(m, "RoofCeiling")&.round(2)
  out["window_u_factor_w_m2k"]  = avg_window_u_factor(m)&.round(3)
  out["window_to_wall_ratio"]   = window_to_wall_ratio(m)&.round(3)
  out["floor_height"]           = average_floor_height(m)&.round(2)
  out["standards_building_type"]= building_type(m)
  out["_space_count"]           = m.getSpaces.size
  out["_zone_count"]            = m.getThermalZones.size
  out["_surface_count"]         = m.getSurfaces.size
  out["_subsurface_count"]      = m.getSubSurfaces.size
  puts JSON.dump(out)
end

main
