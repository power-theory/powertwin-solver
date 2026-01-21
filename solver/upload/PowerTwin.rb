# *********************************************************************************
# PowerTwinMapper
# Add custom measures here
# *********************************************************************************

require 'openstudio'

require_relative 'Baseline'


module URBANopt
  module Scenario
    class PowerTwinMapper < BaselineMapper
      def create_osw(scenario, features, feature_names)
        # Call the parent method to get the base osw
        osw = super(scenario, features, feature_names)

        # Get the feature (building) details
        feature = features[0]
        building_type = feature.building_type

        # Modify measure only to commercial building types
        if commercial_building_types.include? building_type

          # Modify the reduce_epd_by_percentage_for_peak_hours measure
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', '__SKIP__', false)
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'start_date1', '05-01')
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'end_date1', '09-30')
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'start_time1', '00:00:00')
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'end_time1', '23:59:59')
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'epd_reduce_percent', 0)


 
        end
        return osw
      end

    end
  end
end