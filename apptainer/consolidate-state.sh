#!/bin/bash
#==============================================================================
# consolidate-state.sh — Sensor log consolidation for a single state
#
# Submitted by state-runner.sh via:
#   sbatch --parsable \
#       --export=ALL,INPUT_DIR=...,OUTPUT_FILE=...,COLLECTION_ID=... \
#       consolidate-state.sh
#
# Required environment variables (passed via --export):
#   INPUT_DIR      — path to cleaned_reports directory
#   OUTPUT_FILE    — path for consolidated CSV output
#   COLLECTION_ID  — collection ID for the state
#
# Optional:
#   RESAMPLE       — pandas resample freq (e.g. M, H). If empty/unset, no
#                    resample flag is passed (use when data is already at
#                    target frequency, e.g. EnergyPlus Monthly reporting).
#   TYPES          — comma-separated sensor type suffixes to include
#                    (e.g. electricity,natural_gas). If empty/unset, all
#                    types are included.
#==============================================================================

#SBATCH --job-name=consol-sensor
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=0-04:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --output=consol-sensor_%j.out

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
PYTHON_SCRIPT="${REPO_ROOT}/solver/app/modules/utils/consolidate_sensor_logs.py"

module --force purge
module load arcc/1.0
module load slurm
module load miniconda3/24.3.0

RESAMPLE="${RESAMPLE:-}"
TYPES="${TYPES:-}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting consolidation"
echo "  INPUT_DIR:     ${INPUT_DIR}"
echo "  OUTPUT_FILE:   ${OUTPUT_FILE}"
echo "  COLLECTION_ID: ${COLLECTION_ID}"
echo "  RESAMPLE:      ${RESAMPLE:-<none>}"
echo "  TYPES:         ${TYPES:-<all>}"

if [ ! -f "${PYTHON_SCRIPT}" ]; then
    echo "ERROR: PYTHON_SCRIPT not found: ${PYTHON_SCRIPT}" >&2
    exit 1
fi

if [ ! -d "${INPUT_DIR}" ]; then
    echo "ERROR: INPUT_DIR does not exist: ${INPUT_DIR}" >&2
    exit 1
fi

mkdir -p "$(dirname "${OUTPUT_FILE}")"

# Build args array so --resample is omitted when not set
args=(
    --input-dir "${INPUT_DIR}"
    --output "${OUTPUT_FILE}"
    --collection-id "${COLLECTION_ID}"
)
if [ -n "${RESAMPLE}" ]; then
    args+=(--resample "${RESAMPLE}")
fi
if [ -n "${TYPES}" ]; then
    args+=(--types "${TYPES}")
fi

# `set -e` at the top means a non-zero exit from python3 aborts the script
# immediately, propagating its exit code to sbatch. We only reach the echo
# on success.
python3 "${PYTHON_SCRIPT}" "${args[@]}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Consolidation finished successfully"
