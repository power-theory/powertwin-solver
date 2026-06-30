# PowerTwin reference-data lookup. Resolves tier names + asset context into
# concrete physical values (U/SHGC, R-value, fuel efficiency) so the custom
# OS measures stay pure appliers. Envelope R-values arrive as already-resolved
# feature-JSON params (from the Python resolver); this module loads only
# census_regions.json and keeps the window U/SHGC and SWH efficiency tables
# inline here since they have no JSON equivalent.
#
# PURE RUBY: depends only on the JSON stdlib and relative file reads -- NO
# OpenStudio/urbanopt stack. Kept in its own file so it loads (and is
# characterization-tested) with plain `ruby`, outside the simulation container.
require 'json'
require 'digest'

module PowerTwinRefs
  REF_DIR = File.join(File.dirname(__FILE__), 'reference_data')
  @@cache = {}
  def self.load(name)
    @@cache[name] ||= JSON.parse(File.read(File.join(REF_DIR, "#{name}.json")))
  end

  # ASHRAE 90.1-2013 Section 5.5 prescriptive nonres window U-factor (W/m2-K)
  # and SHGC by climate zone. From Table 5.5-1 through 5.5-8.
  # https://www.ashrae.org/technical-resources/bookstore/standard-90-1
  CZ_WINDOW = {
    '1' => { u: 6.81, shgc: 0.25 }, '2' => { u: 4.26, shgc: 0.25 },
    '3' => { u: 3.69, shgc: 0.25 }, '4' => { u: 3.12, shgc: 0.40 },
    '5' => { u: 3.12, shgc: 0.40 }, '6' => { u: 2.56, shgc: 0.40 },
    '7' => { u: 2.27, shgc: 0.45 }, '8' => { u: 1.99, shgc: 0.45 },
  }.freeze
  # IECC 2015 RESIDENTIAL fenestration prescriptive U-factor (Table R402.1.2)
  # and SHGC (required only CZ1-3) by climate zone, in SI (W/m2-K). Residential
  # windows are held to far tighter U than the commercial 90.1 table above, so
  # the residential dynamic-defaults branch uses THIS table, not CZ_WINDOW.
  # IP U-factor: CZ1 0.50, CZ2 0.40, CZ3-4 0.35, CZ5-8 0.32 (x 5.678263 -> SI).
  CZ_WINDOW_RES = {
    '1' => { u: 2.839, shgc: 0.25 }, '2' => { u: 2.271, shgc: 0.25 },
    '3' => { u: 1.987, shgc: 0.25 }, '4' => { u: 1.987, shgc: 0.40 },
    '5' => { u: 1.817, shgc: 0.40 }, '6' => { u: 1.817, shgc: 0.40 },
    '7' => { u: 1.817, shgc: 0.40 }, '8' => { u: 1.817, shgc: 0.40 },
  }.freeze
  # Window U-factor and SHGC multipliers vs the 90.1-2013 prescriptive baseline
  # (which assumes double low-e). Single Pane U multiplier 2.0 reflects RESNET
  # clear-single stock ~U-1.18 Btu/hr-ft2-F vs 90.1 CZ4 prescriptive ~U-0.55
  # (= 3.12 W/m2-K). Triple Pane 0.55 reflects high-perf low-e triple U-0.30
  # vs the same baseline. SHGC multipliers similar: single uncoated ~0.85,
  # double low-e ~0.40 (baseline), triple low-e ~0.34.
  # Sources: ASHRAE 90.1-2013 Tbl 5.5 + RESNET HERS Reference Home tables.
  TIER_FACTOR = { 'Single Pane' => [2.0, 1.5], 'Double Pane' => [1.0, 1.0], 'Triple Pane' => [0.55, 0.85] }.freeze
  # ASHRAE 90.1-2013 Table 7.8 minimum thermal efficiency for storage water
  # heaters under 75 kBtu/h (gas/propane) and 12 kW (electric):
  #   Gas/Propane storage:    Et = 0.80
  #   Oil-fired storage:      Et = 0.78
  #   Electric storage:       EF ~= 0.93 - 0.00132*V (~0.95 for typical 50 gal)
  # https://www.ashrae.org/technical-resources/bookstore/standard-90-1
  SWH_EFFICIENCY = { 'Electricity' => 0.95, 'NaturalGas' => 0.80, 'FuelOilNo2' => 0.78, 'Propane' => 0.80 }.freeze
  RESIDENTIAL_BUILDING_TYPES = %w[Single-Family\ Detached Single-Family\ Attached Multifamily].freeze

  # Trailing CZ digit. urbanopt emits "ASHRAE 169-2013-4A"; we want "4".
  def self.cz_number(cz) = cz.to_s[/(\d)[A-Cc]?\s*\z/, 1]
  def self.state_to_region(state) = state ? load('census_regions')['state_to_region'][state.to_s.upcase] : nil
  def self.vintage_bin(year)
    y = year.to_i
    return nil if y <= 0
    y < 1980 ? 'pre-1980' : y < 2000 ? '1980-1999' : y < 2010 ? '2000-2009' : '2010+'
  end

  def self.window_props(tier, climate_zone)
    base = CZ_WINDOW[cz_number(climate_zone)] || CZ_WINDOW['4']
    fu, fs = TIER_FACTOR[tier] || [1.0, 1.0]
    [base[:u] * fu, [base[:shgc] * fs, 1.0].min]
  end

  # Residential window U/SHGC (SI W/m2-K) by glazing TYPE. The commercial 90.1
  # CZ_WINDOW table + tier factors don't transfer to residential (factors were
  # calibrated against the looser nonres baseline, so Single Pane came out far
  # tighter than physical). Instead: Single/Triple use physical glazing bounds
  # (single clear ~U-1.04 IP, triple low-e ~U-0.20 IP -- invariant of climate,
  # the type IS the glazing), and the modal Double Pane uses the IECC residential
  # prescriptive value by climate zone (CZ_WINDOW_RES). SI = IP x 5.678263.
  def self.window_props_residential(tier, climate_zone)
    case tier
    when 'Single Pane' then [5.91, 0.63]   # U-1.04 IP, clear single
    when 'Triple Pane' then [1.14, 0.40]   # U-0.20 IP, triple low-e
    else
      b = CZ_WINDOW_RES[cz_number(climate_zone)] || CZ_WINDOW_RES['4']
      [b[:u], b[:shgc]]
    end
  end

  def self.swh_efficiency(fuel) = SWH_EFFICIENCY[fuel] || 0.80
  def self.present?(v) = !(v.nil? || (v.respond_to?(:empty?) && v.empty?))

  # --- Residential HVAC efficiency turnover ------------------------------------
  # Federal minimum standards by effective install year. eff_year is the
  # effective replacement year = [year_built + EUL, sim_year - EUL].max for a
  # building whose original system has aged past its ~20yr EUL. Southern SEER
  # minimums run one tier higher (regional standards). NOTE: the AC and HP SEER
  # ladders differ at the 2015 tier (AC gates the 14 on `southern`, HP does not)
  # -- preserved here exactly as the original inline mapper logic had them.
  def self.furnace_afue(eff_year) = eff_year >= 1992 ? 0.80 : 0.78
  def self.boiler_afue(eff_year) = eff_year >= 2021 ? 0.84 : (eff_year >= 2012 ? 0.82 : 0.80)

  def self.ac_seer(eff_year, southern)
    eff_year >= 2023 ? (southern ? 15 : 14) : (eff_year >= 2015 && southern ? 14 : (eff_year >= 2006 ? 13 : 10))
  end

  def self.hp_seer(eff_year, southern)
    eff_year >= 2023 ? (southern ? 15 : 14) : (eff_year >= 2015 ? 14 : (eff_year >= 2006 ? 13 : 10))
  end

  def self.hp_hspf(eff_year) = eff_year >= 2023 ? 8.8 : (eff_year >= 2015 ? 8.2 : (eff_year >= 2006 ? 7.7 : 6.8))

  # --- Residential envelope cavity-R -> HPXML assembly-R ------------------------
  # recs2020 wall_r_value is CAVITY-only R; HPXML wall_assembly_r is whole-wall
  # assembly R (wood-frame parallel-path: ~0.75 framing derate on cavity + ~2.5
  # series for sheathing/finishes/air films). Ceiling: attic insulation covers
  # the joists, so assembly ~ insulation + ~1.0 finish+film series.
  def self.wall_assembly_r(cavity_r) = (cavity_r.to_f * 0.75 + 2.5).round(2)
  def self.ceiling_assembly_r(roof_r) = (roof_r.to_f + 1.0).round(2)

  # --- Residential attached-garage prevalence (RECS 2020, SFD) ------------------
  GARAGE_PREVALENCE = { 'Northeast' => 0.58, 'Midwest' => 0.73, 'South' => 0.62, 'West' => 0.69 }.freeze
  def self.garage_rate(region) = GARAGE_PREVALENCE.fetch(region.to_s, 0.63)

  # Deterministic attached-garage decision. With a feature_id and stochastic
  # sampling on, hash the id to [0,1) and compare to the regional rate; otherwise
  # majority rule (rate >= 0.5). Same building is always garaged-or-not per run.
  def self.garage_attached?(rate, feature_id, stochastic)
    if feature_id.to_s.empty? || !stochastic
      rate >= 0.5
    else
      Digest::MD5.hexdigest("#{feature_id}:garage")[0, 8].to_i(16).to_f / 0xFFFFFFFF < rate
    end
  end
end
