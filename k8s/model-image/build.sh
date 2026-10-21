#!/bin/bash
# Build (and with --push, push to ECR) the image of one served surrogate model (owner 2026-10-20).
#   k8s/model-image/build.sh <model_out_dir> <name> [--push]
# Stages only what serving reads (commercial/, residential/, presence/, serving_meta, compact_meta, split_meta,
# eval_test; never the pickles) under model/<name>/, writes model/MD5SUMS over every file, and builds
# <ECR>/powertwin-solver-model:<name>. The chart's flask.surrogate values then name the image (by digest) and <name>.
set -euo pipefail
SRC=${1:?model dir}; NAME=${2:?name, e.g. nat34h-47ce3b7}; PUSH=${3:-}
HERE=$(cd "$(dirname "$0")" && pwd)
REG=${ECR_REGISTRY:-343499364929.dkr.ecr.us-east-1.amazonaws.com}; REPO=powertwin-solver-model
CTX=$(mktemp -d "${TMPDIR:-/tmp}/model-image.XXXXXX"); trap 'rm -rf "$CTX"' EXIT
mkdir -p "$CTX/model/$NAME"
for f in commercial residential presence serving_meta.json compact_meta.json split_meta.json eval_test.json; do
  [ -e "$SRC/$f" ] || { echo "missing $SRC/$f"; exit 2; }
  cp -a "$SRC/$f" "$CTX/model/$NAME/"
done
(cd "$CTX/model" && find "$NAME" -type f | sort | xargs md5sum > MD5SUMS)
cp "$HERE/Dockerfile" "$CTX/"
docker build -q -t "$REG/$REPO:$NAME" "$CTX"
echo "files $(wc -l < "$CTX/model/MD5SUMS"), size $(du -sh --apparent-size "$CTX/model" | cut -f1), image $REG/$REPO:$NAME"
if [ "$PUSH" = --push ]; then
  aws ecr describe-repositories --repository-names "$REPO" >/dev/null 2>&1 || aws ecr create-repository --repository-name "$REPO" >/dev/null
  aws ecr get-login-password | docker login -u AWS --password-stdin "$REG" >/dev/null
  docker push -q "$REG/$REPO:$NAME"
  docker image inspect "$REG/$REPO:$NAME" --format '{{index .RepoDigests 0}}'
fi
