#!/bin/bash
#SBATCH --job-name=qa_sif_pull
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --account=cowy-ptheory
#SBATCH --mail-type=NONE
#SBATCH --output=%x_%j.out
#
# Rebuild flask.sif from Docker Hub. Runs as a JOB, not on the login node: the pull
# unpacks ~1.5 GB and OOM'd at default memory before. Cache goes to /gscratch and TMPDIR
# to node-local /tmp -- the VAST-backed /project and /gscratch reject the xattrs the
# default cache writes.
set -euo pipefail

module --force purge
module load arcc/1.0 slurm gcc/14.2.0 apptainer/1.4.1

SIF_DIR="/project/cowy-ptheory/powertwin/shared/sif_containers"
export APPTAINER_CACHEDIR="/gscratch/lukemacy/apptainer_cache"
export APPTAINER_TMPDIR="/tmp/apptainer_$$"
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
trap 'rm -rf "$APPTAINER_TMPDIR"' EXIT

echo "[$(date '+%F %T')] pulling missionloyd/powertwin-solver-flask:latest"
cd "$SIF_DIR"

# build beside the live SIF, then swap: a partial download must never replace a working image
apptainer pull --force --disable-cache flask.sif.new docker://missionloyd/powertwin-solver-flask:latest

test -s flask.sif.new
apptainer exec flask.sif.new python3 -c "print('sif python OK')"
mv -f flask.sif.new flask.sif
echo "[$(date '+%F %T')] flask.sif updated: $(ls -la flask.sif)"
