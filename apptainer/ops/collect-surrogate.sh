#!/bin/bash
#SBATCH --partition=mb
#SBATCH --nodes=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --mail-user=admin@powertheoryinc.com
#SBATCH --mail-type=ALL
# Collect one campaign arm into the surrogate training format (surrogate collector.collect) inside collect.sif, with
# the repo copy REPO_COPY bound over /work (collect.sif bakes an old collector). Submitted by launch-campaign.sh with
# POST=collect. The arm needs URBANOPT_KEEP_RUN_DIR=true (the weather resolver reads each run's in.osw) and a
# MANIFEST_SRC (the sampler's <batch>_resolved_inputs.json, staged as <sim>_resolved_inputs.json). The collector's
# stochastic flag is the arm's URBANOPT_STOCHASTIC_SAMPLING, and collect() refuses a manifest stamped otherwise.
set -euo pipefail
module --force purge
module load arcc/1.0 slurm apptainer/1.4.1
: "${REPO_COPY:?set REPO_COPY (a repo copy at the commit the training uses) in the arm env}"
P=/project/cowy-ptheory/powertwin
R=$P/$COLLECTION_ID/user_files/$SIMULATION_NAME
OUT=$P/surrogate_data/${COLLECT_OUT:-$SIMULATION_NAME}
MAN=$P/$COLLECTION_ID/upload/${SIMULATION_NAME}_resolved_inputs.json
STOCH=False; [ "${URBANOPT_STOCHASTIC_SAMPLING:-false}" = true ] && STOCH=True
mkdir -p "$OUT"
echo "[$(date '+%F %T')] collect $SIMULATION_NAME -> $OUT (stochastic $STOCH, repo $REPO_COPY: $(cat "$REPO_COPY/REVISION" 2>/dev/null))"
apptainer exec --bind "$P" --bind "$REPO_COPY:/work" --env PYTHONPATH=/work/surrogate \
  --env URBANOPT_SIMULATION_YEAR="${URBANOPT_SIMULATION_YEAR:-2026}" "$P/shared/sif_containers/collect.sif" python3 - <<PY
import time
from app.modules.data.collector import collect, make_osw_weather_resolver
t0 = time.time()
wr = make_osw_weather_resolver(sim_output_dir="$R", weather_dir="$P/$COLLECTION_ID/powertwin_data/$SIMULATION_NAME/urbanopt_simulation/weather")
res = collect("$MAN", "$R/cleaned_reports", "$OUT", weather_resolver=wr, stochastic=$STOCH)
print("COLLECT RESULT [$SIMULATION_NAME]:", res, "in %.1f min" % ((time.time() - t0) / 60))
PY
echo "[$(date '+%F %T')] collect done"
