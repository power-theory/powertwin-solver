"""solver/upload/measures/powertwin_canonical_idf: the EnergyPlus measure that sorts every SpaceList's spaces by name,
so the translated input does not depend on OpenStudio's random handle order (2026-10-08). It must be an
EnergyPlusMeasure, run on every building, and sit after the last model measure and before any reporting measure."""
import json
import os
import xml.etree.ElementTree as ET

ROOT = os.path.join(os.path.dirname(__file__), '..', 'solver', 'upload')
MEASURE = os.path.join(ROOT, 'measures', 'powertwin_canonical_idf')


def _type(xml_path):
    tree = ET.parse(xml_path)
    for attr in tree.getroot().iter('attribute'):
        if attr.findtext('name') == 'Measure Type':
            return attr.findtext('value')
    return None


def test_measure_is_an_energyplus_measure_named_like_its_dir():
    tree = ET.parse(os.path.join(MEASURE, 'measure.xml'))
    assert tree.getroot().findtext('name') == 'powertwin_canonical_idf'
    assert tree.getroot().findtext('class_name') == 'PowertwinCanonicalIdf'
    assert _type(os.path.join(MEASURE, 'measure.xml')) == 'EnergyPlusMeasure'
    rb = open(os.path.join(MEASURE, 'measure.rb')).read()
    assert 'class PowertwinCanonicalIdf < OpenStudio::Measure::EnergyPlusMeasure' in rb
    assert "getObjectsByType('SpaceList'.to_IddObjectType)" in rb and 'members.sort' in rb
    # decimal inputs rounded to 12 significant digits (C++ sums in handle order leave last-bit noise, Teton 13014)
    assert "format('%.12g', Float(text.get))" in rb and 'DECIMAL = ' in rb


def test_workflow_runs_it_between_model_and_reporting_measures():
    steps = json.load(open(os.path.join(ROOT, 'base_workflow.osw')))['steps']
    names = [s['measure_dir_name'] for s in steps]
    assert names.count('powertwin_canonical_idf') == 1
    i = names.index('powertwin_canonical_idf')
    assert steps[i]['arguments'] == {'__SKIP__': False}
    # the step order the workflow already uses: model measures, then EnergyPlus measures, then reporting measures
    assert names[i - 1] == 'add_packaged_ice_storage'      # an EnergyPlusMeasure (openstudio-ee)
    assert names[i + 1] == 'export_time_series_modelica'   # the first ReportingMeasure
