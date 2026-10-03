#!/bin/bash
#SBATCH --partition=mb
#SBATCH --nodes=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=200G
#SBATCH --time=06:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --mail-user=admin@powertheoryinc.com
#SBATCH --mail-type=ALL
# Post-process one campaign arm inside its own SIF (submitted by launch-campaign.sh with the arm's env):
# monthly sensor logs consolidated like the scored runs (translations on, RESAMPLE from the arm, default M) into
# <campaign>/results/<sim>_sensor_logs_<resample>.csv, and with POST=asu the per-building monthly end uses from the
# kept run tree (extract_enduse.py then extract_sql.py; needs URBANOPT_KEEP_RUN_DIR=true on the arm).
set -euo pipefail
module --force purge
module load arcc/1.0 slurm gcc/14.2.0 apptainer/1.4.1
BASE=/project/cowy-ptheory/powertwin/$COLLECTION_ID/user_files
OUT=$CAMPAIGN_DIR/results
R=${URBANOPT_RESAMPLE:-M}
run() { apptainer exec --bind /project/cowy-ptheory:/project/cowy-ptheory "$SIF" "$@"; }
echo "[$(date '+%F %T')] consolidate $SIMULATION_NAME (resample $R)"
URBANOPT_POSTPROCESS_TRANSLATIONS=true run python3 /solver/app/modules/utils/consolidate_sensor_logs.py \
  --input-dir "$BASE/$SIMULATION_NAME/cleaned_reports" --output "$OUT/${SIMULATION_NAME}_sensor_logs_${R}.csv" \
  --collection-id "$SCORE_CID" --resample "$R" --workers 46
if [ "${POST:-consolidate}" = asu ]; then
  echo "[$(date '+%F %T')] end uses $SIMULATION_NAME"
  run python3 "$HOME/ops/extract_enduse.py" "$BASE/$SIMULATION_NAME/urbanopt_simulation" "$OUT/${SIMULATION_NAME}_enduse_raw.csv"
  run python3 "$HOME/ops/extract_sql.py" "$BASE/$SIMULATION_NAME/urbanopt_simulation" "$OUT/${SIMULATION_NAME}_enduse_raw.csv" "$OUT/${SIMULATION_NAME}_enduse_monthly.csv"
fi
ls -la "$OUT" | grep "$SIMULATION_NAME"
echo "[$(date '+%F %T')] done"
