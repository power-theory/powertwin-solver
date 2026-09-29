#!/usr/bin/env python3
"""Fix upstream defects in openstudio-standards 0.7.x that fail simulations a user can request.

1. create_typical.rb, weekend hours past midnight. The midnight-overlap message interpolates
   `wknd_op_hrs_start`, a name that does not exist (the weekday branch correctly uses
   `wkdy_op_hrs_start_time`), so create_typical_building_from_model raises NameError and the building
   produces no report. All 97 failures of the tetonfan1 pilot (2026-09-19, job 17869342) were this:
   commercial variants with weekend start plus duration past 24:00, a combination any user can set.

2. Prototype.hvac_systems.rb, PTAC and PSZ-AC on a central air source heat pump. Both builders hard-code
   the zone heating design supply air at 122 F (50 C) whatever hot water loop the coil is on. The heat
   pump systems ('PTAC with central air source heat pump', 'PSZ-AC with central air source heat pump')
   get the 'LowTemperature' loop, designed for 120 F (48.9 C) supply water, and a water coil cannot heat
   air above its entering water, so EnergyPlus fails 'Autosizing of heating coil UA ... Inadequate water
   side capacity' and the building produces no report (tetonfan3 pilot 2026-09-27: 5 of 5; as a user edit
   on 4 Teton commercial buildings: 8 of 8). Both builders read the loop's design exit temperature
   (`hw_temp_c`) but use it only for the coil's rated water temperatures, never to cap the supply air;
   upstream master is unchanged (checked 2026-09-28). The patch keeps
   122 F whenever the loop supplies hotter water (boiler 180 F and district loops: no change) and
   otherwise takes the library's own zone heating default from standard_design_sizing_temperatures
   (104 F), the value the fan coil and reheat heat pump systems already size to.

Usage: patch_openstudio_standards.py [GEM_ROOT ...]
Patches every openstudio-standards copy under the given gem roots (default: the image's gem
directories; the cluster passes its per-job BUNDLE_PATH after bundle install). Idempotent. Fails when a
file is missing or carries neither the defect nor the fix, so a gem upgrade that moves the code is
noticed instead of silently skipped.
"""
import glob
import os
import sys

_LOOP_CAP = """{i}# powertwin patch: a water coil cannot heat air above its entering water; on a loop that cannot
{i}# supply the temperature above (the 120 F heat pump loop) use the library's zone heating default
{i}unless hot_water_loop.nil? || dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] < hw_temp_c
{i}  dsgn_temps['zn_htg_dsgn_sup_air_temp_f'] = standard_design_sizing_temperatures['zn_htg_dsgn_sup_air_temp_f']
{i}  dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] = standard_design_sizing_temperatures['zn_htg_dsgn_sup_air_temp_c']
{i}end
"""


def _cap_after(anchor: str, indent: str) -> tuple[str, str]:
    return anchor, anchor + _LOOP_CAP.format(i=indent)


_PTAC = """    # adjusted zone design temperatures for ptac
    dsgn_temps['zn_htg_dsgn_sup_air_temp_f'] = 122.0
    dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] = OpenStudio.convert(dsgn_temps['zn_htg_dsgn_sup_air_temp_f'], 'F', 'C').get
"""
_PSZ = """      # adjusted design heating temperature for psz_ac
      dsgn_temps['zn_htg_dsgn_sup_air_temp_f'] = 122.0
      dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] = OpenStudio.convert(dsgn_temps['zn_htg_dsgn_sup_air_temp_f'], 'F', 'C').get
"""

# (file under lib/openstudio-standards, [(defect, fix), ...])
PATCHES = [
    (os.path.join('create_typical', 'create_typical.rb'),
     [('Weekend start time of #{wknd_op_hrs_start} plus duration',
       'Weekend start time of #{wknd_op_hrs_start_time} plus duration')]),
    (os.path.join('prototypes', 'common', 'objects', 'Prototype.hvac_systems.rb'),
     [_cap_after(_PTAC, '    '), _cap_after(_PSZ, '      ')]),
]
DEFAULT_ROOTS = ['/usr/local/lib/ruby/gems/3.2.2', '/var/oscli/gems/ruby/3.2.0']


def _libs(roots):
    rel = os.path.join('gems', 'openstudio-standards-*', 'lib', 'openstudio-standards')
    return sorted({p for r in roots for p in glob.glob(os.path.join(r, rel)) + glob.glob(os.path.join(r, 'ruby', '*', rel))})


def main(roots):
    libs = _libs(roots)
    if not libs:
        sys.exit(f'no openstudio-standards under {roots}')
    for lib in libs:
        for rel, edits in PATCHES:
            path = os.path.join(lib, rel)
            if not os.path.isfile(path):
                sys.exit(f'{path}: missing; check the gem version')
            with open(path) as fh:
                src = fh.read()
            out = src
            for bad, good in edits:
                if good in out:
                    continue
                if out.count(bad) != 1:
                    sys.exit(f'{path}: expected the patched code once, found {out.count(bad)}; check the gem version')
                out = out.replace(bad, good)
            if out == src:
                print(f'already patched: {path}')
                continue
            # atomic: parallel cluster batches patch one shared gem home, and a Ruby process loading the
            # file must see the old or the new content, never a half-written one
            tmp = f'{path}.{os.getpid()}.tmp'
            with open(tmp, 'w') as fh:
                fh.write(out)
            os.chmod(tmp, os.stat(path).st_mode)
            os.replace(tmp, path)
            print(f'patched: {path}')


if __name__ == '__main__':
    main(sys.argv[1:] or DEFAULT_ROOTS)
