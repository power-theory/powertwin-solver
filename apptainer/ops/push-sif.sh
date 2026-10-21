#!/bin/bash
# Run on exodus from the repo: build solver flask SIFs on the cluster from the exodus GitLab registry (runbook: vault
# Dev/powertwin-solver/ARCC HPC, "Standard campaign launch"; registry: Dev/Infrastructure/Image Registry). The cluster
# cannot reach the registry, so push-image-arcc sends each image as an archive over ssh and a build-sif job builds and
# verifies the SIF on a compute node (verify-flask.sh inside it; a rejected SIF never gets its name).
#   apptainer/ops/push-sif.sh <tag> [<tag>...]       -> sif_containers/flask-<tag>.sif
#   apptainer/ops/push-sif.sh --as-flask <tag>       -> also replaces flask.sif (keeps flask.sif.bak_<date>), in a job
#                                                       after the build passes
# The image is <registry>/development/powertwin-solver/flask:<tag> (docker push it there first), or a local image of
# that name.
set -euo pipefail
OPS=$(cd "$(dirname "$0")" && pwd)
REGISTRY=${REGISTRY_URL:-$(sed -n 's/^REGISTRY_URL=//p' ~/.config/registry/creds)}
IMAGE=$REGISTRY/development/powertwin-solver/flask
SIF_DIR=/project/cowy-ptheory/powertwin/shared/sif_containers
LOGS=/project/cowy-ptheory/powertwin/campaigns/ops-logs
AS_FLASK=0
if [ "${1:-}" = "--as-flask" ]; then AS_FLASK=1; shift; fi
[ $# -ge 1 ] || { echo "usage: push-sif.sh [--as-flask] <tag> [<tag>...]"; exit 2; }
[ "$AS_FLASK" = 0 ] || [ $# = 1 ] || { echo "--as-flask takes one tag"; exit 2; }
for tag in "$@"; do
  out=$(push-image-arcc "$IMAGE:$tag" "$SIF_DIR/flask-$tag.sif" --verify "$OPS/verify-flask.sh" --submit | tee /dev/stderr)
  jid=$(sed -n 's/^Submitted batch job \([0-9][0-9]*\)$/\1/p' <<<"$out")
  [ -n "$jid" ] || { echo "no build job submitted for $tag"; exit 1; }
  echo "$tag: build-sif job $jid, log $LOGS/build-sif_$jid.out (BUILD_INFO and the checks are printed there)"
  if [ "$AS_FLASK" = 1 ]; then
    # stage and rename: a job with flask.sif mounted keeps reading the old inode, never a half-written file
    ssh -o BatchMode=yes mb "sbatch --parsable --dependency=afterok:$jid --kill-on-invalid-dep=yes \
      --job-name=install-flask-sif --account=cowy-ptheory --partition=mb --nodes=1 --cpus-per-task=1 --mem=2G \
      --time=00:30:00 --output=$LOGS/%x_%j.out --wrap 'cd $SIF_DIR && { [ ! -e flask.sif ] || cp -p flask.sif \
      flask.sif.bak_\$(date +%Y%m%d_%H%M); } && cp -p flask-$tag.sif flask.sif.new && mv -f flask.sif.new flask.sif \
      && echo flask.sif now = flask-$tag.sif'" | sed "s/^/$tag: install-flask-sif job /"
  fi
done
