#!/bin/bash
# Submit one campaign arm from an env file (runbook: vault Dev/powertwin-solver/ARCC HPC, "Standard campaign launch").
# Run on the login node: it only makes a few directories, copies the arm's two input files and calls sbatch.
#   ~/ops/launch-campaign.sh <arm.env> [--after JOBID]
# The env file sets (required) CAMPAIGN SIMULATION_NAME COLLECTION_ID SIF_TAG METADATA_SRC GEOJSON_SRC, the
# URBANOPT_* settings the arm needs (the job template's defaults otherwise), and optionally NODES CPUS MEM TIME
# PARTITION (defaults 8 / 48 / 200G / 3-00:00:00 / mb), QOS (a TIME over 3 days needs long), EXCLUDE (nodes kept off
# the arm; it replaces the job template's --exclude list, so repeat those nodes), POST_PARTITION (the
# post job's partition; default its script's), POST=consolidate|asu|none (default consolidate) and
# SCORE_CID (the collection id stamped into the consolidated logs; default COLLECTION_ID). POST=collect instead
# collects the arm into surrogate training data (collect-surrogate.sh; needs MANIFEST_SRC, REPO_COPY, KEEP_RUN_DIR).
# Layout: /project/cowy-ptheory/powertwin/campaigns/<CAMPAIGN>/{arms/<arm>.env, logs/, results/, jobs.tsv}.
set -euo pipefail
# LAUNCH_DRY_RUN=1: check the env file, the SIF and the run dirs, print the sbatch commands, stage and submit nothing.
DRY=${LAUNCH_DRY_RUN:-0}
sb() { if [ "$DRY" = 1 ]; then echo "DRY sbatch $*" >&2; echo "DRYJOB"; else sbatch "$@"; fi; }
ENV_FILE=${1:?usage: launch-campaign.sh <arm.env> [--after JOBID]}
AFTER=""
if [ "${2:-}" = "--after" ]; then AFTER=${3:?--after needs a job id}; fi
set -a; source "$ENV_FILE"; set +a
for v in CAMPAIGN SIMULATION_NAME COLLECTION_ID SIF_TAG METADATA_SRC GEOJSON_SRC; do
  [ -n "${!v:-}" ] || { echo "missing $v in $ENV_FILE"; exit 2; }
done
P=/project/cowy-ptheory/powertwin
C=$P/campaigns/$CAMPAIGN
OPS=$HOME/ops
SIF=$P/shared/sif_containers/flask-$SIF_TAG.sif
[ -s "$SIF" ] || { echo "no SIF $SIF: sbatch ~/ops/pull-sif.sh $SIF_TAG first"; exit 2; }
COLL=$P/$COLLECTION_ID
for d in "$COLL/powertwin_data/$SIMULATION_NAME" "$COLL/user_files/$SIMULATION_NAME"; do
  [ ! -e "$d" ] || { echo "refusing: $d exists (a reused run dir stalls the job); pick a new SIMULATION_NAME or collection dir"; exit 2; }
done
MD="$COLL/upload/${COLLECTION_ID}_metadata.csv"; GJ="$COLL/upload/${COLLECTION_ID}_asset_geometries.geojson"
[ -s "$METADATA_SRC" ] && [ -s "$GEOJSON_SRC" ] || { echo "missing input: $METADATA_SRC / $GEOJSON_SRC"; exit 2; }
if [ "${POST:-consolidate}" = collect ]; then
  [ -s "${MANIFEST_SRC:-}" ] || { echo "POST=collect needs MANIFEST_SRC"; exit 2; }
  [ -d "${REPO_COPY:-}/surrogate" ] || { echo "POST=collect needs REPO_COPY (a repo copy with surrogate/)"; exit 2; }
  [ "${URBANOPT_KEEP_RUN_DIR:-false}" = true ] || { echo "POST=collect needs URBANOPT_KEEP_RUN_DIR=true"; exit 2; }
fi
if [ "$DRY" != 1 ]; then
  mkdir -p "$C/arms" "$C/logs" "$C/results" "$COLL/upload" "$COLL/powertwin_data" "$COLL/user_files"
  cp "$ENV_FILE" "$C/arms/$SIMULATION_NAME.env"
  cp "$METADATA_SRC" "$MD"; cp "$GEOJSON_SRC" "$GJ"
  md5sum "$MD" "$GJ" >> "$C/inputs.md5"
  if [ -n "${MANIFEST_SRC:-}" ]; then
    cp "$MANIFEST_SRC" "$COLL/upload/${SIMULATION_NAME}_resolved_inputs.json"
    md5sum "$COLL/upload/${SIMULATION_NAME}_resolved_inputs.json" >> "$C/inputs.md5"
  fi
fi
DEP=(); [ -n "$AFTER" ] && DEP=(--dependency=afterok:$AFTER)
QOSARG=(); [ -n "${QOS:-}" ] && QOSARG=(--qos="$QOS")
POSTPART=(); [ -n "${POST_PARTITION:-}" ] && POSTPART=(--partition="$POST_PARTITION")
EXCLARG=(); [ -n "${EXCLUDE:-}" ] && EXCLARG=(--exclude="$EXCLUDE")
arm=$(sb --parsable "${DEP[@]}" "${QOSARG[@]}" "${EXCLARG[@]}" --job-name="powertwin-$SIMULATION_NAME" --partition="${PARTITION:-mb}" \
  --nodes="${NODES:-8}" --ntasks-per-node=1 --cpus-per-task="${CPUS:-48}" --mem="${MEM:-200G}" --time="${TIME:-3-00:00:00}" \
  --no-requeue --mail-user=admin@powertheoryinc.com --mail-type=ALL --output="$C/logs/%x_%j.out" \
  --export=ALL,SOLVER_SIF="$SIF",METADATA_CSV_PATH="$MD",ASSET_GEOJSON_PATH="$GJ" "$OPS/example-sql-start.sh")
post="-"
if [ "${POST:-consolidate}" = collect ]; then
  post=$(sb --parsable "${POSTPART[@]}" --dependency=afterok:$arm --job-name="collect-$SIMULATION_NAME" \
    --output="$C/logs/%x_%j.out" --export=ALL "$OPS/collect-surrogate.sh")
elif [ "${POST:-consolidate}" != none ]; then
  post=$(sb --parsable "${POSTPART[@]}" --dependency=afterok:$arm --job-name="post-$SIMULATION_NAME" \
    --output="$C/logs/%x_%j.out" --export=ALL,CAMPAIGN_DIR="$C",SIF="$SIF",SCORE_CID="${SCORE_CID:-$COLLECTION_ID}" \
    "$OPS/post-consolidate.sh")
fi
[ "$DRY" = 1 ] || printf '%s\t%s\t%s\t%s\t%s\n' "$(date '+%F %T')" "$SIMULATION_NAME" "$arm" "$post" "$SIF_TAG" >> "$C/jobs.tsv"
echo "$SIMULATION_NAME arm $arm post $post (logs: $C/logs)"
