"""WP-07 evidence set: deploy/config drift (Q/A D-55..D-59, X-1, X-2). Structural contracts over
the deploy surface so drift trips a test instead of a production surprise."""
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.path.exists(os.path.join(REPO, ".gitlab-ci.yml")):
    # Staged container copy (/qa symlink root) carries tests/ + solver/ only; the deploy
    # surface (compose/helm/apptainer/CI) lives at the repo root. These contracts run on
    # host/CI -- same pattern as test_ci_contract.
    pytest.skip("deploy surface not present here (staged container copy)", allow_module_level=True)
CI = open(os.path.join(REPO, ".gitlab-ci.yml")).read()
PROD = open(os.path.join(REPO, "docker-compose-prod.yml")).read()
DEV = open(os.path.join(REPO, "docker-compose.yml")).read()
HELM_T = open(os.path.join(REPO, "k8s", "helm", "powertwin-solver", "templates",
                           "flask-deployment.yaml")).read()
APP = open(os.path.join(REPO, "apptainer", "example-sql-start.sh")).read()
RUNNER = open(os.path.join(REPO, "apptainer", "state-runner.sh")).read()
DOCKERFILE = open(os.path.join(REPO, "solver", "Dockerfile")).read()


def test_ci_sets_carry_defaults_and_all_knobs():
    # D-55 guard + D-56: every urbanopt knob deploys through URBANOPT_SETS with a safe default.
    seg = CI.split("URBANOPT_SETS=", 1)[1].split("\n", 1)[0]
    for var, dflt in [("URBANOPT_SIMULATION_YEAR", ":-2026"), ("URBANOPT_DYNAMIC_DEFAULTS", ":-"),
                      ("URBANOPT_STOCHASTIC_SAMPLING", ":-"), ("URBANOPT_KEEP_RUN_DIR", ":-"),
                      ("URBANOPT_RUN_TIMEOUT_S", ":-")]:
        assert var in seg and f"{var}{dflt}" in seg, f"{var} missing or defaultless in URBANOPT_SETS"


def test_helm_template_carries_stochastic_sampling():
    assert "URBANOPT_STOCHASTIC_SAMPLING" in HELM_T, (
        "stochastic sampling is stranded: configured as a CI var but absent from the pod env"
    )


def test_prod_compose_honors_data_dir():
    # D-59: the documented DATA_DIR knob silently did nothing in prod.
    assert "${DATA_DIR:-./powertwin_data}" in PROD, "prod compose hardcodes ./powertwin_data"


def test_parallel_exec_gets_full_sim_config():
    # D-57: two phases of one cluster job ran under different sim config.
    seg = APP.split("run-parallel-batches", 1)[0].rsplit("srun", 1)[1]
    for var in ("URBANOPT_RESAMPLE", "URBANOPT_POSTPROCESS_TRANSLATIONS",
                "URBANOPT_RUN_TIMEOUT_S"):
        assert var in seg, f"{var} absent from the run-parallel-batches exec"


def test_all_apptainer_execs_share_gem_env():
    # X-2: create_feature_files/consolidate ran with NO gem overrides while the parallel step
    # overrode them -- ruby resolution differed between phases of one job.
    for phase in ("create-feature-files", "consolidate-databases"):
        seg = APP.split(phase, 1)[0].rsplit("apptainer exec", 1)[1]
        assert "GEM_HOME" in seg, f"{phase} exec lacks the shared GEM_HOME override"


def test_image_carries_provenance_stamp():
    # X-1: no digest/version link existed between the CI image and the hand-built cluster SIF.
    assert "BUILD_INFO" in DOCKERFILE, "image has no build provenance stamp"
    assert "BUILD_INFO" in APP, "cluster start script never surfaces the image provenance"


def test_state_runner_defaults_to_tracked_files():
    # D-58: the production entry script defaulted to untracked cluster-local files.
    # Anchor on the ACTUAL assignment line, not a line index that can drift onto a
    # comment and stay vacuously true (code-review).
    import re
    sim_lines = [l for l in RUNNER.splitlines()
                 if re.match(r'\s*SIM_SCRIPT="\$\{SIM_SCRIPT:-', l)]
    assert sim_lines, "state-runner has no overridable SIM_SCRIPT default"
    assert "example-sql-start.sh" in sim_lines[0] and "patch-sql-start.sh" not in sim_lines[0], (
        f"state-runner SIM_SCRIPT default is not the tracked start script: {sim_lines[0]!r}"
    )


def test_flask_healthcheck_is_real():
    # D-59: `test: exit 0` is a healthcheck that cannot fail, yet pg gates on it.
    assert "exit 0" not in DEV.split("healthcheck", 1)[1].split("restart", 1)[0], (
        "flask healthcheck is unconditionally healthy"
    )


def test_build_cleanup_targets_only_the_jobs_own_per_commit_image():
    # Review 2026-09-20: the after_script in the shared build template matched every sibling
    # image (flask, pgbouncer, mss run concurrently on one daemon) and the branch tag, so a fast
    # sibling untagged flask's image mid-push and a concurrent pipeline lost its :dev.
    import re
    yaml = pytest.importorskip("yaml")   # not in the flask image; the ledger tests skip the same way
    ci = yaml.safe_load(open(os.path.join(REPO, ".gitlab-ci.yml")))
    after = "\n".join(ci[".build_template"]["after_script"])
    assert "${IMAGE_NAME}" in after and "[a-z]+" not in after and "CI_COMMIT_BRANCH" not in after
    for job in ("build_flask", "build_pgbouncer", "build_mss"):
        assert ci[job]["variables"]["IMAGE_NAME"] == job.split("_", 1)[1]
    # the pattern, rendered for flask, must not match its siblings or the branch tag
    pat = re.search(r'grep -E "([^"]+)"', after).group(1)
    pat = pat.replace("${CI_PROJECT_NAME}", "powertwin-solver").replace("${IMAGE_NAME}", "flask").replace("${IMAGE_TAG}", "abc123")
    assert re.search(pat, "1.dkr.ecr.us-west-2.amazonaws.com/powertwin-solver-flask:abc123")
    for other in ("powertwin-solver-pgbouncer:abc123", "powertwin-solver-mss:abc123", "powertwin-solver-flask:dev"):
        assert not re.search(pat, "1.dkr.ecr.us-west-2.amazonaws.com/" + other), other


def test_every_flask_build_supplies_the_surrogate_context():
    # The flask image COPYs the surrogate package from a named build context (fast estimate, 2026-10-03). A build
    # without it fails with "pull access denied ... library/surrogate", which does not name the missing context.
    yaml = pytest.importorskip("yaml")   # not in the flask image; the ledger tests skip the same way
    assert "COPY --from=surrogate " in DOCKERFILE
    flask = yaml.safe_load(CI)["build_flask"]["script"]
    assert any("--build-context surrogate=./surrogate" in line and "./solver/Dockerfile" in line for line in flask)
    for name, text in (("docker-compose.yml", DEV), ("docker-compose-prod.yml", PROD)):
        build = yaml.safe_load(text)["services"]["powertwin-solver-flask"]["build"]
        assert build["context"] == "./solver" and build.get("additional_contexts", {}).get("surrogate") == "./surrogate", name
    # numba must cache outside the root-owned code: the pod runs as uid 1000 and a SIF is read-only
    assert "NUMBA_CACHE_DIR=/tmp/" in DOCKERFILE and "setpriv --reuid=1000" in DOCKERFILE


def test_fast_estimate_is_off_by_default_and_on_main():
    """Owner 2026-10-20: nat34h serves on dev first; main stays off until dev is checked end to end. The model ships as
    its own image, copied by an initContainer after an md5 check (k8s/model-image)."""
    yaml = pytest.importorskip("yaml")
    chart = os.path.join(REPO, "k8s", "helm", "powertwin-solver")
    vals = {n: yaml.safe_load(open(os.path.join(chart, f"values{n}.yaml"))) for n in ("", "-dev", "-main")}
    base = vals[""]["flask"]["surrogate"]
    assert base["modelImage"] == "" and base["modelDir"] == "" and base["modelTypeId"] == "3"
    assert not (vals["-main"]["flask"].get("surrogate") or {}).get("modelImage")
    dev = vals["-dev"]["flask"].get("surrogate") or {}
    if dev.get("modelImage"):   # a branch that turns it on names the model image and its directory
        assert dev["modelImage"].split("/")[-1].startswith("powertwin-solver-model") and dev["modelDir"]
    assert "{{- if $surOn }}" in HELM_T and "md5sum -c -s MD5SUMS" in HELM_T and "SURROGATE_MODEL_DIR" in HELM_T
    dockerfile = open(os.path.join(REPO, "k8s", "model-image", "Dockerfile")).read()
    assert "@sha256:" in dockerfile and "COPY model/ /model/" in dockerfile
