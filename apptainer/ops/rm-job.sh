#!/bin/bash
#SBATCH --job-name=rm-job
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=1-00:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --output=rm-job_%j.out
# Delete one or more trees on a compute node. NEVER rm -rf a tree on the login node.
#   sbatch ~/rm-job.sh /project/cowy-ptheory/powertwin/surrogate_data/<old> [more paths...]
# Each path is renamed into powertwin_trash/rmjob_<jobid>/ first (a cheap rename, so the
# name disappears immediately and a re-run cannot double-delete), then ground by
# grind_trash2.py (parallel rm -rf that streams entries, so a flat directory of millions of
# subdirectories cannot exhaust memory). Accepts only paths strictly inside
# /project/cowy-ptheory/powertwin or a powertwin_* sibling, and refuses any target that is a
# keep path, inside one, or contains one.
# RMJOB_DRY=1 prints every decision and moves or deletes nothing.
set -uo pipefail
P=$(readlink -f "${RMJOB_PROJECT:-/project/cowy-ptheory}")   # on Medicine Bow /project -> /cluster/medbow/project
R=$P/powertwin
T=$P/powertwin_trash/rmjob_${SLURM_JOB_ID:-manual}
DRY=${RMJOB_DRY:-0}
PY=$(command -v python3)
say() { echo "[$(date "+%F %T")] $*"; }
free_gb() { df -B1G --output=avail "$P" | tail -1 | tr -d " "; }
# compared after readlink -f, the same form the targets take
KEEP=("$R/shared" "$R/7" "$R/9995/upload" "$R/surrogate_data/nat80v4_wa2" "$R/surrogate_data/nat80v4_wb" "$R/surrogate_data/nat80v4_wc")
[ $# -ge 1 ] || { echo "usage: sbatch rm-job.sh <path>..."; exit 2; }
[ "$DRY" = 1 ] || mkdir -p "$T"
say "START free: $(free_gb) GB; targets: $*"
staged=0
for p in "$@"; do
  p=$(readlink -f "$p") || { say "SKIP unresolved: $p"; continue; }
  case "$p" in
    "$R"/*) ;;
    "$P"/powertwin_*/*) ;;
    *) say "REFUSE not strictly inside $R or a powertwin_* sibling: $p"; continue ;;
  esac
  case "$p" in "$P"/powertwin_trash|"$P"/powertwin_trash/*) say "REFUSE trash itself: $p"; continue ;; esac
  for k in "${KEEP[@]}"; do
    case "$k" in "$p"|"$p"/*) say "REFUSE contains or is keep-path $k: $p"; continue 2 ;; esac
    case "$p" in "$k"/*) say "REFUSE inside keep-path $k: $p"; continue 2 ;; esac
  done
  [ -e "$p" ] || { say "SKIP missing: $p"; continue; }
  if [ "$DRY" = 1 ]; then say "WOULD STAGE $p"; continue; fi
  mv "$p" "$T/$(basename "$p")" && { say "staged $p"; staged=$((staged + 1)); }
done
[ "$DRY" = 1 ] && exit 0
if [ "$staged" -gt 0 ]; then
  say "grinding $T (free: $(free_gb) GB)"
  GRIND_FS="$P" "$PY" "$HOME/grind_trash2.py" "$T" 4 32
fi
rm -rf "$T"
say "FINAL free: $(free_gb) GB"
