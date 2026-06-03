# Build a SimpleGlazing from u_factor + shgc and assign to all windows.

class SetWindowConstruction < OpenStudio::Measure::ModelMeasure
  def name; 'Set Window Construction'; end
  def description; 'Build a SimpleGlazing from given U-factor + SHGC.'; end
  def modeler_description; description; end

  def arguments(_model)
    args = OpenStudio::Measure::OSArgumentVector.new
    u = OpenStudio::Measure::OSArgument.makeDoubleArgument('u_factor', true)
    u.setDisplayName('Window U-factor (W/m2-K)')
    u.setDefaultValue(3.12)
    args << u
    s = OpenStudio::Measure::OSArgument.makeDoubleArgument('shgc', true)
    s.setDisplayName('Window SHGC (0-1)')
    s.setDefaultValue(0.40)
    args << s
    args
  end

  def run(model, runner, user_arguments)
    super(model, runner, user_arguments)
    return false unless runner.validateUserArguments(arguments(model), user_arguments)
    u    = runner.getDoubleArgumentValue('u_factor', user_arguments)
    shgc = runner.getDoubleArgumentValue('shgc',     user_arguments)

    glaz = OpenStudio::Model::SimpleGlazing.new(model)
    glaz.setUFactor(u)
    glaz.setSolarHeatGainCoefficient(shgc)
    glaz.setName("PowerTwin Glazing U=#{u} SHGC=#{shgc}")
    constr = OpenStudio::Model::Construction.new(model)
    constr.setLayers([glaz])
    constr.setName('PowerTwin Window Construction')

    n = 0
    model.getSubSurfaces.each do |ss|
      next unless %w[FixedWindow OperableWindow].include?(ss.subSurfaceType)
      ss.setConstruction(constr)
      n += 1
    end
    runner.registerInfo("set #{n} window subsurfaces (U=#{u} SHGC=#{shgc})")
    true
  end
end

SetWindowConstruction.new.registerWithApplication
