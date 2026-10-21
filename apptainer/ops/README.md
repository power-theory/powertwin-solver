# Cluster ops (Medicine Bow)

The standard way to run a simulation campaign on the cluster. Runbook: vault `Dev/powertwin-solver/ARCC HPC`, section "Standard campaign launch".

| Script | Where it runs | What it does |
|---|---|---|
| `sync-to-cluster.sh` | exodus, from the repo | copies `apptainer/example-sql-start.sh` and these scripts to `~/ops/` on the cluster; links the documented `~/rm-job.sh` and `~/grind_trash*.py` to them |
| `push-sif.sh <tag>...` | exodus, from the repo | sends `development/powertwin-solver/flask:<tag>` from the exodus GitLab registry to the cluster (`push-image-arcc`; the cluster cannot reach the registry), where a `build-sif` job builds `flask-<tag>.sif` and runs `verify-flask.sh` inside it before it gets its name; `--as-flask` also replaces `flask.sif` in a job after the build passes |
| `launch-campaign.sh <arm.env> [--after JOBID]` | login node | stages one arm's inputs into its collection dir and submits `example-sql-start.sh` with the arm's settings, plus `post-consolidate.sh` after it |
| `post-consolidate.sh` | `sbatch` (by the launcher) | monthly sensor logs like the scored runs; with `POST=asu`, per-building end uses from the kept run tree (`extract_enduse.py`, `extract_sql.py`) |
| `collect-surrogate.sh` | `sbatch` (by the launcher, `POST=collect`) | collects a kept-run-dir arm into surrogate training data in `collect.sif` against `REPO_COPY`, with the arm's stochastic mode |
| `rm-job.sh`, `grind_trash.py`, `grind_trash2.py` | `sbatch` | the only sanctioned way to delete trees (never on the login node) |
| `archive-home.sh [--dry-run]` | `sbatch` | moves loose scripts and job logs out of `~` into `~/archive/<YYYY-MM>/`; nothing deleted |

Every campaign lives in `/project/cowy-ptheory/powertwin/campaigns/<name>/`: `arms/*.env`, `logs/`, `results/`, `inputs.md5`, `jobs.tsv`. Nothing lands in `~`.

An arm env file (example):

```
CAMPAIGN=floorfit-20260930
SIMULATION_NAME=tetonfl1
COLLECTION_ID=7702                 # a fresh collection dir per arm; building ids are baked into the inputs
SIF_TAG=9d8610a-floorfit-dirty
METADATA_SRC=/path/to/7_metadata.csv
GEOJSON_SRC=/path/to/7_asset_geometries.geojson
SCORE_CID=7
URBANOPT_SIMULATION_YEAR=2026
URBANOPT_REPORTING_FREQUENCY=Hourly
URBANOPT_RESAMPLE=M
URBANOPT_DYNAMIC_DEFAULTS=true
URBANOPT_STOCHASTIC_SAMPLING=true  # the benchmark manifest's setting for the stock
NODES=8
CPUS=48
```
