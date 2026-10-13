"""The standard cluster campaign launch (vault ARCC HPC, 2026-09-30): the repo's job template is configured by the
environment with its old values as defaults, and the ops scripts parse."""
import glob
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, "apptainer", "example-sql-start.sh")


def _config(env):
    src = open(TEMPLATE).read()
    block = src[src.index("# Simulation parameters"):src.index("# Database environment variables")]
    names = "SIMULATION_NAME COLLECTION_ID URBANOPT_SIMULATION_YEAR URBANOPT_STOCHASTIC_SAMPLING SOLVER_SIF " \
            "COLLECTION_BASE METADATA_CSV_PATH USER_FILES_DIR URBANOPT_KEEP_RUN_DIR"
    out = subprocess.run(["bash", "-c", block + f'\nfor v in {names}; do echo "$v=${{!v}}"; done'],
                         env=env, capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines())


def test_defaults_are_the_old_values():
    c = _config({"PATH": os.environ["PATH"]})
    assert c["SIMULATION_NAME"] == "teton1" and c["COLLECTION_ID"] == "7" and c["URBANOPT_SIMULATION_YEAR"] == "2026"
    assert c["URBANOPT_STOCHASTIC_SAMPLING"] == "false" and c["URBANOPT_KEEP_RUN_DIR"] == "false"
    assert c["SOLVER_SIF"] == "/project/cowy-ptheory/powertwin/shared/sif_containers/flask.sif"
    assert c["METADATA_CSV_PATH"] == "/project/cowy-ptheory/powertwin/7/upload/7_metadata.csv"


def test_environment_overrides_flow_into_derived_paths():
    c = _config({"PATH": os.environ["PATH"], "SIMULATION_NAME": "tetonfl1", "COLLECTION_ID": "7702",
                 "URBANOPT_STOCHASTIC_SAMPLING": "true", "SOLVER_SIF": "/x/flask-t.sif"})
    assert c["SIMULATION_NAME"] == "tetonfl1" and c["URBANOPT_STOCHASTIC_SAMPLING"] == "true"
    assert c["COLLECTION_BASE"].endswith("/7702") and c["METADATA_CSV_PATH"].endswith("/7702/upload/7702_metadata.csv")
    assert c["SOLVER_SIF"] == "/x/flask-t.sif"


@pytest.mark.parametrize("script", sorted(glob.glob(os.path.join(REPO, "apptainer", "ops", "*.sh"))) + [TEMPLATE])
def test_ops_scripts_parse(script):
    assert subprocess.run(["bash", "-n", script]).returncode == 0


def test_collect_post_refuses_an_arm_it_cannot_collect():
    """POST=collect (surrogate training data, 2026-10-03) needs the sampler's manifest, a repo copy for the collector
    and the kept run dirs, checked before anything is staged; the collector's mode is the arm's stochastic flag."""
    src = open(os.path.join(REPO, "apptainer", "ops", "launch-campaign.sh")).read()
    guard = src.index('if [ "${POST:-consolidate}" = collect ]; then')
    assert guard < src.index('if [ "$DRY" != 1 ]; then')
    for need in ("needs MANIFEST_SRC", "needs REPO_COPY", "needs URBANOPT_KEEP_RUN_DIR=true"):
        assert need in src[guard:guard + 600], need
    col = open(os.path.join(REPO, "apptainer", "ops", "collect-surrogate.sh")).read()
    assert 'STOCH=False; [ "${URBANOPT_STOCHASTIC_SAMPLING:-false}" = true ] && STOCH=True' in col
    assert "stochastic=$STOCH" in col and '--bind "$REPO_COPY:/work"' in col
    assert 'PYTHONPATH="$PYLIB:/work/surrogate"' in col   # shapely for the resolver's county lookup
