# Scale every interior Lights instance by one factor so the building's installed lighting power density equals
# target_w_per_ft2 (the ComStock 2024.1 average for the building's type and lighting generation, NREL/TP-5500-86100
# Table 2). Replaces the flat 35% cut to the 90.1 allowance (2026-10-01); the template's space-by-space pattern is kept.
class SetLightingPowerDensity < OpenStudio::Measure::ModelMeasure
  def name; 'Set Lighting Power Density'; end
  def description; 'Scale interior lighting so the building-average installed LPD equals target_w_per_ft2.'; end
  def modeler_description; description; end
  def arguments(_model)
    args = OpenStudio::Measure::OSArgumentVector.new
    a = OpenStudio::Measure::OSArgument.makeDoubleArgument('target_w_per_ft2', true)
    a.setDisplayName('Target building-average lighting power density')
    a.setUnits('W/ft^2')
    a.setDefaultValue(0.0)
    args << a
    args
  end
  def run(model, runner, user_arguments)
    super(model, runner, user_arguments)
    return false unless runner.validateUserArguments(arguments(model), user_arguments)
    target = runner.getDoubleArgumentValue('target_w_per_ft2', user_arguments)
    return runner.registerError('target_w_per_ft2 must be > 0') unless target > 0
    area_m2 = model.getBuilding.floorArea
    if area_m2 <= 0
      runner.registerAsNotApplicable('building floor area is zero')
      return true
    end
    current_w = model.getBuilding.lightingPower
    if current_w <= 0
      runner.registerAsNotApplicable('model has no interior lighting to rescale')
      return true
    end
    area_ft2 = area_m2 * 10.763910417
    factor = target * area_ft2 / current_w
    model.getLightss.each { |l| l.setMultiplier(l.multiplier * factor) }
    new_w = model.getBuilding.lightingPower
    runner.registerInfo("lighting: was #{(current_w / area_ft2).round(3)} W/ft2, target #{target.round(3)}, factor #{factor.round(4)}, now #{(new_w / area_ft2).round(3)} W/ft2")
    runner.registerFinalCondition("target_w_per_ft2=#{target.round(3)}, new=#{(new_w / area_ft2).round(3)}")
    true
  end
end
SetLightingPowerDensity.new.registerWithApplication
