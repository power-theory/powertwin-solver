#!/bin/bash
# Consolidate a finished local (docker) simulation's cleaned reports into one sensor-logs CSV.
#
#   bash run_consolidate.sh <simulation_name> <collection_id> [RESAMPLE] [WORKERS]
#
# RESAMPLE: pandas period the per-building series are summed to before export. M (monthly)
# is what the powertwin-db seed and the utility-actuals scorers consume; H keeps hourly rows
# for surrogate datasets; empty exports the report's native rows. Default M.
# WORKERS defaults to nproc-2.
#
# Runs modules/utils/consolidate_sensor_logs.py inside the running powertwin-solver-flask
# container (the same module apptainer/consolidate-state.sh runs on the cluster, so both
# paths emit the canonical sensor_id,collection_id,ts,value,metadata layout). Reads
# ${DATA_DIR}/user_files/<simulation_name>/cleaned_reports and writes
# ${DATA_DIR}/exports/<simulation_name>_sensor_logs.csv on the host (DATA_DIR from .env.local).
set -euo pipefail

SIM="${1:?usage: run_consolidate.sh <simulation_name> <collection_id> [RESAMPLE] [WORKERS]}"
CID="${2:?usage: run_consolidate.sh <simulation_name> <collection_id> [RESAMPLE] [WORKERS]}"
RESAMPLE="${3-M}"
WORKERS="${4:-$(( $(nproc) - 2 ))}"

set -o allexport
source ./.env.local
set +o allexport
DATA_DIR="${DATA_DIR:-./powertwin_data}"

if ! docker ps --format '{{.Names}}' | grep -qx powertwin-solver-flask; then
  echo "powertwin-solver-flask is not running; start it with run_docker.sh first" >&2
  exit 1
fi
if [ ! -d "${DATA_DIR}/user_files/${SIM}/cleaned_reports" ]; then
  echo "no cleaned reports at ${DATA_DIR}/user_files/${SIM}/cleaned_reports" >&2
  exit 1
fi

mkdir -p "${DATA_DIR}/exports"
OUT="${DATA_DIR}/exports/${SIM}_sensor_logs.csv"
echo "Consolidating ${SIM} (collection ${CID}, resample '${RESAMPLE}', ${WORKERS} workers) -> ${OUT}"

docker exec powertwin-solver-flask python /solver/app/modules/utils/consolidate_sensor_logs.py \
  --input-dir "/solver/powertwin-solver-pg/user_files/${SIM}/cleaned_reports" \
  --output "/solver/powertwin-solver-pg/exports/${SIM}_sensor_logs.csv" \
  --collection-id "${CID}" --resample "${RESAMPLE}" --workers "${WORKERS}"

echo "rows: $(( $(wc -l < "${OUT}") - 1 ))  sensors: $(tail -n +2 "${OUT}" | cut -d, -f1 | sort -u | wc -l)"
echo "wrote ${OUT}"
