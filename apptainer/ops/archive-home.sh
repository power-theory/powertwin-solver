#!/bin/bash
#SBATCH --job-name=archive-home
#SBATCH --partition=mb
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=01:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --output=/project/cowy-ptheory/powertwin/campaigns/ops-logs/%x_%j.out
# Move loose campaign scripts and job logs out of the cluster home directory into ~/archive/<YYYY-MM>/ (a job, per the
# login-node rule). Nothing is deleted. Kept in place: the scripts runbooks and memory reference by ~/ path, the ops
# links, and every directory. Dry run first:  sbatch archive-home.sh --dry-run
set -euo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
KEEP=(rm-job.sh grind_trash.py grind_trash2.py qa_sif_pull.sh patch-sql-start.sh consolidate_sensor_logs.py
  train-natv3.sh train-natv3-fp450.sh merge-natv3.sh collect-natv3.sh prep-natv3d-resubmit.sh natv3_collect_driver.py
  verify-images.sh powertwin.sh powertwin-tetonrev.sh powertwin-tetonr6pc4.sh powertwin-tetonfan1.sh
  image-droplets-clean.sh grind-trash.sh consolidate-state.sh cleanup-trash-final.sh cleanup-prune.sh cleanup-old-dirs.sh)
keep() { local k; for k in "${KEEP[@]}"; do [ "$k" = "$1" ] && return 0; done; return 1; }
A=$HOME/archive/$(date +%Y-%m); [ "$DRY" = 1 ] || mkdir -p "$A"
moved=0; kept=0
cd "$HOME"
for f in *.sh *.out *.py; do
  [ -f "$f" ] && [ ! -L "$f" ] || continue
  if keep "$f"; then kept=$((kept+1)); echo "keep $f"; continue; fi
  if [ "$DRY" = 1 ]; then echo "would move $f"; else mv -n "$f" "$A/" && echo "moved $f"; fi
  moved=$((moved+1))
done
echo "moved $moved, kept $kept (dry run: $DRY) into $A"
