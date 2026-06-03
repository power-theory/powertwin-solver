# Set fuel + thermal_efficiency on every WaterHeater:Mixed.

class SetServiceWaterHeatingFuel < OpenStudio::Measure::ModelMeasure
  FUELS = %w[Electricity NaturalGas FuelOilNo2 Propane].freeze

  def name; 'Set Service Water Heating Fuel'; end
  def description; 'Set fuel type and thermal efficiency on every WaterHeater:Mixed.'; end
  def modeler_description; description; end

  def arguments(_model)
    args = OpenStudio::Measure::OSArgumentVector.new
    f = OpenStudio::Measure::OSArgument.makeChoiceArgument('fuel', FUELS, true)
    f.setDisplayName('E+ fuel name')
    f.setDefaultValue('NaturalGas')
    args << f
    e = OpenStudio::Measure::OSArgument.makeDoubleArgument('thermal_efficiency', true)
    e.setDisplayName('Thermal efficiency (0-1)')
    e.setDefaultValue(0.80)
    args << e
    args
  end

  def run(model, runner, user_arguments)
    super(model, runner, user_arguments)
    return false unless runner.validateUserArguments(arguments(model), user_arguments)
    fuel = runner.getStringArgumentValue('fuel', user_arguments)
    eff  = runner.getDoubleArgumentValue('thermal_efficiency', user_arguments)

    heaters = model.getWaterHeaterMixeds
    if heaters.empty?
      runner.registerAsNotApplicable('no WaterHeater:Mixed in model')
      return true
    end
    heaters.each do |h|
      h.setHeaterFuelType(fuel)
      h.setHeaterThermalEfficiency(eff)
    end
    runner.registerInfo("set #{heaters.size} water heater(s): fuel=#{fuel} eta=#{eff}")
    true
  end
end

SetServiceWaterHeatingFuel.new.registerWithApplication
