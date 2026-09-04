#!/bin/bash
# Stage the tests/ suite into the running powertwin-solver-flask container and print the run
# recipe. The trap this encodes (Q/A campaign WP-01): tests compute REPO as parent-of-tests and
# expect REPO/solver/..., so naive staging into /solver produces hundreds of collection errors;
# and weather.py opens cwd-relative resources, so the requires_container stage must run from
# /solver. e2e modules are serialized on purpose (shared data dir + env mutation).
set -euo pipefail
C=powertwin-solver-flask
docker exec "$C" sh -c 'rm -rf /qa && mkdir -p /qa && ln -s /solver /qa/solver'
docker cp "$(dirname "$0")/.." "$C":/qa/tests
echo "staged. Run:"
echo "  host layer : docker exec $C sh -c 'cd /qa && python -m pytest tests/ -n \$(python -c \"import multiprocessing,os; print(max(1,int(os.environ.get(\\\"SLURM_CPUS_PER_TASK\\\", multiprocessing.cpu_count()))-2))\") --dist loadfile -m \"not requires_container\" -q'"
echo "  e2e (SERIAL): docker exec $C sh -c 'cd /solver && python -m pytest /qa/tests -m requires_container -q'"
