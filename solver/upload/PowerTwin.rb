# *********************************************************************************
# PowerTwinMapper
# *********************************************************************************

require 'openstudio'
require 'urbanopt/reporting'
require 'openstudio/geb'
require 'openstudio/common_measures'
require 'openstudio/model_articulation'
require 'openstudio/ee_measures'
require 'openstudio/calibration'

require_relative 'Baseline'

require 'json'

module URBANopt
  module Scenario
    class PowerTwinMapper < BaselineMapper
      
      # Override create_osw to configure zone sizing for all simulations
      def create_osw(scenario, features, feature_names)
        # Call the parent method to get the base osw
        osw = super(scenario, features, feature_names)

        # Get the feature (building) details
        feature = features[0]
        building_type = feature.building_type

        puts "[PowerTwin] Creating OSW for building type: #{building_type}"

        # CRITICAL FIX: Add a model measure to enable zone sizing BEFORE create_typical_building_from_model
        # This must run early in the workflow to configure SimulationControl properly
        
        if osw['steps']
          # Find the index of create_typical_building_from_model
          create_typical_index = osw['steps'].find_index { |s| s['measure_dir_name'] == 'create_typical_building_from_model' }
          
          if create_typical_index && create_typical_index > 0
            # Insert a custom inline measure to enable zone sizing
            # This runs as Ruby code within the OpenStudio workflow
            enable_sizing_measure = {
              'measure_dir_name' => 'EnableZoneSizing',
              'arguments' => {},
              'name' => 'Enable Zone Sizing',
              '__SKIP__' => false
            }
            
            # Insert this measure right before create_typical_building_from_model
            osw['steps'].insert(create_typical_index, enable_sizing_measure)
            
            puts "[PowerTwin] Inserted EnableZoneSizing measure at index #{create_typical_index}"
          end
          
          # Also configure the create_typical_building_from_model measure arguments
          osw['steps'].each do |step|
            if step['measure_dir_name'] == 'create_typical_building_from_model'
              # Ensure arguments hash exists
              step['arguments'] ||= {}
              
              # Configure measure to add all necessary components
              step['arguments']['add_constructions'] = true
              step['arguments']['add_space_type_loads'] = true
              step['arguments']['add_elevators'] = false
              step['arguments']['add_exterior_lights'] = true
              step['arguments']['add_exhaust'] = true
              step['arguments']['add_swh'] = true
              step['arguments']['add_hvac'] = true
              step['arguments']['add_thermostat'] = true
              
              puts "[PowerTwin] Configured create_typical_building_from_model arguments"
            end
          end
        end

        # Modify measure only for commercial building types
        if commercial_building_types.include? building_type
          # Modify the reduce_epd_by_percentage_for_peak_hours measure
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', '__SKIP__', false)
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'start_date1', '05-01')
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'end_date1', '09-30')
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'start_time', '00:00:00')
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'end_time', '23:59:59')
          OpenStudio::Extension.set_measure_argument(osw, 'reduce_epd_by_percentage_for_peak_hours', 'epd_reduce_percent', 0)
        end
        
        return osw
      end

    end
  end
end