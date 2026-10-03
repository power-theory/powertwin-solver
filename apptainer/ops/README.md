# Cluster ops (Medicine Bow)

The standard way to run a simulation campaign on the cluster. Runbook: vault `Dev/powertwin-solver/ARCC HPC`, section "Standard campaign launch".

| Script | Where it runs | What it does |
|---|---|---|
| `sync-to-cluster.sh` | exodus, from the repo | copies `apptainer/example-sql-start.sh` and these scripts to `~/ops/` on the cluster; links the documented `~/rm-job.sh`, `~/grind_trash*.py` and `~/qa_sif_pull.sh` to them |
| `pull-sif.sh <tag>...` | `sbatch` | pulls `missionloyd/powertwin-solver-flask:<tag>` into `flask-<tag>.sif` and verifies it inside the SIF; `--as-flask` also replaces `flask.sif` |
| `launch-campaign.sh <arm.env> [--after JOBID]` | login node | stages one arm's inputs into its collection dir and submits `example-sql-start.sh` with the arm's settings, plus `post-consolidate.sh` after it |
| `post-consolidate.sh` | `sbatch` (by the launcher) | monthly sensor logs like the scored runs; with `POST=asu`, per-building end uses from the kept run tree (`extract_enduse.py`, `extract_sql.py`) |
| `rm-job.sh`, `grind_trash.py`, `grind_trash2.py` | `sbatch` | the only sanctioned way to delete trees (never on the login node) |
| `archive-home.sh [--dry-run]` | `sbatch` | moves loose scripts and job logs out of `~` into `~/archive/<YYYY-MM>/`; nothing deleted |
| `qa_sif_pull.sh` | `sbatch` | legacy: pulls `:latest` over `flask.sif`; prefer `pull-sif.sh --as-flask <tag>` |

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
