#!/bin/bash
set -e

if [ -f .env.local ]; then
  set -a; source .env.local; set +a
fi

GITHUB_REMOTE="github"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
SHORT_SHA=$(git rev-parse --short HEAD)

# DISABLED with the registry-push block below (CI now builds images to ECR):
# COMPOSE_PROJECT="powertwin-solver"
# SERVICES=("powertwin-solver-pgbouncer" "powertwin-solver-flask" "powertwin-solver-mss")

echo "=== Push ==="
echo "Branch: ${BRANCH}"
echo "Commit: ${SHORT_SHA}"
echo ""

# 0. Pre-push resolver tests
echo "--- Running resolver tests ---"
if ! python3 tests/test_resolvers.py; then
  echo ""
  echo "RESOLVER TESTS FAILED -- push aborted."
  exit 1
fi

# 0b. Sync README version from RESOLVER_VERSION
VERSION=$(python3 -c "
import importlib.util, os
s = importlib.util.spec_from_file_location('sps', 'solver/app/modules/simulation/sim_params_spec.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(m.RESOLVER_VERSION)
")
sed -i "s/^# PowerTwin Solver v.*/# PowerTwin Solver v${VERSION}/" README.md
if ! git diff --quiet README.md; then
  git add README.md
  git commit -m "docs: bump README version to v${VERSION}"
  SHORT_SHA=$(git rev-parse --short HEAD)
  echo "  README updated to v${VERSION}"
fi
echo ""

# 1. Push to GitLab
echo "--- Pushing to GitLab (origin) ---"
git push origin "${BRANCH}"

# 2. Push to GitHub
if git remote get-url "${GITHUB_REMOTE}" &>/dev/null; then
  echo "--- Pushing to GitHub (${GITHUB_REMOTE}) ---"
  git push "${GITHUB_REMOTE}" "${BRANCH}"
else
  echo "--- Skipping GitHub (remote '${GITHUB_REMOTE}' not configured) ---"
fi

# ---------------------------------------------------------------------------
# DISABLED (2026-07): building images and pushing to the local ${DOCKER_REGISTRY}
# is now redundant. The promoted .gitlab-ci.yml builds flask/pgbouncer/mss and
# pushes them to ECR in-pipeline (on dev/main push), then deploys to EKS via
# helm. This block only fed the legacy docker-compose deploy. Re-enable if you
# run that path against the local registry.
# ---------------------------------------------------------------------------
# # 3. Build images from docker-compose-prod.yml
# echo ""
# echo "--- Building images ---"
# export CI_COMMIT_BRANCH="${BRANCH}"
# docker compose -f docker-compose-prod.yml build
#
# # 4. Tag and push to local registry
# echo ""
# echo "--- Pushing to registry (${DOCKER_REGISTRY}) ---"
# for SERVICE in "${SERVICES[@]}"; do
#   REGISTRY_IMAGE="${DOCKER_REGISTRY}/${SERVICE}"
#   echo "  ${REGISTRY_IMAGE}:${BRANCH}, :${SHORT_SHA}"
#   docker tag "${REGISTRY_IMAGE}:${BRANCH}" "${REGISTRY_IMAGE}:${SHORT_SHA}"
#   docker push "${REGISTRY_IMAGE}:${BRANCH}"
#   docker push "${REGISTRY_IMAGE}:${SHORT_SHA}"
#   if [ "${BRANCH}" = "main" ]; then
#     docker tag "${REGISTRY_IMAGE}:${BRANCH}" "${REGISTRY_IMAGE}:latest"
#     docker push "${REGISTRY_IMAGE}:latest"
#   fi
# done

echo ""
echo "=== Done (pushed to git; CI builds images and deploys) ==="
