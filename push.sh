#!/bin/bash
set -e

REGISTRY="100.72.180.45:5000"
GITHUB_REMOTE="github"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
SHORT_SHA=$(git rev-parse --short HEAD)

# Compose services that get built (excludes postgres:17 which is a stock image)
COMPOSE_PROJECT="powertwin-solver"
SERVICES=("powertwin-solver-pgbouncer" "powertwin-solver-flask" "powertwin-solver-mss")

echo "=== Push & Registry Sync ==="
echo "Branch: ${BRANCH}"
echo "Commit: ${SHORT_SHA}"
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

# 3. Build images from docker-compose-prod.yml
echo ""
echo "--- Building images ---"
docker compose -f docker-compose-prod.yml build

# 4. Tag and push to local registry
# docker compose build tags images using the image: field from compose file
# e.g. 100.72.180.45:5000/powertwin-solver-flask:dev
echo ""
echo "--- Pushing to registry (${REGISTRY}) ---"
for SERVICE in "${SERVICES[@]}"; do
  REGISTRY_IMAGE="${REGISTRY}/${SERVICE}"

  echo "  ${REGISTRY_IMAGE}:${BRANCH}, :${SHORT_SHA}"
  docker tag "${REGISTRY_IMAGE}:${BRANCH}" "${REGISTRY_IMAGE}:${SHORT_SHA}"
  docker push "${REGISTRY_IMAGE}:${BRANCH}"
  docker push "${REGISTRY_IMAGE}:${SHORT_SHA}"

  if [ "${BRANCH}" = "main" ]; then
    docker tag "${REGISTRY_IMAGE}:${BRANCH}" "${REGISTRY_IMAGE}:latest"
    docker push "${REGISTRY_IMAGE}:latest"
  fi
done

echo ""
echo "=== Done ==="
echo "Pull with: docker pull ${REGISTRY}/<service>:${BRANCH}"
echo "Services: ${SERVICES[*]}"
