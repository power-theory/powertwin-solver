#!/usr/bin/env python3
"""Patch HPXMLtoOpenStudio location.rb to tolerate leap-year sims with
8760-hour TMY weather.

By default `OpenStudioHPXML/HPXMLtoOpenStudio/resources/location.rb:apply_year`
fails fast when `sim_calendar_year` is a leap year and the EPW has 8760
hours (it expects 8784). Since virtually all TMY3 EPWs are 8760-hour, this
blocks every residential sim run with URBANOPT_SIMULATION_YEAR set to a
leap year (2024, 2028, 2032 ...). Commercial workflows are unaffected
because their model translation doesn't perform this check.

Patch behavior: when the leap-year/8760-hour mismatch is detected, emit a
WARNING and use (sim_year - 1) as the OpenStudio model's calendar year
so EnergyPlus reads its full 8760-hour weather stream against a non-leap
calendar. Day-of-week alignment shifts by 1; for stock-survey aggregations
and monthly/yearly accuracy scoring this is acceptable.

Runs at Dockerfile build time, idempotent. See task #62 for context.
"""
import re
import sys
from pathlib import Path

TARGET = Path(
    '/usr/local/lib/ruby/gems/3.2.2/gems/urbanopt-cli-1.0.1/example_files/'
    'resources/residential-measures/resources/hpxml-measures/'
    'HPXMLtoOpenStudio/resources/location.rb'
)
MARKER = '# PowerTwin patch: tolerate 8760h weather on leap-year sim'

# Patch 1: get_sim_calendar_year (the UPSTREAM entry point that propagates to
# every schedule-sizing call in BuildResidentialModel). Downgrading here makes
# every downstream consumer see the non-leap year so schedule arrays end up
# 365-element instead of 366-element.
GET_PATTERN = re.compile(
    r'^( +)def self\.get_sim_calendar_year\(sim_calendar_year, weather\)\n'
    r'( +if \(not weather\.nil\?\) && \(not weather\.header\.ActualYear\.nil\?\) # AMY\n'
    r' +sim_calendar_year = weather\.header\.ActualYear\n'
    r' +end\n'
    r' +if sim_calendar_year\.nil\?\n'
    r' +sim_calendar_year = 2007\n'
    r' +end\n)'
    r'\n'
    r'( +return sim_calendar_year\n'
    r' +end\n)',
    re.MULTILINE,
)

GET_REPLACEMENT = (
    r'\1def self.get_sim_calendar_year(sim_calendar_year, weather)' '\n'
    r'\2'
    '\n'
    r'\1  ' + MARKER + '\n'
    r'\1  if Date.leap?(sim_calendar_year) && (not weather.nil?) && weather.header.NumRecords != 8784' '\n'
    r'\1    puts "WARNING: leap year #{sim_calendar_year} requested with #{weather.header.NumRecords}h weather; using #{sim_calendar_year - 1} for calendar alignment"' '\n'
    r'\1    sim_calendar_year -= 1' '\n'
    r'\1  end' '\n'
    '\n'
    r'\3'
)

# Patch 2 (defensive): apply_year sets the OpenStudio model calendar year. With
# get_sim_calendar_year patched upstream, this branch likely won't trigger
# anymore (since the hpxml header carries the downgraded year). Keep the patch
# anyway so the model's year_description matches in any case where the upstream
# header was set elsewhere.
APPLY_PATTERN = re.compile(
    r'^( +)def self\.apply_year\(model, hpxml_header, weather\)\n'
    r' +if Date\.leap\?\(hpxml_header\.sim_calendar_year\)\n'
    r' +n_hours = weather\.header\.NumRecords\n'
    r' +if n_hours != 8784\n'
    r' +fail "Specified a leap year[^"]*"\n'
    r' +end\n'
    r' +end\n'
    r'\n'
    r' +year_description = model\.getYearDescription\n'
    r' +year_description\.setCalendarYear\(hpxml_header\.sim_calendar_year\)\n'
    r' +end\n',
    re.MULTILINE,
)

APPLY_REPLACEMENT = (
    r'\1def self.apply_year(model, hpxml_header, weather)' '\n'
    r'\1  ' + MARKER + '\n'
    r'\1  sim_year = hpxml_header.sim_calendar_year' '\n'
    r'\1  if Date.leap?(sim_year)' '\n'
    r'\1    n_hours = weather.header.NumRecords' '\n'
    r'\1    if n_hours != 8784' '\n'
    r'\1      sim_year -= 1' '\n'
    r'\1    end' '\n'
    r'\1  end' '\n'
    '\n'
    r'\1  year_description = model.getYearDescription' '\n'
    r'\1  year_description.setCalendarYear(sim_year)' '\n'
    r'\1end' '\n'
)


def main():
    if not TARGET.exists():
        sys.exit(f"ERROR: {TARGET} not found (urbanopt-cli not installed?)")
    src = TARGET.read_text()
    if MARKER in src:
        print(f"already patched: {TARGET}")
        return
    new = GET_PATTERN.sub(GET_REPLACEMENT, src, count=1)
    if new == src:
        sys.exit(f"ERROR: get_sim_calendar_year pattern did not match in {TARGET}; "
                 "upstream code may have changed and the patch needs updating")
    new2 = APPLY_PATTERN.sub(APPLY_REPLACEMENT, new, count=1)
    if new2 == new:
        sys.exit(f"ERROR: apply_year pattern did not match in {TARGET}; "
                 "upstream code may have changed and the patch needs updating")
    TARGET.write_text(new2)
    print(f"patched: {TARGET}")


if __name__ == '__main__':
    main()
