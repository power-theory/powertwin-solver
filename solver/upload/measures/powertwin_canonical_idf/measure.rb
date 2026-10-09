# frozen_string_literal: true

# PowerTwin: make the translated EnergyPlus input independent of OpenStudio's object order (2026-10-08).
# 1. The ForwardTranslator writes each SpaceList with its spaces in OpenStudio's handle order, and handles are random
#    per run, so EnergyPlus summed space loads in a different order each run. Every SpaceList's spaces are sorted by name.
# 2. OpenStudio's C++ side also sums in handle order (a space's floor area over its surfaces, ...), so values derived
#    from such sums (blended lighting and outdoor-air densities) differ in their last bits from run to run, and a large
#    multi-zone building's results moved up to 0.1% on that alone (Teton 13014). Every decimal number in the input is
#    rounded to 12 significant digits (a change of at most 5e-13 relative), which absorbs that noise.
# The model itself is unchanged.
class PowertwinCanonicalIdf < OpenStudio::Measure::EnergyPlusMeasure
  DECIMAL = /\A[-+]?\d*\.\d+(?:[eE][-+]?\d+)?\z/.freeze

  def name
    'powertwin_canonical_idf'
  end

  def description
    'Sorts the spaces of every SpaceList by name and rounds decimal inputs to 12 significant digits, so the EnergyPlus input does not depend on object order.'
  end

  def modeler_description
    'SpaceList members are written in OpenStudio handle order, and C++ sums follow it into the last bits of derived values; both are random per run.'
  end

  def arguments(_workspace)
    OpenStudio::Measure::OSArgumentVector.new
  end

  def run(workspace, runner, user_arguments)
    super(workspace, runner, user_arguments)
    return false unless runner.validateUserArguments(arguments(workspace), user_arguments)

    sorted = 0
    workspace.getObjectsByType('SpaceList'.to_IddObjectType).each do |space_list|
      members = (1...space_list.numFields).map { |i| space_list.getString(i).get }
      next if members == members.sort

      members.sort.each_with_index { |member, i| space_list.setString(i + 1, member) }
      sorted += 1
    end
    rounded = 0
    workspace.objects.each do |object|
      (0...object.numFields).each do |i|
        text = object.getString(i)
        next unless text.is_initialized && DECIMAL.match?(text.get)

        canonical = format('%.12g', Float(text.get))
        next if canonical == text.get

        object.setString(i, canonical)
        rounded += 1
      end
    end
    runner.registerFinalCondition("Sorted the spaces of #{sorted} SpaceList(s) by name; rounded #{rounded} decimal field(s) to 12 significant digits.")
    true
  end
end

PowertwinCanonicalIdf.new.registerWithApplication
