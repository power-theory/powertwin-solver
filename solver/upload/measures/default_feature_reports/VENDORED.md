Vendored from urbanopt-reporting 1.0.0 with ONE change (patch_district_meters.py): the measure also
requests the E+ 23.2+/OS 3.9 RENAMED district meters (DistrictHeatingWater/Steam:Facility). The gem
copy only requests the legacy DistrictHeating:Facility name, so district-heated buildings report an
empty column and the energy silently vanishes from the Steam sensor capture. Vendoring (rather than
patching the gem) is required because `uo run` bundle-installs a FRESH gem copy per project -- but
the OSW measure_paths puts the project measures dir first, so this copy shadows the gem everywhere.

Second change (2026-09-13, same patch script): the CSV timeseries loop looks up a series' key values
at the configured reporting frequency (falling back to 'Zone Timestep') instead of the hardcoded
'Zone Timestep'. Under Hourly reporting the EMS electricity-emissions series were stored only at
Hourly, so the hardcoded lookup returned no keys and the columns came out empty and unit-less; the
CO2 sensor then carried only the gas/propane terms on every cluster run. Verified by A/B on 2026-09-12
(jobs 16472188 Timestep vs 16472189 Hourly) and by reading the Hourly run's eplusout.sql (8760 real
values stored at Hourly, key EMS).
