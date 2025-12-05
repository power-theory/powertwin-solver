# *******************************************************************************
# OpenStudio(R), Copyright (c) 2008-2021, Alliance for Sustainable Energy, LLC.
# All rights reserved.
# *******************************************************************************

# Start the measure
class EnableZoneSizing < OpenStudio::Measure::ModelMeasure
  # Define the name that a user will see
  def name
    return 'Enable Zone Sizing'
  end

  # Human readable description
  def description
    return 'Enables zone sizing and system sizing calculations required for HVAC autosizing'
  end

  # Human readable description of modeling approach
  def modeler_description
    return 'Sets SimulationControl fields DoZoneSizingCalculation and DoSystemSizingCalculation to true, plus HVAC sizing simulation for OpenStudio 3.0+'
  end

  # Define the arguments that the user will input
  def arguments(model)
    args = OpenStudio::Measure::OSArgumentVector.new
    return args
  end

  # Define what happens when the measure is run
  def run(model, runner, user_arguments)
    super(model, runner, user_arguments)

    # Use the built-in error checking
    if !runner.validateUserArguments(arguments(model), user_arguments)
      return false
    end

    # Get the SimulationControl object
    simulation_control = model.getSimulationControl

    # Enable zone sizing calculation
    # This is critical for HVAC autosizing to work properly
    simulation_control.setDoZoneSizingCalculation(true)
    runner.registerInfo('Enabled zone sizing calculation')

    # Enable system sizing calculation
    simulation_control.setDoSystemSizingCalculation(true)
    runner.registerInfo('Enabled system sizing calculation')

    # Enable plant sizing calculation (also helpful)
    simulation_control.setDoPlantSizingCalculation(true)
    runner.registerInfo('Enabled plant sizing calculation')

    # For OpenStudio 3.0.0 and above, also enable HVAC sizing simulation
    if model.version >= OpenStudio::VersionString.new('3.0.0')
      simulation_control.setDoHVACSizingSimulationforSizingPeriods(true)
      simulation_control.setMaximumNumberofHVACSizingSimulationPasses(1)
      runner.registerInfo('Enabled HVAC sizing simulation for sizing periods (OpenStudio 3.0.0+)')
    end

    # Set run simulation for sizing periods to ensure sizing calculations are performed
    simulation_control.setRunSimulationforSizingPeriods(true)
    runner.registerInfo('Enabled run simulation for sizing periods')

    runner.registerFinalCondition('Zone sizing, system sizing, plant sizing, and HVAC sizing simulation have been enabled for HVAC autosizing')

    return true
  end
end

# Register the measure to be used by the application
EnableZoneSizing.new.registerWithApplication