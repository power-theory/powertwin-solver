# Characterization test for PowerTwinRefs (the pure-Ruby reference/helper module
# extracted from PowerTwin.rb). Loads with plain `ruby` -- NO OpenStudio
# container needed -- and snapshots every helper across a fixed input matrix.
# This is the Phase-2 guardrail: behavior-preserving refactors that move the
# residential measure-arg logic into PowerTwinRefs helpers must keep this green.
#
#   ruby tests/test_powertwin_refs.rb          # verify against the golden
#   REGEN=1 ruby tests/test_powertwin_refs.rb  # regenerate (deliberate change only)
require 'json'
require_relative '../solver/upload/powertwin_refs'

CZS = %w[ASHRAE\ 169-2013-1A ASHRAE\ 169-2013-2A ASHRAE\ 169-2013-3B ASHRAE\ 169-2013-4A
         ASHRAE\ 169-2013-5A ASHRAE\ 169-2013-6A ASHRAE\ 169-2013-7 ASHRAE\ 169-2013-8 bogus]
TIERS = ['Single Pane', 'Double Pane', 'Triple Pane', 'Unknown Tier']
FUELS = %w[Electricity NaturalGas FuelOilNo2 Propane Coal]
YEARS = [0, 1950, 1979, 1980, 1999, 2000, 2009, 2010, 2025]
STATES = %w[AZ MA IL TX CA WY ZZ]
EFF_YEARS = [1980, 1991, 1992, 2006, 2011, 2012, 2014, 2015, 2020, 2021, 2022, 2023, 2025]
CAVITY_RS = [0, 1, 3.5, 11, 13, 19, 30, 38]
REGIONS = ['Northeast', 'Midwest', 'South', 'West', 'Unknown']

def snapshot
  s = {}
  s['window_props'] = TIERS.map { |t| CZS.map { |cz| ["#{t}|#{cz}", PowerTwinRefs.window_props(t, cz)] } }.flatten(1).to_h
  s['window_props_residential'] = TIERS.map { |t| CZS.map { |cz| ["#{t}|#{cz}", PowerTwinRefs.window_props_residential(t, cz)] } }.flatten(1).to_h
  s['swh_efficiency'] = FUELS.map { |f| [f, PowerTwinRefs.swh_efficiency(f)] }.to_h
  s['vintage_bin'] = YEARS.map { |y| [y.to_s, PowerTwinRefs.vintage_bin(y)] }.to_h
  s['cz_number'] = CZS.map { |cz| [cz, PowerTwinRefs.cz_number(cz)] }.to_h
  s['state_to_region'] = STATES.map { |st| [st, PowerTwinRefs.state_to_region(st)] }.to_h
  s['furnace_afue'] = EFF_YEARS.map { |y| [y.to_s, PowerTwinRefs.furnace_afue(y)] }.to_h
  s['boiler_afue'] = EFF_YEARS.map { |y| [y.to_s, PowerTwinRefs.boiler_afue(y)] }.to_h
  s['hp_hspf'] = EFF_YEARS.map { |y| [y.to_s, PowerTwinRefs.hp_hspf(y)] }.to_h
  s['ac_seer'] = EFF_YEARS.map { |y| [true, false].map { |so| ["#{y}|#{so}", PowerTwinRefs.ac_seer(y, so)] } }.flatten(1).to_h
  s['hp_seer'] = EFF_YEARS.map { |y| [true, false].map { |so| ["#{y}|#{so}", PowerTwinRefs.hp_seer(y, so)] } }.flatten(1).to_h
  s['wall_assembly_r'] = CAVITY_RS.map { |r| [r.to_s, PowerTwinRefs.wall_assembly_r(r)] }.to_h
  s['ceiling_assembly_r'] = CAVITY_RS.map { |r| [r.to_s, PowerTwinRefs.ceiling_assembly_r(r)] }.to_h
  s['garage_rate'] = REGIONS.map { |rg| [rg, PowerTwinRefs.garage_rate(rg)] }.to_h
  # garage_attached?: majority-rule (no id / non-stochastic) + hashed cases
  ga = {}
  REGIONS.each do |rg|
    rate = PowerTwinRefs.garage_rate(rg)
    ga["#{rg}|noid|nostoch"] = PowerTwinRefs.garage_attached?(rate, '', false)
    ga["#{rg}|noid|stoch"] = PowerTwinRefs.garage_attached?(rate, '', true)
    %w[feat001 feat002 feat999 building-42].each do |fid|
      ga["#{rg}|#{fid}|stoch"] = PowerTwinRefs.garage_attached?(rate, fid, true)
      ga["#{rg}|#{fid}|nostoch"] = PowerTwinRefs.garage_attached?(rate, fid, false)
    end
  end
  s['garage_attached'] = ga
  s
end

GOLDEN = File.join(__dir__, 'fixtures', 'powertwin_refs_golden.json')
got = snapshot

if ENV['REGEN']
  File.write(GOLDEN, JSON.pretty_generate(got))
  puts "REGENERATED #{GOLDEN} (#{got.values.map(&:size).sum} values)"
  exit 0
end

exp = JSON.parse(File.read(GOLDEN))
fails = 0
got.each do |group, vals|
  vals.each do |k, v|
    # JSON round-trips tuples to arrays; normalize for comparison
    ev = exp.dig(group, k)
    if JSON.parse(v.to_json) != ev
      fails += 1
      puts "  FAIL [#{group}] #{k}: got #{v.inspect}, expected #{ev.inspect}"
    end
  end
end
total = got.values.map(&:size).sum
if fails.zero?
  puts "PowerTwinRefs golden: #{total}/#{total} values match"
  exit 0
else
  puts "PowerTwinRefs golden: #{fails}/#{total} FAILED"
  exit 1
end
