# Force every People object to a uniform peoplePerFloorArea density so the
# building's modelled occupancy equals target_total exactly.

class SetPeoplePerFloorArea < OpenStudio::Measure::ModelMeasure
  def name; 'Set Total Occupants'; end
  def description; 'Force every People object to a uniform density that yields target_total in the building.'; end
  def modeler_description; description; end

  def arguments(_model)
    args = OpenStudio::Measure::OSArgumentVector.new
    a = OpenStudio::Measure::OSArgument.makeIntegerArgument('target_total', true)
    a.setDisplayName('Target total occupants')
    a.setDefaultValue(1)
    args << a
    args
  end

  def run(model, runner, user_arguments)
    super(model, runner, user_arguments)
    return false unless runner.validateUserArguments(arguments(model), user_arguments)
    target = runner.getIntegerArgumentValue('target_total', user_arguments)
    return runner.registerError('target_total must be >= 0') if target < 0

    area_m2 = model.getBuilding.floorArea
    if area_m2 <= 0
      runner.registerAsNotApplicable('building floor area is zero; cannot set density')
      return true
    end

    people = model.getPeoples
    if people.empty?
      runner.registerAsNotApplicable('model has no People objects to rescale')
      return true
    end

    current = 0.0
    model.getSpaces.each { |s| current += s.numberOfPeople }
    density = target.to_f / area_m2  # people / m2

    people.each do |p|
      defn = p.peopleDefinition
      defn.setNumberOfPeopleCalculationMethod('People/Area', area_m2)
      defn.setPeopleperSpaceFloorArea(density)
    end

    new_total = 0.0
    model.getSpaces.each { |s| new_total += s.numberOfPeople }
    runner.registerInfo("occupants: was=#{current.round}, target=#{target}, density=#{density.round(6)} ppl/m2, new=#{new_total.round}")
    runner.registerFinalCondition("target_total=#{target}, new_total=#{new_total.round}")
    true
  end
end

SetPeoplePerFloorArea.new.registerWithApplication
