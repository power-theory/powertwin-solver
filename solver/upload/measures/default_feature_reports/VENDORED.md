Vendored from urbanopt-reporting 1.0.0 with ONE change (patch_district_meters.py): the measure also
requests the E+ 23.2+/OS 3.9 RENAMED district meters (DistrictHeatingWater/Steam:Facility). The gem
copy only requests the legacy DistrictHeating:Facility name, so district-heated buildings report an
empty column and the energy silently vanishes from the Steam sensor capture. Vendoring (rather than
patching the gem) is required because `uo run` bundle-installs a FRESH gem copy per project -- but
the OSW measure_paths puts the project measures dir first, so this copy shadows the gem everywhere.
