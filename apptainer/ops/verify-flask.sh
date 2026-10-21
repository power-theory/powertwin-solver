# Checks for a powertwin-solver flask SIF, run inside the new SIF by build-sif.sh before it gets its name (push-sif.sh
# passes it to push-image-arcc --verify). Any failed line exits 1 and the SIF is not installed.
cat /solver/BUILD_INFO
g=/usr/local/lib/ruby/gems/3.2.2/gems; bad=0
if (cd /solver/upload/reference_data && sha256sum -c --quiet county_tables.sha256 >/dev/null 2>&1); then echo "county tables: all match county_tables.sha256"; else echo "county tables: MISSING OR MISMATCHED"; bad=1; fi
echo "gem cache: $(ls $g/../cache | wc -l)"
n=$(grep -c "powertwin patch: zoning read" $g/urbanopt-geojson-1.0.1/lib/urbanopt/geojson/building.rb 2>/dev/null); echo "geojson floor-height patch: ${n:-0}"; [ "${n:-0}" -ge 1 ] || bad=1
n=$(grep -c "will not divide.)$" $g/urbanopt-geojson-1.0.1/lib/urbanopt/geojson/zoning.rb 2>/dev/null); m=$(grep -c "_ZONING_CORE_FIX" /solver/patches/patch_openstudio_standards.py 2>/dev/null)
echo "perimeter zoning guards: ${n:-0} warnings (4 when patched), runtime patch script ${m:-0}"; [ "${n:-0}" = 4 ] && [ "${m:-0}" -ge 1 ] || bad=1
n=$(grep -c "comstock_system_type" /solver/upload/PowerTwin.rb 2>/dev/null); echo "comstock_system_type in mapper: ${n:-0}"; [ "${n:-0}" -ge 1 ] || bad=1
grep -m1 "^RESOLVER_VERSION" /solver/app/modules/simulation/sim_params_spec.py | cut -c1-26
python3 -c 'print("sif python OK")' || bad=1
exit $bad
