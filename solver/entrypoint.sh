#!/usr/bin/env bash
set -e

GEM_HOME_DIR="${GEM_HOME:-/usr/local/lib/ruby/gems/3.2.2}"
MARKER="openstudio-common-measures"

if ! ls "${GEM_HOME_DIR}/gems" 2>/dev/null | grep -q "^${MARKER}-"; then
  echo "[entrypoint] ${MARKER} missing from ${GEM_HOME_DIR} — re-warming gems"
  rm -f /usr/local/bin/bundle /usr/local/bin/bundler
  gem install --no-document bundler -v 2.4.10 || true
  uo create --project-folder /tmp/prewarm
  cd /tmp/prewarm && bundle install --jobs 4 --retry 3
  gem install ruby2_keywords -v 0.0.5 || true
  cd / && rm -rf /tmp/prewarm
fi

exec "$@"
