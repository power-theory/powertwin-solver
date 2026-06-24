# *********************************************************************************
# URBANopt (tm), Copyright (c) Alliance for Sustainable Energy, LLC.
# See also https://github.com/urbanopt/urbanopt-cli/blob/develop/LICENSE.md
# *********************************************************************************

require 'urbanopt/reporting'
require 'openstudio/common_measures'
require 'openstudio/model_articulation'
require 'openstudio/ee_measures'
require 'openstudio/calibration'
require 'openstudio/load_flexibility_measures'
require 'openstudio/geb'

require 'json'
require 'rexml/document'
require 'logger'

# PowerTwin reference-data lookup. Resolves tier names + asset context into
# concrete physical values (U/SHGC, R-value, fuel efficiency) so the custom
# OS measures stay pure appliers. Reads the same JSONs the Python resolver
# uses (solver/upload/reference_data/) for envelope R; window U/SHGC and SWH
# efficiency tables are kept inline here since they don't have JSON equivalents.
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

  def self.swh_efficiency(fuel) = SWH_EFFICIENCY[fuel] || 0.80
  def self.present?(v) = !(v.nil? || (v.respond_to?(:empty?) && v.empty?))
end


module URBANopt
  module Scenario
    class PowerTwinMapper < SimulationMapperBase
      # class level variables
      @@logger = Logger.new($stdout)
      @@instance_lock = Mutex.new
      @@osw = nil
      @@geometry = nil

      def initialize
        # do initialization of class variables in thread safe way
        @@instance_lock.synchronize do
          if @@osw.nil?

            # load the OSW for this class
            osw_path = File.join(File.dirname(__FILE__), 'base_workflow.osw')
            File.open(osw_path, 'r') do |file|
              @@osw = JSON.parse(file.read, symbolize_names: true)
            end

            # add any paths local to the project
            @@osw[:measure_paths] << File.join(File.dirname(__FILE__), '../measures/')
            @@osw[:measure_paths] << File.join(File.dirname(__FILE__), '../resources/residential-measures/resources/hpxml-measures')
            @@osw[:file_paths] << File.join(File.dirname(__FILE__), '../weather/')

            # configures OSW with extension gem paths for measures and files, all extension gems must be
            # required before this
            @@osw = OpenStudio::Extension.configure_osw(@@osw)
          end
        end
      end

      def lookup_building_type(building_type, template, footprint_area, number_of_stories)
        if template.include? 'DEER'
          case building_type
          when 'Education'
            return 'EPr'
          when 'Enclosed mall'
            return 'RtL'
          when 'Food sales'
            return 'Gro'
          when 'Food service'
            return 'RSD'
          when 'Inpatient health care'
            return 'Nrs'
          when 'Laboratory'
            return 'Hsp'
          when 'Lodging'
            return 'Htl'
          when 'Mixed use'
            return 'ECC'
          when 'Mobile Home'
            return 'DMo'
          when 'Multifamily (2 to 4 units)'
            return 'MFm'
          when 'Multifamily (5 or more units)'
            return 'MFm'
          when 'Nonrefrigerated warehouse'
            return 'SUn'
          when 'Nursing'
            return 'Nrs'
          when 'Office'
            if footprint_area
              total_area = footprint_area.to_f * [number_of_stories.to_i, 1].max
              if total_area > 100000
                return 'OfL'
              else
                return 'OfS'
              end
            else
              raise 'footprint_area required to map office building type'
            end
          when 'Outpatient health care'
            return 'Nrs'
          when 'Public assembly'
            return 'Asm'
          when 'Public order and safety'
            return 'Asm'
          when 'Refrigerated warehouse'
            return 'WRf'
          when 'Religious worship'
            return 'Asm'
          when 'Retail other than mall'
            return 'RtS'
          when 'Service'
            return 'MLI'
          when 'Single-Family'
            return 'MFm'
          when 'Strip shopping mall'
            return 'RtL'
          when 'Vacant'
            return 'SUn'
          else
            raise "building type #{building_type} cannot be mapped to a DEER building type"
          end

        else
          # default: ASHRAE
          case building_type
          when 'Education'
            return 'SecondarySchool'
          when 'Enclosed mall'
            return 'RetailStripmall'
          when 'Food sales'
            return 'SuperMarket'
          when 'Food service'
            return 'FullServiceRestaurant'
          when 'Inpatient health care'
            return 'Hospital'
          when 'Laboratory'
            return 'Laboratory'
          when 'Lodging'
            # TODO add 'Dormitory' asset_subtype routed to MidriseApartment.
            # No code-only national fix (DOE plates per story: SmallHotel
            # 10.8k, LargeHotel 20.4k, MidriseApt 8.4k --
            # openei.org/wiki/Commercial_Reference_Buildings).
            if number_of_stories
              if number_of_stories.to_i > 3
                return 'LargeHotel'
              else
                return 'SmallHotel'
              end
            end
            return 'LargeHotel'
          when 'Mixed use'
            return 'Mixed use'
          when 'Mobile Home'
            return 'MidriseApartment'
          when 'Multifamily (2 to 4 units)'
            return 'MidriseApartment'
          when 'Multifamily (5 or more units)'
            return 'MidriseApartment'
          when 'Nonrefrigerated warehouse'
            return 'Warehouse'
          when 'Nursing'
            return 'Outpatient'
          when 'Office'
            if footprint_area
              total_area = footprint_area.to_f * [number_of_stories.to_i, 1].max
              if total_area < 25000
                return 'SmallOffice'
              elsif total_area > 100000
                return 'LargeOffice'
              else
                return 'MediumOffice'
              end
            else
              raise 'Floor area required to map office building type'
            end
          when 'Outpatient health care'
            return 'Outpatient'
          when 'Public assembly'
            return 'MediumOffice'
          when 'Public order and safety'
            return 'MediumOffice'
          when 'Refrigerated warehouse'
            return 'Warehouse'
          when 'Religious worship'
            return 'MediumOffice'
          when 'Retail other than mall'
            return 'RetailStandalone'
          when 'Service'
            return 'MediumOffice'
          when 'Single-Family'
            return 'MidriseApartment'
          when 'Strip shopping mall'
            return 'RetailStripmall'
          when 'Vacant'
            return 'Warehouse'
          else
            raise "building type #{building_type} cannot be mapped to an ASHRAE building type"
          end
        end
      end

      DOE_REF_INCOMPATIBLE = %w[SmallHotel Laboratory].freeze

      def lookup_template_by_year_built(template, year_built, building_type = nil)
        if template.include? 'DEER'
          if year_built <= 1996
            return 'DEER 1985'
          elsif year_built <= 2003
            return 'DEER 1996'
          elsif year_built <= 2007
            return 'DEER 2003'
          elsif year_built <= 2011
            return 'DEER 2007'
          elsif year_built <= 2014
            return 'DEER 2011'
          elsif year_built <= 2015
            return 'DEER 2014'
          elsif year_built <= 2017
            return 'DEER 2015'
          elsif year_built <= 2020
            return 'DEER 2017'
          else
            return 'DEER 2020'
          end
        else
          # SmallHotel and Laboratory lack space types needed by create_bar
          # in DOE Ref templates (e.g. GuestRoom123Occ). All other building
          # types are empirically verified compatible — see tmp/compatibility_matrix.csv.
          can_use_doe_ref = building_type.nil? || !DOE_REF_INCOMPATIBLE.include?(building_type)

          if can_use_doe_ref && year_built < 1980
            return 'DOE Ref Pre-1980'
          elsif can_use_doe_ref && year_built <= 2004
            return 'DOE Ref 1980-2004'
          elsif year_built <= 2007
            return '90.1-2004'
          elsif year_built <= 2010
            return '90.1-2007'
          elsif year_built <= 2013
            return '90.1-2010'
          else
            return '90.1-2013'
          end
        end
      end

      def residential_building_types
        PowerTwinRefs::RESIDENTIAL_BUILDING_TYPES
      end

      def commercial_building_types
        return [
          'Vacant',
          'Office',
          'Laboratory',
          'Nonrefrigerated warehouse',
          'Food sales',
          'Public order and safety',
          'Outpatient health care',
          'Refrigerated warehouse',
          'Religious worship',
          'Public assembly',
          'Education',
          'Food service',
          'Inpatient health care',
          'Nursing',
          'Lodging',
          'Strip shopping mall',
          'Enclosed mall',
          'Retail other than mall',
          'Service',
          'Uncovered Parking',
          'Covered Parking',
          'Mixed use',
          'Multifamily (2 to 4 units)',
          'Multifamily (5 or more units)',
          'Single-Family'
        ]
      end

      def get_climate_zone_iecc(epw)
        headers = CSV.open(epw, 'r', &:first)
        wmo = headers[5]
        zones_csv = Pathname(__FILE__).dirname.parent / 'resources' / 'residential-measures' / 'resources' / 'hpxml-measures' / 'HPXMLtoOpenStudio' / 'resources' / 'data' / 'zipcode_weather_stations.csv'

        # Check if the CSV file is empty
        if File.empty?(epw)
          raise "Error: Your weather file #{epw} is empty."
        end

        CSV.foreach(zones_csv) do |row|
          if row[7].to_s == wmo.to_s
            return row[6].to_s
          end
        end

        return nil
      end

      # epw_state to subregions mapping methods
      # REK: Maybe we can move these method to the geojson gem
      def get_future_emissions_region(feature)
        # Options are: AZNMc, CAMXc, ERCTc, FRCCc, MROEc, MROWc, NEWEc, NWPPc, NYSTc, RFCEc, RFCMc, RFCWc, RMPAc, SPNOc, SPSOc, SRMVc, SRMWc, SRSOc, SRTVc, and SRVCc
        # egrid subregions can map directly to zipcodes but not to states. Some state might include multiple egrid subregions. the default mapper prioritize the egrid subregion that is most common in the state (covers the biggest number of zipcodes)
        future_emissions_mapping_hash =
          { 'FL': 'FRCCc', # ['FRCCc', 'SRSOc']
            'MS': 'SRMVc', # ['SRMVc', 'SRTVc']
            'NE': 'MROWc', # ['MROWc', 'RMPAc']
            'OR': 'NWPPc',
            'CA': 'CAMXc', # ['CAMXc', 'NWPPc']
            'VA': 'SRVCc', # ['SRVCc', 'RFCWc', 'RFCEc'],
            'AR': 'SRMVc', # ['SRMVc', 'SPSOc']
            'TX': 'ERCTc', # ['ERCTc', 'SRMVc', 'SPSOc', 'AZNMc']
            'OH': 'RFCWc',
            'UT': 'NWPPc',
            'MT': 'NWPPc', # ['NWPPc', 'MROWc']
            'TN': 'SRTVc',
            'ID': 'NWPPc',
            'WI': 'MROEc', # ['RFCWc', 'MROEc', 'MROWc']
            'WV': 'RFCWc',
            'NC': 'SRVCc',
            'LA': 'SRMVc',
            'IL': 'SRMWc', # ['RFCWc', 'SRMWc']
            'OK': 'SPSOc',
            'IA': 'MROWc',
            'WA': 'NWPPc',
            'SD': 'MROWc', # ['MROWc', 'RMPAc']
            'MN': 'MROWc',
            'KY': 'SRTVc', # ['SRTVc', 'RFCWc']
            'MI': 'RFCMc', # ['RFCMc', 'MROEc']
            'KS': 'SPNOc',
            'NJ': 'RFCEc',
            'NY': 'NYSTc',
            'IN': 'RFCWc',
            'VT': 'NEWEc',
            'NM': 'AZNMc', # ['AZNMc', 'SPSOc']
            'WY': 'RMPAc', # ['RMPAc', 'NWPPc']
            'GA': 'SRSOc',
            'MO': 'SRMWc', # ['SRMWc', 'SPNOc']
            'DC': 'RFCEc',
            'SC': 'SRVCc',
            'PA': 'RFCEc', # ['RFCEc', 'RFCWc']
            'CO': 'RMPAc',
            'AZ': 'AZNMc',
            'ME': 'NEWEc',
            'AL': 'SRSOc',
            'MD': 'RFCEc', # ['RFCEc', 'RFCWc']
            'NH': 'NEWEc',
            'MA': 'NEWEc',
            'ND': 'MROWc',
            'NV': 'NWPPc', # ['NWPPc', 'AZNMc']
            'CT': 'NEWEc',
            'DE': 'RFCEc',
            'RI': 'NEWEc' }

        # get the state from weather file
        state = feature.weather_filename.split('_', -1)[1]

        # find region input based on the state
        region = future_emissions_mapping_hash[state.to_sym]

        @@logger.warn("emissions_future_subregion for #{state} is assigned to: #{region}. Note: Not all states have a 1 to 1 mapping with a subregion. Some states('ND','IN', 'MN', 'SD', 'IA', 'WV', 'OH', 'NE' ) include 2 subregions.
        The default mapper maps to the subregion that includes the most zipcodes in the corresponding state. You can overwrite this assigned input by specifying the emissions_future_subregion input in the FeatureFile.")

        return region
      end

      def get_hourly_historical_emissions_region(feature)
        # Options are: California, Carolinas, Central, Florida, Mid-Atlantic, Midwest, New England, New York, Northwest, Rocky Mountains, Southeast, Southwest, Tennessee, and Texas
        # There is no "correct" mapping of eGrid to AVERT regions as they are both large geographical areas that partially overlap.
        # Mapping is done using mapping tools from eGrid and AVERT (ZipCode for eGrid and fraction of state for AVERT).
        # Mapped based on the maps of each set of regions:
        hourly_historical_mapping_hash =
          { 'FL': 'Florida',
            'MS': 'Midwest',
            'NE': 'Midwest',  # MRWO could be Midwest / Central
            'OR': 'Northwest',
            'CA': 'California',
            'VA': 'Carolinas',
            'AR': 'Midwest',
            'TX': 'Texas',
            'OH': 'Midwest',  # RFCW could be Midwest / Mid Atlantic
            'UT': 'Northwest',
            'MT': 'Northwest',
            'TN': 'Tennessee',
            'ID': 'Northwest',
            'WI': 'Midwest',
            'WV': 'Midwest', # RFCW could be Midwest / Mid Atlantic
            'NC': 'Carolinas',
            'LA': 'Midwest',
            'IL': 'Midwest',
            'OK': 'Central',
            'IA': 'Midwest', # MRWO could be Midwest / Central
            'WA': 'Northwest',
            'SD': 'Midwest',  # MRWO could be Midwest / Central
            'MN': 'Midwest',  # MRWO could be Midwest / Central
            'KY': 'Tennessee',
            'MI': 'Midwest',
            'KS': 'Central',
            'NJ': 'Mid-Atlantic',
            'NY': 'New York',
            'IN': 'Midwest', # RFCW could be Midwest / Mid Atlantic
            'VT': 'New England',
            'NM': 'Southwest',
            'WY': 'Rocky Mountains',
            'GA': 'Southeast',
            'MO': 'Midwest',
            'DC': 'Mid-Atlantic',
            'SC': 'Carolinas',
            'PA': 'Mid-Atlantic',
            'CO': 'Rocky Mountains',
            'AZ': 'Southwest',
            'ME': 'New England',
            'AL': 'Southeast',
            'MD': 'Mid-Atlantic',
            'NH': 'New England',
            'MA': 'New England',
            'ND': 'Midwest', # MRWO could be Midwest / Central
            'NV': 'Northwest',
            'CT': 'New England',
            'DE': 'Mid-Atlantic',
            'RI': 'New England' }

        # get the state from weather file
        state = feature.weather_filename.split('_', -1)[1]

        # find region input based on the state
        region = hourly_historical_mapping_hash[state.to_sym]
        @@logger.warn("emissions_hourly_historical_subregion for #{state} is assigned to: #{region}. Note: Not all states have a 1 to 1 mapping with a subregion. Some states('ND','IN', 'MN', 'SD', 'IA', 'WV', 'OH', 'NE' ) include 2 subregions.
        The default mapper maps to the subregion that includes the most zipcodes in the corresponding state. You can overwrite this assigned input by specifying the emissions_hourly_historical_subregion input in the FeatureFile.")

        return region
      end

      def get_annual_historical_emissions_region(feature)
        # Options are: AKGD, AKMS, AZNM, CAMX, ERCT, FRCC, HIMS, HIOA, MROE, MROW, NEWE, NWPP, NYCW, NYLI, NYUP, RFCE, RFCM, RFCW, RMPA, SPNO, SPSO, SRMV, SRMW, SRSO, SRTV, and SRVC
        # egrid subregions can map directly to zipcodes but not to states. Some state might include multiple egrid subregions. the default mapper prioritize the egrid subregion that is most common in the state (covers the biggest number of zipcodes)
        annual_historical_mapping_hash =
          { 'FL': 'FRCC',
            'MS': 'SRMV',
            'NE': 'MROW',
            'OR': 'NWPP',
            'CA': 'CAMX',
            'VA': 'SRVC',
            'AR': 'SRMV',
            'TX': 'ERCT',
            'OH': 'RFCW',
            'UT': 'NWPP',
            'MT': 'NWPP',
            'TN': 'SRTV',
            'ID': 'NWPP',
            'WI': 'MROE',
            'WV': 'RFCW',
            'NC': 'SRVC',
            'LA': 'SRMV',
            'IL': 'SRMW',
            'OK': 'SPSO',
            'IA': 'MROW',
            'WA': 'NWPP',
            'SD': 'MROW',
            'MN': 'MROW',
            'KY': 'SRTV',
            'MI': 'RFCM',
            'KS': 'SPNO',
            'NJ': 'RFCE',
            'NY': 'NYCW',
            'IN': 'RFCW',
            'VT': 'NEWE',
            'NM': 'AZNM',
            'WY': 'RMPA',
            'GA': 'SRSO',
            'MO': 'SRMW',
            'DC': 'RFCE',
            'SC': 'SRVC',
            'PA': 'RFCE',
            'CO': 'RMPA',
            'AZ': 'AZNM',
            'ME': 'NEWE',
            'AL': 'SRSO',
            'MD': 'RFCE',
            'NH': 'NEWE',
            'MA': 'NEWE',
            'ND': 'MROW',
            'NV': 'NWPP',
            'CT': 'NEWE',
            'DE': 'RFCE',
            'RI': 'NEWE' }
        # get the state from weather file
        state = feature.weather_filename.split('_', -1)[1]

        # find region input based on the state
        region = annual_historical_mapping_hash[state.to_sym]

        @@logger.warn("electricity_emissions_annual_historical_subregion for #{state} is assigned to: #{region}. Note: Not all states have a 1 to 1 mapping with a subregion. Some states('ND','IN', 'MN', 'SD', 'IA', 'WV', 'OH', 'NE' ) include 2 subregions.
        The default mapper maps to the subregion that includes the most zipcodes in the corresponding state. You can overwrite this assigned input by specifying the electricity_emissions_annual_historical_subregion input in the FeatureFile.")

        return region
      end

      def is_defined(feature, method_name, raise_error = true)
        if feature.method_missing(method_name)
          return true
        end
      rescue NoMethodError
        if raise_error
          raise "*** ERROR *** #{method_name} is not set on this feature"
        end

        return false
      end

      def create_osw(scenario, features, feature_names)
        if features.size != 1
          raise 'Baseline currently cannot simulate more than one feature.'
        end

        feature = features[0]
        feature_id = feature.id
        feature_type = feature.type

        # take the centroid of the vertices as the location of the building
        feature_vertices_coordinates = feature.feature_json[:geometry][:coordinates][0]
        feature_location = feature.find_feature_center(feature_vertices_coordinates).to_s

        feature_name = feature.name
        if feature_names.size == 1
          feature_name = feature_names[0]
        end

        # deep clone of @@osw before we configure it
        osw = Marshal.load(Marshal.dump(@@osw))

        # now we have the feature, we can look up its properties and set arguments in the OSW
        osw[:name] = feature_name
        osw[:description] = feature_name

        if feature_type == 'Building'

          building_type = feature.building_type

          if building_type.nil?
            # need building type
            raise 'Building type is not set'
          end

          if residential_building_types.include? building_type
            # Check for required residential fields
            is_defined(feature, :number_of_stories_above_ground)
            is_defined(feature, :foundation_type)

            if !is_defined(feature, :hpxml_directory, false)
              # check additional fields when HPXML dir is not given
              is_defined(feature, :attic_type)
              is_defined(feature, :number_of_bedrooms)
              if ['Single-Family Attached', 'Multifamily'].include?(building_type)
                is_defined(feature, :number_of_residential_units)
              end
            end

            epw = File.join(File.dirname(__FILE__), '../weather', feature.weather_filename)
            climate_zone = get_climate_zone_iecc(epw)
            if climate_zone.nil?
              abort("Error: No match found for the WMO station from your weather file #{Pathname(epw).expand_path} in our US WMO list.
              This is known to happen when your weather file is from somewhere outside of the United States.
              Please replace your weather file with one from an analogous weather location in the United States.")
            end

            # Start general residential mapping
            # mappers/residential/util.rb
            args = {}
            require File.join(File.dirname(__FILE__), 'residential/util')
            residential(scenario, feature, args, building_type)

            # Then onto optional "template" mapping
            # mappers/residential/template/util.rb
            template = nil
            begin
              template = feature.template
            rescue StandardError
            end

            if !template.nil?
              require File.join(File.dirname(__FILE__), 'residential/template/util')
              residential_template(args, template, climate_zone)
            end

            # Then onto optional "samples" mapping
            # mappers/residential/samples/util.rb
            uo_resstock_connection = false
            begin
              uo_resstock_connection = feature.characterize_residential_buildings_from_buildstock_csv
            rescue StandardError
            end

            # Run workflows if UO-ResStock connection is established
            if uo_resstock_connection

              buildstock_csv_path = nil
              begin
                csv_path = feature.resstock_buildstock_csv_path
                buildstock_csv_path = File.absolute_path(File.join(File.dirname(__FILE__), '..', csv_path))
              rescue StandardError
                @@logger.error("\n resstock_buildstock_csv_path was not assigned by the user.")
              end

              uo_buildstock_mapping_csv_path = nil
              begin
                mapping_csv_path = feature.uo_buildstock_mapping_csv_path
                uo_buildstock_mapping_csv_path = File.absolute_path(File.join(File.dirname(__FILE__), '..', mapping_csv_path))
              rescue StandardError
                @@logger.error("\n uo_buildstock_mapping_csv_path was not assigned by the user")
              end

              require File.join(File.dirname(__FILE__), 'residential/samples/util')
              if !buildstock_csv_path.nil? # If resstock_buildstock_csv_path is provided
                @@logger.info("Processing with BuildStock CSV path.")

                start_time = Time.now # To document the time of finding the resstock building id
                resstock_building_id = find_resstock_building_id(buildstock_csv_path, feature, building_type, @@logger)
                puts "Processing time for finding a building match (resstock_building_id = #{resstock_building_id}) from the buildstock CSV: #{Time.now - start_time} seconds."

                residential_samples(args, resstock_building_id, buildstock_csv_path)

              elsif !uo_buildstock_mapping_csv_path.nil? # If uo_buildstock_mapping_csv_path is provided
                @@logger.info("Processing with UO-BuildStock mapping CSV path.")

                start_time = Time.now # To document the time of getting the resstock building id
                resstock_building_id = find_building_for_uo_id(uo_buildstock_mapping_csv_path, feature.id)
                puts "Processing time for finding the building match (resstock_building_id = #{resstock_building_id}) from the buildstock CSV: #{Time.now - start_time} seconds."

                residential_samples(args, resstock_building_id, uo_buildstock_mapping_csv_path) # uo_buildstock_mapping_csv_path may contain a subset of all parameters

              else
                @@logger.error("The user did not specify either the uo_buildstock_mapping_csv_path or the resstock_buildstock_csv_path. At least one of these is required for UO - ResStock connection.")
              end
            end

            # Dynamic-defaults parity: feed resolver envelope/WWR/window/occupants to the residential measure (gated so OFF keeps HPXML defaults).
            res_props = feature.feature_json[:properties] || {}
            if res_props[:dynamic_defaults]
              if PowerTwinRefs.present?(res_props[:window_to_wall_ratio])
                res_wwr = res_props[:window_to_wall_ratio].to_f
                %i[window_front_wwr window_back_wwr window_left_wwr window_right_wwr].each { |k| args[k] = res_wwr }
              end
              args[:geometry_unit_num_occupants] = res_props[:number_of_occupants].to_i if PowerTwinRefs.present?(res_props[:number_of_occupants])
              res_tier = PowerTwinRefs.present?(res_props[:window_type]) ? res_props[:window_type] :
                         ((res_props[:windows] || [{}]).first || {})[:window_type]
              if PowerTwinRefs.present?(res_tier)
                u, shgc = PowerTwinRefs.window_props(res_tier, climate_zone)
                # window_props U is SI (W/m2-K, for OpenStudio SimpleGlazing); HPXML window_ufactor is IP.
                args[:window_ufactor] = (u / 5.678263).round(3)
                args[:window_shgc] = shgc.round(3)
              end
              res_constr = res_props[:constructions] || {}
              res_wall_r = (res_constr[:wall] || {})[:r_value]
              res_roof_r = (res_constr[:roof] || {})[:r_value]
              args[:wall_assembly_r] = res_wall_r.to_f if PowerTwinRefs.present?(res_wall_r)
              # roof_r_value is ceiling insulation (recs2020); ceiling_assembly_r is the attic R.
              args[:ceiling_assembly_r] = res_roof_r.to_f if PowerTwinRefs.present?(res_roof_r)
              # Heating system type drives HPXML args. The type determines
              # valid fuels -- system type and fuel must be correlated.
              sys_type = res_props[:heating_system_type].to_s.downcase
              case sys_type
              when 'heat_pump'
                args[:heat_pump_type] = 'air-to-air'
                args[:heating_system_type] = 'none'
                args[:cooling_system_type] = 'none'
                args[:heat_pump_backup_type] = 'integrated'
                args[:heat_pump_backup_fuel] = 'electricity'
              when 'boiler'
                args[:heating_system_type] = 'Boiler'
                htg_fuel = res_props[:heating_system_fuel_type].to_s.downcase
                htg_fuel = 'natural gas' if %w[electricity wood].include?(htg_fuel)
                args[:heating_system_fuel] = htg_fuel
              when 'electric_resistance'
                args[:heating_system_type] = 'ElectricResistance'
                args[:heating_system_fuel] = 'electricity'
              when 'wood_stove'
                args[:heating_system_type] = 'Stove'
                args[:heating_system_fuel] = 'wood'
              else
                args[:heating_system_type] = 'Furnace'
                if PowerTwinRefs.present?(res_props[:heating_system_fuel_type])
                  args[:heating_system_fuel] = res_props[:heating_system_fuel_type].to_s.downcase
                end
              end
              if sys_type != 'heat_pump' && res_props[:cooling_system_fuel_type].to_s.downcase == 'none'
                args[:cooling_system_type] = 'none'
              end
              if PowerTwinRefs.present?(res_props[:service_water_heating_fuel_type])
                args[:water_heater_fuel_type] = res_props[:service_water_heating_fuel_type].to_s
              end
              if PowerTwinRefs.present?(res_props[:water_heater_type])
                wh_type = res_props[:water_heater_type].to_s.downcase
                if %w[storage\ water\ heater instantaneous\ water\ heater heat\ pump\ water\ heater].include?(wh_type)
                  args[:water_heater_type] = wh_type
                end
              end
            end

            # Parse BuildResidentialHPXML measure xml so we can fill "args" in with default values where keys aren't already assigned
            default_args = {}
            measures_dir = File.absolute_path(File.join(File.dirname(__FILE__), '../resources/residential-measures/resources/hpxml-measures'))
            measure_xml = File.read(File.join(measures_dir, 'BuildResidentialHPXML', 'measure.xml'))
            measure = REXML::Document.new(measure_xml).root
            measure.elements.each('arguments/argument') do |arg|
              arg_name = arg.elements['name'].text.to_sym

              default_args[arg_name] = nil
              if arg.elements['default_value']
                arg_default = arg.elements['default_value'].text
                default_args[arg_name] = arg_default
              end
            end

            build_res_model_args = [:urbanopt_feature_id, :resstock_buildstock_csv_path, :resstock_building_id, :schedules_type, :schedules_random_seed, :schedules_variation, :geometry_num_floors_above_grade, :hpxml_dir, :output_dir]
            args.each_key do |arg_name|
              unless default_args.key?(arg_name)
                next if build_res_model_args.include?(arg_name)

                puts "Argument '#{arg_name}' is unknown."
              end
            end

            debug = false
            default_args.each do |arg_name, arg_default|
              next if arg_default.nil?

              if !args.key?(arg_name)
                args[arg_name] = arg_default
              else
                if debug
                  if !arg_default.nil?
                    if args[arg_name] != arg_default
                      puts "Overriding #{arg_name} default '#{arg_default}' with '#{args[arg_name]}'."
                    end
                  else
                    puts "Setting #{arg_name} to '#{args[arg_name]}'."
                  end
                end
              end
            end

            OpenStudio::Extension.set_measure_argument(osw, 'BuildResidentialModel', '__SKIP__', false)
            args.each_key do |arg_name|
              OpenStudio::Extension.set_measure_argument(osw, 'BuildResidentialModel', arg_name, args[arg_name])
            end

          elsif commercial_building_types.include? building_type
            # set_run_period
            OpenStudio::Extension.set_measure_argument(osw, 'set_run_period', '__SKIP__', false)
            # can enable reporting (commercial building types only for now)
            # OpenStudio::Extension.set_measure_argument(osw, 'openstudio_results', '__SKIP__', false)
            # OpenStudio::Extension.set_measure_argument(osw, 'envelope_and_internal_load_breakdown', '__SKIP__', false)
            OpenStudio::Extension.set_measure_argument(osw, 'generic_qaqc', '__SKIP__', false)
            
            # Added for PowerTwin
            OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', '__SKIP__', false)
            OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'start_date1', '05-01')
            OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'end_date1', '09-30')
            OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'start_time1', '00:00:00')
            OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'end_time1', '23:59:59')
            OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'epd_reduce_percent', 0)


            begin
              timesteps_per_hour = feature.timesteps_per_hour
              if timesteps_per_hour
                OpenStudio::Extension.set_measure_argument(osw, 'set_run_period', 'timesteps_per_hour', timesteps_per_hour)
              else
                puts 'No timesteps_per_hours set in the feature file...using default'
              end
            rescue StandardError
            end
            begin
              begin_date = feature.begin_date
              if begin_date
                # check date-only YYYY-MM-DD
                if begin_date.length > 10
                  begin_date = begin_date[0, 10]
                end
                OpenStudio::Extension.set_measure_argument(osw, 'set_run_period', 'begin_date', begin_date)
              else
                puts 'no simulation begin_date set in the feature file...using default'
              end
            rescue StandardError
            end
            begin
              end_date = feature.end_date
              if end_date
                # check date-only YYYY-MM-DD
                if end_date.length > 10
                  end_date = end_date[0, 10]
                end
                OpenStudio::Extension.set_measure_argument(osw, 'set_run_period', 'end_date', end_date)
              else
                puts 'no simulation end_date set in the feature file...using default'
              end
            rescue StandardError
            end

            # convert to hash
            building_hash = feature.to_hash
            OpenStudio::Extension.set_measure_argument(osw, 'PredictedMeanVote', '__SKIP__', false)

            # Changing location here means we always read the geojson weather file, no matter what.
            OpenStudio::Extension.set_measure_argument(osw, 'ChangeBuildingLocation', '__SKIP__', false)

            # check for detailed model filename
            if building_hash.key?(:detailed_model_filename)
              detailed_model_filename = building_hash[:detailed_model_filename]
              osw[:file_paths] << File.join(File.dirname(__FILE__), '../osm_building/')
              osw[:seed_file] = detailed_model_filename

              # skip PMV measure with detailed models:
              OpenStudio::Extension.set_measure_argument(osw, 'PredictedMeanVote', '__SKIP__', true)

            # For when the user DIDN'T BYO osm file
            else

              building_type_1 = building_hash[:building_type]

              # lookup/map building type
              number_of_stories = building_hash[:number_of_stories]
              if building_hash.key?(:number_of_stories_above_ground)
                number_of_stories_above_ground = building_hash[:number_of_stories_above_ground]
                number_of_stories_below_ground = number_of_stories - number_of_stories_above_ground
              else
                number_of_stories_above_ground = number_of_stories
                number_of_stories_below_ground = 0
              end
              template = building_hash.key?(:template) ? building_hash[:template] : nil
              if template.nil?
                raise 'Template is not defined in the feature file'
              end

              footprint_area = building_hash[:footprint_area]

              mapped_building_type_1 = lookup_building_type(building_type_1, template, footprint_area, number_of_stories)

              # process Mixed Use (for create_bar measure)
              if building_type_1 == 'Mixed use'
                # map mixed use types
                running_fraction = 0
                mixed_type_1 = building_hash[:mixed_type_1]
                mixed_type_2 = building_hash.key?(:mixed_type_2) ? building_hash[:mixed_type_2] : nil
                unless mixed_type_2.nil?
                  mixed_type_2_percentage = building_hash[:mixed_type_2_percentage]
                  mixed_type_2_fract_bldg_area = mixed_type_2_percentage * 0.01
                  running_fraction += mixed_type_2_fract_bldg_area
                end

                mixed_type_3 = building_hash.key?(:mixed_type_3) ? building_hash[:mixed_type_3] : nil
                unless mixed_type_3.nil?
                  mixed_type_3_percentage = building_hash[:mixed_type_3_percentage]
                  mixed_type_3_fract_bldg_area = mixed_type_3_percentage * 0.01
                  running_fraction += mixed_type_3_fract_bldg_area
                end

                mixed_type_4 = building_hash.key?(:mixed_type_4) ? building_hash[:mixed_type_4] : nil
                unless mixed_type_4.nil?
                  mixed_type_4_percentage = building_hash[:mixed_type_4_percentage]
                  mixed_type_4_fract_bldg_area = mixed_type_4_percentage * 0.01
                  running_fraction += mixed_type_4_fract_bldg_area
                end

                # potentially calculate from other inputs
                mixed_type_1_fract_bldg_area = building_hash.key?(:mixed_type_1_percentage) ? building_hash[:mixed_type_1_percentage] : (1 - running_fraction)

                # lookup mixed_use types
                footprint_1 = footprint_area * mixed_type_1_fract_bldg_area
                openstudio_mixed_type_1 = lookup_building_type(mixed_type_1, template, footprint_1, number_of_stories)
                unless mixed_type_2.nil?
                  footprint_2 = footprint_area * mixed_type_2_fract_bldg_area
                  openstudio_mixed_type_2 = lookup_building_type(mixed_type_2, template, footprint_2, number_of_stories)
                end
                unless mixed_type_3.nil?
                  footprint_3 = footprint_area * mixed_type_3_fract_bldg_area
                  openstudio_mixed_type_3 = lookup_building_type(mixed_type_3, template, footprint_3, number_of_stories)
                end
                unless mixed_type_4.nil?
                  footprint_4 = footprint_area * mixed_type_4_fract_bldg_area
                  openstudio_mixed_type_4 = lookup_building_type(mixed_type_4, template, footprint_4, number_of_stories)
                end
              end

              # Per-feature override; fall back to 9 ft (matches SIM_PARAM_DEFAULTS).
              # Read from raw feature.json properties; Building#to_hash drops floor_height.
              raw_props_fh = feature.feature_json[:properties] || {}
              floor_height = PowerTwinRefs.present?(raw_props_fh[:floor_height]) ? raw_props_fh[:floor_height].to_f : 9
              # Map system type to openstudio system types
              # TODO: Map all system types
              if building_hash.key?(:system_type)
                system_type = building_hash[:system_type]
                case system_type
                when 'Fan coil district hot and chilled water'
                  system_type = 'Fan coil district chilled water with district hot water'
                when 'Fan coil air-cooled chiller and boiler'
                  system_type = 'Fan coil air-cooled chiller with boiler'
                when 'VAV with gas reheat'
                  system_type = 'VAV air-cooled chiller with gas boiler reheat'
                end
              else
                system_type = 'Inferred'
              end

              def time_mapping(time)
                hour = time.split(':')[0]
                minute = time.split(':')[1]
                fraction = minute.to_f / 60
                fraction_roundup = fraction.round(2)
                minute_fraction = fraction_roundup.to_s.split('.')[1]
                new_time = [hour, minute_fraction].join('.')
                return new_time
              end

              # cec climate zone takes precedence
              cec_found = false
              begin
                cec_climate_zone = feature.cec_climate_zone
                if !cec_climate_zone.empty?
                  cec_climate_zone = "CEC T24-CEC#{cec_climate_zone}"
                  OpenStudio::Extension.set_measure_argument(osw, 'ChangeBuildingLocation', 'climate_zone', cec_climate_zone)
                  cec_found = true
                end
              rescue StandardError
              end
              if !cec_found
                begin
                  climate_zone = feature.climate_zone
                  if !climate_zone.empty?
                    climate_zone = "ASHRAE 169-2013-#{climate_zone}"
                    OpenStudio::Extension.set_measure_argument(osw, 'ChangeBuildingLocation', 'climate_zone', climate_zone)
                  end
                rescue StandardError
                end
              end

              # set weather file
              begin
                weather_filename = feature.weather_filename
                if !feature.weather_filename.nil? && !feature.weather_filename.empty?
                  OpenStudio::Extension.set_measure_argument(osw, 'ChangeBuildingLocation', 'weather_file_name', weather_filename)
                  puts "Setting weather_file_name to #{weather_filename} as specified in the FeatureFile"
                end
              rescue StandardError
                puts 'No weather_file specified on feature'
                epw_file_path = Dir.glob(File.join(File.dirname(__FILE__), '../weather/*.epw'))[0]
                if !epw_file_path.nil? && !epw_file_path.empty?
                  epw_file_name = File.basename(epw_file_path)
                  OpenStudio::Extension.set_measure_argument(osw, 'ChangeBuildingLocation', 'weather_file_name', epw_file_name)
                  puts "Setting weather_file_name to first epw file found in the weather folder: #{epw_file_name}"
                else
                  puts 'NO WEATHER FILES SPECIFIED...SIMULATIONS MAY FAIL'
                end
              end

              # geojson schema doesn't have modify_wkdy_op_hrs and modify_wknd_op_hrs checking for both start and duration to set to true in osw
              weekday_flag = 0 # set modify arg to true of this gets to 2
              weekend_flag = 0 # set modify arg to true of this gets to 2

              # set weekday start time
              begin
                weekday_start_time = feature.weekday_start_time
                if !feature.weekday_start_time.empty?
                  new_weekday_start_time = time_mapping(weekday_start_time)
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'wkdy_op_hrs_start_time', new_weekday_start_time, 'create_typical_building_from_model 1')
                  weekday_flag += 1
                end
              rescue StandardError
              end

              # set weekday duration
              begin
                weekday_duration = feature.weekday_duration
                if !feature.weekday_duration.empty?
                  new_weekday_duration = time_mapping(weekday_duration)
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'wkdy_op_hrs_duration', new_weekday_duration, 'create_typical_building_from_model 1')
                  weekday_flag += 1
                end
              rescue StandardError
              end

              # set weekday modify
              begin
                if weekday_flag == 2
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'modify_wkdy_op_hrs', true, 'create_typical_building_from_model 1')
                end
              rescue StandardError
              end

              # set weekend start time
              begin
                weekend_start_time = feature.weekend_start_time
                if !feature.weekend_start_time.empty?
                  new_weekend_start_time = time_mapping(weekend_start_time)
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'wknd_op_hrs_start_time', new_weekend_start_time, 'create_typical_building_from_model 1')
                  weekend_flag += 1
                end
              rescue StandardError
              end

              # set weekend duration
              begin
                weekend_duration = feature.weekend_duration
                if !feature.weekend_duration.empty?
                  new_weekend_duration = time_mapping(weekend_duration)
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'wknd_op_hrs_duration', new_weekend_duration, 'create_typical_building_from_model 1')
                  weekend_flag += 1
                end
              rescue StandardError
              end

              # set weekday modify
              begin
                if weekend_flag == 2
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'modify_wknd_op_hrs', true, 'create_typical_building_from_model 1')
                end
              rescue StandardError
              end

              # template
              begin
                new_template = nil
                template = feature.template
                year_built = feature.feature_json[:properties][:year_built]

                # For Mixed Use, the template applies to all component types —
                # fall back to 90.1 if ANY component is DOE-Ref-incompatible.
                if building_type_1 == 'Mixed use'
                  all_bar_types = [openstudio_mixed_type_1]
                  all_bar_types << openstudio_mixed_type_2 unless mixed_type_2.nil?
                  all_bar_types << openstudio_mixed_type_3 unless mixed_type_3.nil?
                  all_bar_types << openstudio_mixed_type_4 unless mixed_type_4.nil?
                  incompatible = all_bar_types.find { |t| DOE_REF_INCOMPATIBLE.include?(t) }
                  template_building_type = incompatible || all_bar_types.first
                else
                  template_building_type = mapped_building_type_1
                end

                # can we override template with year_built info? (keeping same template family)
                if !year_built.nil? && !feature.template.empty?
                  new_template = lookup_template_by_year_built(template, year_built, template_building_type)
                elsif !feature.template.empty?
                  new_template = template
                end

                if new_template
                  OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'template', new_template)
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'template', new_template, 'create_typical_building_from_model 1')
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'template', new_template, 'create_typical_building_from_model 2')
                end
              rescue StandardError
              end

              # Building#to_hash only emits a hardcoded subset of fields; read the
              # raw feature.json properties (symbol-keyed) for everything else.
              props = feature.feature_json[:properties] || {}

              # Fuel overrides into create_typical_building_from_model htg_src/clg_src.
              # urbanopt's measure.xml enumerates the acceptable values per arg:
              #   htg_src: Electricity, NaturalGas, DistrictHeating, DistrictAmbient, Inferred
              #   clg_src: Electricity, DistrictCooling, DistrictAmbient, Inferred
              # Anything else (FuelOil, Propane, NaturalGas-for-cooling) gets silently
              # dropped so the OSW stays valid.
              fuel_map = { 'electricity' => 'Electricity', 'natural gas' => 'NaturalGas',
                           'fuel oil' => 'NaturalGas', 'propane' => 'NaturalGas', 'wood' => 'NaturalGas' }
              valid_src = { htg_src: %w[Electricity NaturalGas DistrictHeating DistrictAmbient Inferred],
                            clg_src: %w[Electricity DistrictCooling DistrictAmbient Inferred] }
              %i[heating_system_fuel_type cooling_system_fuel_type].each do |field|
                next unless PowerTwinRefs.present?(props[field])
                mapped = fuel_map[props[field].to_s.downcase]
                arg = field == :heating_system_fuel_type ? :htg_src : :clg_src
                next unless mapped && valid_src[arg].include?(mapped)
                ['create_typical_building_from_model 1', 'create_typical_building_from_model 2'].each do |step|
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', arg.to_s, mapped, step)
                end
              end

              # Asset context for the reference-data lookups below.
              state   = (feature.weather_filename.to_s.split('_', -1)[1] rescue nil)
              region  = PowerTwinRefs.state_to_region(state)
              vintage = PowerTwinRefs.vintage_bin(year_built)

              # Envelope: CBECS-direct explicit R-value via IncreaseInsulation with
              # allow_reduction=true so the measure can drop walls/roofs below the
              # template prescriptive baseline for pre-1980 stock as well as raise
              # them for newer stock. The CBECS tier label (wall_material /
              # roof_material) is no longer consumed -- it disagreed with the
              # CBECS explicit R-value at the pre-1980 and 2010+ extremes because
              # the tier multipliers came from residential ResStock/NEEA data.
              [[:wall, 'IncreaseInsulationRValueForExteriorWalls'],
               [:roof, 'IncreaseInsulationRValueForRoofs']].each do |surface, inc_m|
                r_field = "#{surface}_r_value".to_sym
                constr = (props[:constructions] || {})[surface] || {}
                r = PowerTwinRefs.present?(props[r_field]) ? props[r_field] :
                    PowerTwinRefs.present?(constr[:r_value]) ? constr[:r_value] : nil
                next unless r
                OpenStudio::Extension.set_measure_argument(osw, inc_m, '__SKIP__', false)
                OpenStudio::Extension.set_measure_argument(osw, inc_m, 'r_value', r)
                OpenStudio::Extension.set_measure_argument(osw, inc_m, 'allow_reduction', true)
              end

              # Window: tier + climate_zone -> (U-factor, SHGC).
              window_tier = PowerTwinRefs.present?(props[:window_type]) ? props[:window_type] :
                            ((props[:windows] || [{}]).first || {})[:window_type]
              if PowerTwinRefs.present?(window_tier)
                u, shgc = PowerTwinRefs.window_props(window_tier, climate_zone)
                OpenStudio::Extension.set_measure_argument(osw, 'set_window_construction', '__SKIP__', false)
                OpenStudio::Extension.set_measure_argument(osw, 'set_window_construction', 'u_factor', u)
                OpenStudio::Extension.set_measure_argument(osw, 'set_window_construction', 'shgc', shgc)
              end

              # SWH fuel: prefer create_typical's swh_src (the toolchain lever).
              # Custom measure only kicks in for fuels swh_src doesn't accept.
              if PowerTwinRefs.present?(props[:service_water_heating_fuel_type])
                ep_fuel = fuel_map[props[:service_water_heating_fuel_type].to_s.downcase]
                case ep_fuel
                when 'Electricity', 'NaturalGas', 'HeatPump'
                  OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'swh_src', ep_fuel, 'create_typical_building_from_model 1')
                when 'FuelOil', 'Propane'
                  # WaterHeater:Mixed uses 'FuelOilNo2' (E+ naming); htg_src uses 'FuelOil'.
                  e_fuel = ep_fuel == 'FuelOil' ? 'FuelOilNo2' : 'Propane'
                  OpenStudio::Extension.set_measure_argument(osw, 'set_service_water_heating_fuel', '__SKIP__', false)
                  OpenStudio::Extension.set_measure_argument(osw, 'set_service_water_heating_fuel', 'fuel', e_fuel)
                  OpenStudio::Extension.set_measure_argument(osw, 'set_service_water_heating_fuel', 'thermal_efficiency', PowerTwinRefs.swh_efficiency(e_fuel))
                end
              end

              # Occupants: pure pass-through; measure rescales to target_total.
              if PowerTwinRefs.present?(props[:number_of_occupants])
                OpenStudio::Extension.set_measure_argument(osw, 'set_people_per_floor_area', '__SKIP__', false)
                OpenStudio::Extension.set_measure_argument(osw, 'set_people_per_floor_area', 'target_total', props[:number_of_occupants].to_i)
              end

              # WWR: set at geometry-creation time on create_bar; post-geometry mutation does not stick.
              if PowerTwinRefs.present?(props[:window_to_wall_ratio])
                OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'wwr', props[:window_to_wall_ratio].to_f)
              end

              # TODO: surface_elevation, tariff_filename: no mapping yet.

              # create a bar building, will have spaces tagged with individual space types given the
              # input building types
              # set skip measure to false
              OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', '__SKIP__', false)
              OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'single_floor_area', footprint_area)
              OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'floor_height', floor_height)
              OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'num_stories_above_grade', number_of_stories_above_ground)
              OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'num_stories_below_grade', number_of_stories_below_ground)

              OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'bldg_type_a', mapped_building_type_1)

              if building_type_1 == 'Mixed use'

                OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'bldg_type_a', openstudio_mixed_type_1)

                unless mixed_type_2.nil?
                  OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'bldg_type_b', openstudio_mixed_type_2)
                  OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'bldg_type_b_fract_bldg_area', mixed_type_2_fract_bldg_area)
                end
                unless mixed_type_3.nil?
                  OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'bldg_type_c', openstudio_mixed_type_3)
                  OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'bldg_type_c_fract_bldg_area', mixed_type_3_fract_bldg_area)
                end
                unless mixed_type_4.nil?
                  OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'bldg_type_d', openstudio_mixed_type_4)
                  OpenStudio::Extension.set_measure_argument(osw, 'create_bar_from_building_type_ratios', 'bldg_type_d_fract_bldg_area', mixed_type_4_fract_bldg_area)
                end
              end

              # calling create typical building the first time will create space types
              OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', '__SKIP__', false)
              OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'add_hvac', false, 'create_typical_building_from_model 1')

              # create a blended space type for each story
              OpenStudio::Extension.set_measure_argument(osw,
                                                         'blended_space_type_from_model', '__SKIP__', false)
              OpenStudio::Extension.set_measure_argument(osw,
                                                         'blended_space_type_from_model', 'blend_method', 'Building Story')

              # create geometry for the desired feature, this will reuse blended space types in the model for each story and remove the bar geometry
              OpenStudio::Extension.set_measure_argument(osw, 'urban_geometry_creation_zoning', '__SKIP__', false)
              OpenStudio::Extension.set_measure_argument(osw, 'urban_geometry_creation_zoning', 'geojson_file', scenario.feature_file.path)
              OpenStudio::Extension.set_measure_argument(osw, 'urban_geometry_creation_zoning', 'feature_id', feature_id)
              OpenStudio::Extension.set_measure_argument(osw, 'urban_geometry_creation_zoning', 'surrounding_buildings', 'ShadingOnly')

              # call create typical building a second time, do not touch space types, only add hvac
              OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', '__SKIP__', false)
              OpenStudio::Extension.set_measure_argument(osw, 'create_typical_building_from_model', 'system_type', system_type, 'create_typical_building_from_model 2')
            end

            OpenStudio::Extension.set_measure_argument(osw, 'export_time_series_modelica', '__SKIP__', false)
            OpenStudio::Extension.set_measure_argument(osw, 'export_modelica_loads', '__SKIP__', false)
          else
            raise "Building type #{building_type} not currently supported."
          end

        end

        ######## Emissions Addition from add_ems_emissions_reporting
        if feature_type == 'Building'

          # emissions options
          future_regions = ['AZNMc', 'CAMXc', 'ERCTc', 'FRCCc', 'MROEc', 'MROWc', 'NEWEc', 'NWPPc', 'NYSTc', 'RFCEc', 'RFCMc', 'RFCWc', 'RMPAc', 'SPNOc', 'SPSOc', 'SRMVc', 'SRMWc', 'SRSOc', 'SRTVc', 'SRVCc']
          hourly_historical_regions = ['California', 'Carolinas', 'Central', 'Florida', 'Mid-Atlantic', 'Midwest', 'New England', 'New York', 'Northwest', 'Rocky Mountains', 'Southeast', 'Southwest', 'Tennessee', 'Texas']
          annual_historical_regions = ['AKGD', 'AKMS', 'AZNM', 'CAMX', 'ERCT', 'FRCC', 'HIMS', 'HIOA', 'MROE', 'MROW', 'NEWE', 'NWPP', 'NYCW', 'NYLI', 'NYUP', 'RFCE', 'RFCM', 'RFCW', 'RMPA', 'SPNO', 'SPSO', 'SRMV', 'SRMW', 'SRSO', 'SRTV', 'SRVC']
          annual_historical_years = ['2007', '2009', '2010', '2012', '2014', '2016', '2018', '2019']
          future_years = ['2020', '2022', '2024', '2026', '2028', '2030', '2032', '2034', '2036', '2038', '2040', '2042', '2044', '2046', '2048', '2050']
          hourly_historical_years = ['2019']

          # add Emissions
          emissions = nil

          begin
            emissions = feature.emissions
          rescue StandardError
          end

          if emissions != true
            @@logger.info('Emissions is not activated for this feature. Please set emissions to true in the the Feature properties in the GeoJSON file to add emissions results.')

          elsif emissions == true

            # activate emissions measure
            OpenStudio::Extension.set_measure_argument(osw, 'add_ems_emissions_reporting', '__SKIP__', false)

            # get emissions inputs if they are available or get them from the mapping methods if the are not
            begin
              electricity_emissions_future_subregion = feature.electricity_emissions_future_subregion
            rescue StandardError
              @@logger.info("\nelectricity_emission_future_subregion is not assigned for feature #{feature_id}. Defining subregion based on the State....")
              electricity_emissions_future_subregion = get_future_emissions_region(feature)
            end

            begin
              electricity_emissions_hourly_historical_subregion = feature.electricity_emissions_hourly_historical_subregion
            rescue StandardError
              @@logger.info("\nelectricity_emissions_hourly_historical_subregion is not assigned for feature #{feature_id}. Defining subregion based on the State....")
              electricity_emissions_hourly_historical_subregion = get_hourly_historical_emissions_region(feature)
            end

            begin
              electricity_emissions_annual_historical_subregion = feature.electricity_emissions_annual_historical_subregion
            rescue StandardError
              @@logger.info("\nelectricity_emissions_annual_historical_subregion is not assigned for feature #{feature_id}. Defining subregion based on the State....")
              electricity_emissions_annual_historical_subregion = get_annual_historical_emissions_region(feature)
            end

            begin
              electricity_emissions_future_year = feature.electricity_emissions_future_year
            rescue StandardError
              @@logger.info("\nelectricity_emissions_future_year was not assigned by the user. The assigned default value is 2030")
              electricity_emissions_future_year = '2030'
            end

            begin
              electricity_emissions_hourly_historical_year = feature.electricity_emissions_hourly_historical_year
            rescue StandardError
              @@logger.info("\nelectricity_emissions_hourly_historical_year was not assigned by the user. The assigned default value is 2019")
              electricity_emissions_hourly_historical_year = '2019'
            end

            begin
              electricity_emissions_annual_historical_year = feature.electricity_emissions_annual_historical_year
            rescue StandardError
              @@logger.info("\nelectricity_emissions_annual_historical_year was not assigned by the user. The assigned default value is 2019")
              electricity_emissions_annual_historical_year = '2019'
            end

            # puts "\n building #{feature_id} emission inputs summarry:
            # electricity_emissions_future_subregion = #{electricity_emissions_future_subregion};
            #   electricity_emissions_hourly_historical_subregion = #{electricity_emissions_hourly_historical_subregion};
            #   electricity_emissions_annual_historical_subregion = #{electricity_emissions_annual_historical_subregion};
            #   electricity_emissions_future_year = #{electricity_emissions_future_year};
            #   electricity_emissions_hourly_historical_year = #{electricity_emissions_hourly_historical_year};
            #   electricity_emissions_annual_historical_year = #{electricity_emissions_annual_historical_year}\n "

            ## Assign the OS measure arguments
            begin
              # emissions_future_subregion
              if !electricity_emissions_future_subregion.nil? && !electricity_emissions_future_subregion.empty?
                if future_regions.include? electricity_emissions_future_subregion
                  OpenStudio::Extension.set_measure_argument(osw, 'add_ems_emissions_reporting', 'future_subregion', electricity_emissions_future_subregion)
                else
                  @@logger.error(" '#{electricity_emissions_future_subregion}' is not valid option for electricity_emissions_future_subregion. Please choose an input from #{future_regions}")
                end
              end

              # hourly_historical_subregion
              if !electricity_emissions_hourly_historical_subregion.nil? && !electricity_emissions_hourly_historical_subregion.empty?
                if hourly_historical_regions.include? electricity_emissions_hourly_historical_subregion
                  OpenStudio::Extension.set_measure_argument(osw, 'add_ems_emissions_reporting', 'hourly_historical_subregion', electricity_emissions_hourly_historical_subregion)
                else
                  @@logger.error(" '#{electricity_emissions_hourly_historical_subregion}' is not valid option for electricity_emissions_hourly_historical_subregion. Please choose an input from #{hourly_historical_regions}")
                end
              end

              # annual_historical_subregion
              if !electricity_emissions_annual_historical_subregion.nil? && !electricity_emissions_annual_historical_subregion.empty?
                if annual_historical_regions.include? electricity_emissions_annual_historical_subregion
                  OpenStudio::Extension.set_measure_argument(osw, 'add_ems_emissions_reporting', 'annual_historical_subregion', electricity_emissions_annual_historical_subregion)
                else
                  @@logger.error(" '#{electricity_emissions_annual_historical_subregion}' is not valid option for electricity_emissions_annual_historical_subregion. Please choose an input from #{annual_historical_regions}")
                end
              end

              # future_year
              if !electricity_emissions_future_year.nil? && !electricity_emissions_future_year.empty?

                if future_years.include? electricity_emissions_future_year
                  OpenStudio::Extension.set_measure_argument(osw, 'add_ems_emissions_reporting', 'future_year', electricity_emissions_future_year)
                else
                  @@logger.error(" '#{electricity_emissions_future_year}' is not valid option for electricity_emissions_future_year. Please choose an input from #{future_years}")
                end
              end

              # hourly_historical_year
              if !electricity_emissions_hourly_historical_year.nil? && !electricity_emissions_hourly_historical_year.empty?
                if hourly_historical_years.include? electricity_emissions_hourly_historical_year
                  OpenStudio::Extension.set_measure_argument(osw, 'add_ems_emissions_reporting', 'hourly_historical_year', electricity_emissions_hourly_historical_year)
                else
                  @@logger.error(" '#{electricity_emissions_hourly_historical_year}' is not valid option for electricity_emissions_hourly_historical_year. Please choose an input from #{hourly_historical_years}")
                end
              end

              # annual_historical_year
              if !electricity_emissions_annual_historical_year.nil? && !electricity_emissions_annual_historical_year.empty?
                if annual_historical_years.include? electricity_emissions_annual_historical_year
                  OpenStudio::Extension.set_measure_argument(osw, 'add_ems_emissions_reporting', 'annual_historical_year', electricity_emissions_annual_historical_year)
                else
                  @@logger.error("'#{electricity_emissions_annual_historical_year}' is not valid option for electricity_emissions_annual_historical_year. Please choose an input from #{annual_historical_years}")
                end
              end
            rescue StandardError
            end

          end

        end

        # call the default feature reporting measure
        reporting_frequency = ENV['URBANOPT_REPORTING_FREQUENCY'] || 'Timestep'
        OpenStudio::Extension.set_measure_argument(osw, 'default_feature_reports', 'reporting_frequency', reporting_frequency)
        OpenStudio::Extension.set_measure_argument(osw, 'default_feature_reports', 'feature_id', feature_id)
        OpenStudio::Extension.set_measure_argument(osw, 'default_feature_reports', 'feature_name', feature_name)
        OpenStudio::Extension.set_measure_argument(osw, 'default_feature_reports', 'feature_type', feature_type)
        OpenStudio::Extension.set_measure_argument(osw, 'default_feature_reports', 'feature_location', feature_location)

        return osw
      end
    end # end class
  end
end
