#!/bin/bash
# Run on exodus from the repo: copy the job template and ops scripts to the cluster's ~/ops/ (the only place campaign
# launches run from), then point the documented legacy paths (~/rm-job.sh, ~/grind_trash.py, ~/grind_trash2.py,
# ~/qa_sif_pull.sh) at ~/ops/ copies, but only where the home file is byte-identical, never over a diverged one.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
ssh -o BatchMode=yes mb 'mkdir -p ~/ops /project/cowy-ptheory/powertwin/campaigns/ops-logs'
scp -q "$REPO/apptainer/example-sql-start.sh" "$REPO"/apptainer/ops/*.sh "$REPO"/apptainer/ops/*.py mb:ops/
ssh -o BatchMode=yes mb 'cd ~ && chmod +x ops/*.sh && for f in rm-job.sh grind_trash.py grind_trash2.py qa_sif_pull.sh; do
  if [ -L "$f" ]; then echo "link ok: $f"; elif [ ! -e "$f" ] || cmp -s "$f" "ops/$f"; then ln -sfn "ops/$f" "$f" && echo "linked: $f -> ops/$f"; else echo "DIFFERS, left alone: $f"; fi; done;
  md5sum ops/* | cut -c1-8 | paste -sd" "'
