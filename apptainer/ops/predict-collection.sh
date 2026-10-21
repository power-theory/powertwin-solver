#!/bin/bash
#SBATCH --partition=mb
#SBATCH --nodes=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=96G
#SBATCH --time=12:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --mail-user=admin@powertheoryinc.com
#SBATCH --mail-type=ALL
# Fast-estimate predictions for one collection in parallel chunks (predict_collection.py, inside the solver image).
#   exodus:  RUNTIME=docker IMAGE=<flask image> predict-collection.sh <collection_dir> <cid> <out_dir> --collection-id ID [opts]
#   cluster: sbatch predict-collection.sh ... with SIF=<flask .sif> (RUNTIME=apptainer, the default under SLURM)
# Needs MODEL_DIR (a served model: commercial/, residential/, presence/, serving_meta.json) and WEATHER_DIR (the station
# EPWs, <title>/<title>.epw). Options pass through: --mode logs|gate, --resample, --chunk, --workers, --threads.
# Timestamps follow URBANOPT_POSTPROCESS_TRANSLATIONS (default true, as dev, main and the benchmark run).
set -euo pipefail
COLL=$(readlink -f "${1:?collection_dir}"); CID=${2:?cid}; OUT=${3:?out_dir}; shift 3
mkdir -p "$OUT"; OUT=$(readlink -f "$OUT")
: "${MODEL_DIR:?MODEL_DIR}"; : "${WEATHER_DIR:?WEATHER_DIR}"
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
RUNTIME=${RUNTIME:-$([ -n "${SLURM_JOB_ID:-}" ] && echo apptainer || echo docker)}
ENVS=(SURROGATE_MODEL_DIR=/model URBANOPT_SIMULATION_YEAR=${URBANOPT_SIMULATION_YEAR:-2026}
      URBANOPT_POSTPROCESS_TRANSLATIONS=${URBANOPT_POSTPROCESS_TRANSLATIONS:-true} HOME=/tmp NUMBA_CACHE_DIR=/tmp/numba
      CUDA_VISIBLE_DEVICES= PYTHONUNBUFFERED=1)
echo "[$(date '+%F %T')] predict $CID ($RUNTIME): $COLL -> $OUT"
if [ "$RUNTIME" = docker ]; then
  : "${IMAGE:?IMAGE (the flask image)}"
  E=(); for e in "${ENVS[@]}"; do E+=(-e "$e"); done
  docker run --rm --name "predict-$CID-$$" --network none -u "$(id -u):$(id -g)" ${MEMORY:+--memory "$MEMORY"} \
    -v "$COLL":"$COLL":ro -v "$OUT":"$OUT" -v "$(readlink -f "$MODEL_DIR")":/model:ro \
    -v "$(readlink -f "$WEATHER_DIR")":/solver/app/urbanopt/weather_files:ro -v "$HERE":/ops:ro "${E[@]}" -w /solver \
    --entrypoint python "$IMAGE" -B /ops/predict_collection.py "$COLL" "$CID" "$OUT" "$@"
else
  : "${SIF:?SIF (the flask .sif)}"
  module --force purge 2>/dev/null || true; module load arcc/1.0 apptainer/1.4.1 2>/dev/null || true
  E=(); for e in "${ENVS[@]}"; do E+=(--env "$e"); done
  apptainer exec --bind /project/cowy-ptheory --bind "$(readlink -f "$MODEL_DIR")":/model \
    --bind "$(readlink -f "$WEATHER_DIR")":/solver/app/urbanopt/weather_files --bind "$HERE":/ops "${E[@]}" --pwd /solver \
    "$SIF" python -B /ops/predict_collection.py "$COLL" "$CID" "$OUT" "$@"
fi
echo "[$(date '+%F %T')] done"
