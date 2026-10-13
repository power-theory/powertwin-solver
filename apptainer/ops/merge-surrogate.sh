#!/bin/bash
#SBATCH --job-name=merge-surrogate
#SBATCH --partition=mb
#SBATCH --nodes=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=06:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --mail-user=admin@powertheoryinc.com
#SBATCH --mail-type=ALL
# Merge collected wave roots (a campaign simulated and collected in waves to fit the cluster's disk) into the one
# root the trainer reads, with one stratified split re-stamped over the merged population
# (surrogate/app/modules/data/merge_datasets.py in REPO_COPY, inside collect.sif; hard links, so the waves stay
# intact; every check runs before any write). From ~/merge-natv3.sh (2026-09-24), which merged natv3a..d.
#   sbatch --export=ALL,REPO_COPY=<repo copy>,OUT=<root name>,WAVES="<wave root> <wave root>..." ~/ops/merge-surrogate.sh
# OUT and WAVES are names under surrogate_data/.
set -euo pipefail
module --force purge
module load arcc/1.0 slurm apptainer/1.4.1
: "${REPO_COPY:?set REPO_COPY}" "${OUT:?set OUT}" "${WAVES:?set WAVES}"
S=/project/cowy-ptheory/powertwin/surrogate_data
args=()
for w in $WAVES; do [ -s "$S/$w/manifest.parquet" ] || { echo "no collected wave at $S/$w"; exit 2; }; args+=("$S/$w"); done
echo "merge $WAVES -> $S/$OUT (repo $(cat "$REPO_COPY/REVISION" 2>/dev/null))  $(date)"
apptainer exec --bind /project/cowy-ptheory/powertwin --bind "$REPO_COPY":/work --env PYTHONPATH=/work/surrogate \
  /project/cowy-ptheory/powertwin/shared/sif_containers/collect.sif \
  python -m app.modules.data.merge_datasets "$S/$OUT" "${args[@]}"
echo "merge done  $(date)"; cat "$S/$OUT/merge_report.json"
