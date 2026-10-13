#!/bin/bash
#SBATCH --job-name=surrogate-train
#SBATCH --partition=mb-h100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=64
#SBATCH --mem=400G
#SBATCH --time=2-00:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --mail-user=admin@powertheoryinc.com
#SBATCH --mail-type=ALL
# Train the production baseline surrogate (PCA-256 + xgboost per workflow, plus its stream presence gate) on a
# collected dataset, score the TEST split, compile it to compact format 3 and write serving_meta.json, so the model
# directory is what the solver's fast estimate loads. From ~/train-natv3.sh (2026-09-24), which established:
#  mb-h100 partition (memory; the fit is CPU-only, one GPU because the partition allocates by GPU), 400G, 2 days
#  (below the preemptible 3-day QoS), SURROGATE_TREE_JOBS=64, node-local staging when it fits twice over, a 45-minute
#  smoke fit first (xgboost under joblib has hung), and the repo copy bound over the image's baked code.
#   sbatch --export=ALL,DATA=<dataset dir>,REPO_COPY=<repo copy>,MODEL_NAME=<name>[,OUT=<dir>] ~/ops/train-surrogate.sh
# FINISH_ONLY=1 skips the fits and the test split and only compiles an already trained OUT and writes its
# serving_meta.json (nat33h's 23710995 trained and scored but stopped at the compact step, 2026-10-06).
set -euo pipefail
module --force purge
module load arcc/1.0 slurm apptainer/1.4.1
: "${DATA:?set DATA (a collected dataset)}" "${REPO_COPY:?set REPO_COPY}" "${MODEL_NAME:?set MODEL_NAME}"
SRC=$DATA
OUT=${OUT:-$SRC/tree_ckpt}
# Dockerfile.train with pinned versions (2026-10-12): those of train-xgb-99f77e9-dirty.sif, which trained natv3 and
# nat33h but has no numba, so compact_trees could not run in it, plus numba 0.68.0 (the serving image's pin)
SIF=${TRAIN_SIF:-/project/cowy-ptheory/powertwin/shared/sif_containers/train-xgb-numba-fbdb044-dirty.sif}
TMPD=${TMPDIR:-/tmp}
JOBTMP=$TMPD/surrtrain_${SLURM_JOB_ID}
LOCAL=$JOBTMP/data
SMOKE=$JOBTMP/smoke
handle_termination() {
  local sig=$1
  trap - SIGTERM SIGINT SIGHUP EXIT
  echo "cleanup on ${sig}: removing $JOBTMP  $(date)"
  rm -rf "$JOBTMP"
  [ "$sig" = EXIT ] || exit 143
}
trap 'handle_termination SIGTERM' SIGTERM
trap 'handle_termination SIGINT' SIGINT
trap 'handle_termination SIGHUP' SIGHUP
trap 'handle_termination EXIT' EXIT
[ -f "$SRC/manifest.parquet" ] || { echo "no dataset at $SRC"; exit 2; }
mkdir -p "$JOBTMP/tmp"
APPT=(apptainer exec --bind /project/cowy-ptheory/powertwin --bind "$REPO_COPY":/repo --bind "$TMPD"
      --env PYTHONPATH=/repo --env SURROGATE_TREE_JOBS=64 --env NUMBA_CACHE_DIR="$JOBTMP/numba"
      --env TMPDIR="$JOBTMP/tmp" --env JOBLIB_TEMP_FOLDER="$JOBTMP/tmp" "$SIF")
echo "repo copy $(cat "$REPO_COPY/REVISION" 2>/dev/null), image $SIF, node $(hostname)  $(date)"
"${APPT[@]}" python -c "import numba, xgboost, sklearn" || { echo "image lacks numba, xgboost or scikit-learn"; exit 2; }
if [ "${FINISH_ONLY:-0}" = 1 ]; then
  [ -f "$OUT/eval_test.json" ] || { echo "FINISH_ONLY needs a trained and scored $OUT"; exit 2; }
else
need=$(du -s --apparent-size -BG "$SRC" | cut -f1 | tr -dc 0-9)
free=$(df -BG --output=avail "$TMPD" | tail -1 | tr -dc 0-9)
DATA_RUN=$SRC
if [ "$free" -gt $(( need * 2 )) ]; then
  mkdir -p "$LOCAL"; cp -r "$SRC/manifest.parquet" "$SRC/weather" "$SRC/targets" "$LOCAL/"; DATA_RUN=$LOCAL
  echo "staged ${need}G to $LOCAL  $(date)"
fi
echo "== smoke fit  $(date)"
timeout 45m "${APPT[@]}" python -m surrogate.train --data "$DATA_RUN" --out "$SMOKE" --model xgboost --n-components 8 --train-subsample 500
echo "== production fit (PCA-256 + xgboost, presence gate)  $(date)"
"${APPT[@]}" python -m surrogate.train --data "$DATA_RUN" --out "$OUT" --model xgboost --n-components 256
echo "== test split  $(date)"
"${APPT[@]}" python -m surrogate.eval_tree --data "$DATA_RUN" --ckpt "$OUT" --split test
fi
echo "== compact format 3  $(date)"
"${APPT[@]}" python -m surrogate.app.modules.models.compact_trees "$OUT" "$OUT"
echo "== serving_meta.json  $(date)"
"${APPT[@]}" python - "$SRC" "$OUT" "$MODEL_NAME" <<'PY'
import json, os, sys
from surrogate.app.modules.serving import write_serving_meta
src, out, name = sys.argv[1:4]
ev = json.load(open(os.path.join(out, "eval_test.json"))) if os.path.exists(os.path.join(out, "eval_test.json")) else {}
e = (ev.get("monthly") or {}).get("electricity") or {}
acc = (f"test split, electricity monthly CV(RMSE) {e.get('cvrmse', float('nan')):.1f}% and NMBE {e.get('nmbe', float('nan')):+.1f}% "
       f"against the Solver on {ev.get('n', '?')} held-out buildings") if e else "test split: see eval_test.json"
print(write_serving_meta(out, src, name, acc, sim_year=2026, stochastic_sampling=False, dynamic_defaults=True))
PY
echo "train done  $(date)"; ls -la "$OUT"
