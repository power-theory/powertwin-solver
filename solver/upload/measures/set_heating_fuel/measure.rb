# Set fuel on every CoilHeatingGas and BoilerHotWater in the model.

class SetHeatingFuel < OpenStudio::Measure::ModelMeasure
  FUELS = %w[NaturalGas Propane FuelOilNo2].freeze

  def name; 'Set Heating Fuel'; end
  def description; 'Set fuel type on every gas heating coil and hot-water boiler.'; end
  def modeler_description; description; end

  def arguments(_model)
    args = OpenStudio::Measure::OSArgumentVector.new
    f = OpenStudio::Measure::OSArgument.makeChoiceArgument('fuel', FUELS, true)
    f.setDisplayName('E+ fuel name')
    f.setDefaultValue('NaturalGas')
    args << f
    args
  end

  def run(model, runner, user_arguments)
    super(model, runner, user_arguments)
    return false unless runner.validateUserArguments(arguments(model), user_arguments)
    fuel = runner.getStringArgumentValue('fuel', user_arguments)

    count = 0
    model.getCoilHeatingGass.each do |coil|
      coil.setFuelType(fuel)
      count += 1
    end
    model.getBoilerHotWaters.each do |boiler|
      boiler.setFuelType(fuel)
      count += 1
    end

    if count == 0
      runner.registerAsNotApplicable('no CoilHeatingGas or BoilerHotWater in model')
      return true
    end
    runner.registerInfo("set #{count} heating object(s) to fuel=#{fuel}")
    true
  end
end

SetHeatingFuel.new.registerWithApplication
