#!/bin/bash
#==============================================================================
# surrogate-train.sh — Train the PowerTwin hourly surrogate on ARCC mb-h100.
#
# Submit:  sbatch apptainer/surrogate-train.sh /path/to/training_dataset
# The dataset dir is the collector output (manifest.parquet + targets/ + weather/).
#==============================================================================
#SBATCH --job-name=surrogate-train
#SBATCH --partition=mb-h100
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:8
#SBATCH --cpus-per-task=32
#SBATCH --time=08:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --output=surrogate-train_%j.out

set -euo pipefail

DATASET="${1:?usage: sbatch surrogate-train.sh <dataset_root> [epochs]}"
EPOCHS="${2:-100}"

module --force purge
module load arcc/1.0 slurm miniconda3/24.3.0

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${REPO_ROOT}"

NGPU=$(nvidia-smi -L | wc -l)
echo "[$(date '+%F %T')] training surrogate: dataset=${DATASET} epochs=${EPOCHS} gpus=${NGPU}"

# One process per GPU (DDP). batch-size is PER-GPU.
torchrun --standalone --nproc_per_node="${NGPU}" -m surrogate.train \
    --data "${DATASET}" \
    --out "${DATASET}/checkpoints" \
    --epochs "${EPOCHS}" \
    --batch-size 32 \
    --lr 1e-3 \
    --val-every 5

echo "[$(date '+%F %T')] done -> ${DATASET}/checkpoints/best.pt"
