#!/bin/bash
#SBATCH --job-name=cleanup-prune
#SBATCH --output=%x_%j.out
#SBATCH --account=cowy-ptheory
#SBATCH --partition=teton
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=8G
#SBATCH --time=7-00:00:00

set -uo pipefail

TARGET="${1:?Usage: sbatch cleanup-prune.sh /path/to/target [DEPTH]}"
DEPTH="${2:-2}"

if [ ! -d "${TARGET}" ]; then
    echo "ERROR: ${TARGET} is not a directory"
    exit 1
fi

echo "Pruning ${TARGET} at depth ${DEPTH} ($(date))"
echo "Enumerating branches..."

mapfile -t branches < <(find "${TARGET}" -mindepth "${DEPTH}" -maxdepth "${DEPTH}" -type d 2>/dev/null)
total=${#branches[@]}
echo "Found ${total} branches to prune"

removed=0
for dir in "${branches[@]}"; do
    rm -rf "${dir}" 2>/dev/null &
    # Throttle to --cpus-per-task parallel rm processes
    if (( $(jobs -rp | wc -l) >= 32 )); then
        wait -n
    fi
    removed=$((removed + 1))
    if (( removed % 50 == 0 )); then
        echo "Pruned ${removed}/${total} branches ($(date +%H:%M:%S))"
    fi
done
wait

echo "Branches pruned at $(date), removing empty directories..."
find "${TARGET}" -depth -type d -empty -delete 2>/dev/null
echo "Done at $(date)"
ls -la "${TARGET}" 2>&1
