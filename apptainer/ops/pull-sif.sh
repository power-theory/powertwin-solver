#!/bin/bash
#SBATCH --job-name=pull-sif
#SBATCH --partition=mb
#SBATCH --nodes=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=04:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --mail-user=admin@powertheoryinc.com
#SBATCH --mail-type=ALL
#SBATCH --output=/project/cowy-ptheory/powertwin/campaigns/ops-logs/%x_%j.out
# Pull solver images into their own SIFs and verify each inside the SIF (runbook: vault Dev/powertwin-solver/ARCC HPC,
# "Standard campaign launch"). Replaces the per-campaign pull-sif-*.sh / swap-sif-*.sh copies.
#   sbatch pull-sif.sh <tag> [<tag>...]            -> sif_containers/flask-<tag>.sif
#   sbatch pull-sif.sh --as-flask <tag>            -> also replaces flask.sif (keeps flask.sif.bak_<date>)
# The pull follows the working recipe: blob cache on scratch, unpack in node-local /tmp (the shared volume lacks the
# extended attributes the unpack needs), the finished SIF written to project storage.
set -euo pipefail
module --force purge
module load arcc/1.0 slurm gcc/14.2.0 apptainer/1.4.1
AS_FLASK=0
if [ "${1:-}" = "--as-flask" ]; then AS_FLASK=1; shift; fi
[ $# -ge 1 ] || { echo "usage: sbatch pull-sif.sh [--as-flask] <tag> [<tag>...]"; exit 2; }
SIF_DIR=/project/cowy-ptheory/powertwin/shared/sif_containers
export APPTAINER_CACHEDIR=/gscratch/lukemacy/apptainer_cache
export APPTAINER_TMPDIR=/tmp/apptainer_$$
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
trap 'rm -rf "$APPTAINER_TMPDIR"' EXIT
cd "$SIF_DIR"
for tag in "$@"; do
  echo "[$(date '+%F %T')] pulling missionloyd/powertwin-solver-flask:$tag"
  apptainer pull --force --disable-cache "flask-$tag.sif.new" "docker://missionloyd/powertwin-solver-flask:$tag"
  test -s "flask-$tag.sif.new"
  echo "== verify flask-$tag.sif.new ($(du -m "flask-$tag.sif.new" | cut -f1) MB)"
  # Verified before it takes its final name: launch-campaign.sh uses flask-<tag>.sif, so a rejected image must never
  # get that name (or flask.sif). The check exits 1 on any failed line.
  if ! apptainer exec "flask-$tag.sif.new" sh -c 'cat /solver/BUILD_INFO; g=/usr/local/lib/ruby/gems/3.2.2/gems; bad=0;
    if (cd /solver/upload/reference_data && sha256sum -c --quiet county_tables.sha256 >/dev/null 2>&1); then echo "county tables: all match county_tables.sha256"; else echo "county tables: MISSING OR MISMATCHED"; bad=1; fi;
    echo "gem cache: $(ls $g/../cache | wc -l)";
    n=$(grep -c "powertwin patch: zoning read" $g/urbanopt-geojson-1.0.1/lib/urbanopt/geojson/building.rb 2>/dev/null); echo "geojson floor-height patch: ${n:-0}"; [ "${n:-0}" -ge 1 ] || bad=1;
    n=$(grep -c "will not divide.)$" $g/urbanopt-geojson-1.0.1/lib/urbanopt/geojson/zoning.rb 2>/dev/null); m=$(grep -c "_ZONING_CORE_FIX" /solver/patches/patch_openstudio_standards.py 2>/dev/null);
    echo "perimeter zoning guards: ${n:-0} warnings (4 when patched), runtime patch script ${m:-0}"; [ "${n:-0}" = 4 ] && [ "${m:-0}" -ge 1 ] || bad=1;
    n=$(grep -c "comstock_system_type" /solver/upload/PowerTwin.rb 2>/dev/null); echo "comstock_system_type in mapper: ${n:-0}"; [ "${n:-0}" -ge 1 ] || bad=1;
    grep -m1 "^RESOLVER_VERSION" /solver/app/modules/simulation/sim_params_spec.py | cut -c1-26;
    python3 -c "print(\"sif python OK\")" || bad=1;
    exit $bad'; then
    echo "REJECTED: flask-$tag.sif.new failed verification; left under that name, not installed"
    exit 1
  fi
  mv -f "flask-$tag.sif.new" "flask-$tag.sif"
  if [ "$AS_FLASK" = 1 ]; then
    [ -e flask.sif ] && cp -p flask.sif "flask.sif.bak_$(date +%Y%m%d_%H%M)"
    # stage and rename: a job with flask.sif mounted keeps reading the old inode, never a half-written file
    cp -p "flask-$tag.sif" flask.sif.new && mv -f flask.sif.new flask.sif
    echo "flask.sif now = flask-$tag.sif"
  fi
done
echo "[$(date '+%F %T')] done"
