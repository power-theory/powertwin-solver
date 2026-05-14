#!/bin/bash
#==============================================================================
# state-runner.sh — Sequential state-by-state HPC simulation orchestrator
#
# Submits one PowerTwin simulation per state, waits for completion, runs
# sensor log consolidation, cleans up sim data, and moves to the next state.
#
# Runs as its own sbatch job (sbatch-within-sbatch pattern). Uses squeue/sacct
# polling to wait on child jobs since SLURM --dependency doesn't fit this flow.
#
# Usage:
#   sbatch state-runner.sh [options]
#
# Options:
#   --dry-run              Validate config and exit without submitting jobs
#   --start-from STATE     Skip states before STATE (resumability)
#   --limit N              Stop after processing N states (0 = unlimited).
#                          Use `--limit 2` as a smoke test to validate the
#                          full sim+consolidate+cleanup pipeline on the first
#                          two states before launching a full 51-state run.
#   --sim-script PATH      Path to the sim sbatch script
#                          (default: ~/patch-sql-start.sh)
#   --states-file PATH     Path to states.conf
#                          (default: /project/cowy-ptheory/powertwin/shared/states.conf)
#   --consolidate-script PATH
#                          Path to consolidate-state.sh
#                          (default: same dir as this script)
#==============================================================================

#SBATCH --job-name=state-runner
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=7-00:00:00
#SBATCH --account=cowy-ptheory
#SBATCH --output=state-runner_%j.out
#
# NOTE: The cluster's max wall time is 7 days. One state's sim can take the
# full 7 days, so the orchestrator will typically process only a few states
# per invocation before hitting its own wall time. On exit, re-submit with
# --start-from <next_state>. Adoption will pick up any still-running sim.

set -uo pipefail
# NOTE: not using `set -e` — we want per-state failures to be non-fatal

#==============================================================================
# MODULE LOADS
#==============================================================================
module --force purge
module load arcc/1.0
module load slurm

#==============================================================================
# CONFIGURATION
#==============================================================================
POWERTWIN_ROOT="/project/cowy-ptheory/powertwin"
SHARED_DIR="${POWERTWIN_ROOT}/shared"
WORK_DIR="${SHARED_DIR}/state-runner-work"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Defaults (overridable via CLI args)
SIM_SCRIPT="${HOME}/patch-sql-start.sh"
STATES_FILE="${SHARED_DIR}/states.conf"
CONSOLIDATE_SCRIPT="${SCRIPT_DIR}/consolidate-state.sh"
DRY_RUN=0
START_FROM=""
LIMIT=0                     # max states to process per invocation (0 = unlimited)

# Polling intervals
POLL_INTERVAL=60            # seconds between squeue polls
NFS_WAIT=90                 # seconds to wait after job ends for NFS handles
SIM_POLL_TIMEOUT=691200     # 8 days (sim has 7-day limit)
CONSOL_POLL_TIMEOUT=14400   # 4 hours (consolidation sbatch limit)

# NOTE on recovery: the solver framework does NOT support graceful mid-run
# recovery. If a sim hits its 7-day wall time (TIMEOUT), re-submitting would
# start over from scratch, not resume. States that cannot finish in 7 days
# must be split into smaller chunks by the user. The orchestrator treats any
# non-COMPLETED sim as a terminal failure for that state.
#
# However, we CAN safely resume the orchestrator itself across restarts:
#   1. Adoption: if a sim is still in the queue (orchestrator died but sim
#      lived on), we poll it instead of submitting a duplicate
#   2. Skip-if-done: if the output CSV already exists, skip the state

# Resample frequency for consolidation. Leave empty when URBANOPT_REPORTING_FREQUENCY
# in the sim script is already Monthly (or coarser) — no resample needed.
# Set to "M" when running Timestep/Hourly to aggregate to monthly.
RESAMPLE=""

# Minimum free disk space (GB) at POWERTWIN_ROOT. Checked in pre-flight and
# before every state. Orchestrator aborts if free space drops below this.
# A single state's working set can reach ~200GB during sim at Timestep
# frequency; 500GB leaves reasonable margin.
MIN_FREE_GB=500

#==============================================================================
# ARGUMENT PARSING
#==============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=1; shift ;;
        --start-from)
            START_FROM="$2"; shift 2 ;;
        --limit)
            LIMIT="$2"; shift 2 ;;
        --sim-script)
            SIM_SCRIPT="$2"; shift 2 ;;
        --states-file)
            STATES_FILE="$2"; shift 2 ;;
        --consolidate-script)
            CONSOLIDATE_SCRIPT="$2"; shift 2 ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

#==============================================================================
# UTILITY FUNCTIONS
#==============================================================================
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

die() {
    log "FATAL: $*"
    exit 1
}

# Query the final state of a job via sacct. Echoes the state name (or UNKNOWN).
# sacct can lag significantly on a loaded slurmdbd — we retry for up to ~3
# minutes before giving up. If this still returns UNKNOWN, callers should
# fall back to checking the solver's SQLite DB for completion status.
get_final_state() {
    local job_id=$1
    local final_state="" attempt=0
    local max_attempts=36   # 36 × 5s = 180s
    while [ ${attempt} -lt ${max_attempts} ]; do
        final_state=$(sacct -j "${job_id}" -n -o State -X 2>/dev/null | head -1 | tr -d ' ')
        # Filter out transient states that mean "still finishing up"
        case "${final_state}" in
            ""|COMPLETING|RUNNING|PENDING)
                sleep 5
                attempt=$((attempt + 1))
                continue
                ;;
            *)
                break
                ;;
        esac
    done
    echo "${final_state:-UNKNOWN}"
}

# Poll a SLURM job until it leaves the queue, then echo its final state.
#
# Robustness features:
#   - Queue time (PENDING, CONFIGURING, etc.) does NOT count toward the run
#     timeout. Only time in RUNNING state does. A busy cluster can delay the
#     start indefinitely without triggering a false timeout.
#   - Requires 3 consecutive empty squeue results before concluding the job
#     is gone. Protects against transient slurmctld hiccups that would
#     otherwise look like sudden job completion.
#
# Returns 0 if COMPLETED, 1 for other final states, 2 for run-time timeout.
poll_job() {
    local job_id=$1
    local run_timeout=${2:-${SIM_POLL_TIMEOUT}}
    local run_elapsed=0
    local last_state=""
    local current_state
    local consecutive_empty=0
    local empty_confirmations=3

    while true; do
        current_state=$(squeue -j "${job_id}" -h -o %T 2>/dev/null | head -1)

        if [ -z "${current_state}" ]; then
            consecutive_empty=$((consecutive_empty + 1))
            if [ ${consecutive_empty} -ge ${empty_confirmations} ]; then
                break
            fi
            sleep "${POLL_INTERVAL}"
            continue
        fi

        # Reset empty counter on any observed state (handles transient errors)
        consecutive_empty=0

        if [ "${current_state}" != "${last_state}" ]; then
            log "Job ${job_id} state: ${current_state}"
            last_state="${current_state}"
        fi

        if [ "${current_state}" = "RUNNING" ]; then
            run_elapsed=$((run_elapsed + POLL_INTERVAL))
            if [ ${run_elapsed} -ge ${run_timeout} ]; then
                log "ERROR: Job ${job_id} exceeded RUNNING timeout (${run_timeout}s)"
                return 2
            fi
        fi

        sleep "${POLL_INTERVAL}"
    done

    local final_state
    final_state=$(get_final_state "${job_id}")
    log "Job ${job_id} final state: ${final_state}"
    [ "${final_state}" = "COMPLETED" ]
}

# Look for an existing running/pending sim job for a given collection ID.
# Echoes the job ID if found, empty otherwise. Used for resume safety so
# --start-from doesn't create duplicate jobs after an orchestrator crash.
find_existing_sim_job() {
    local cid=$1
    local name="pt-cid${cid}"
    squeue -u "${USER}" --name="${name}" -h -o %i 2>/dev/null | head -1
}

# Mirror of find_existing_sim_job for the consolidation job. Closes the race
# where the orchestrator dies between submitting consolidation and polling it.
find_existing_consol_job() {
    local cid=$1
    local name="pt-consol-cid${cid}"
    squeue -u "${USER}" --name="${name}" -h -o %i 2>/dev/null | head -1
}

# Returns 0 if the sim for this state is fully complete — i.e., all buildings
# are marked Completed in the SQLite master DB AND cleaned_reports exists
# with a plausible number of sensor subdirectories.
#
# Why the count check? SQLite alone is not enough: if a prior run was killed
# mid-cleanup, SQLite could claim "all Completed" while cleaned_reports is
# partially deleted. A plausibility check against the file system guards
# against silent partial-data consolidation.
#
# Threshold: we require at least 80% of the total building count's worth of
# top-level entries in cleaned_reports. This is loose enough to tolerate a
# small number of buildings that don't produce sensor output, strict enough
# to reject a badly-damaged state.
sim_is_fully_complete() {
    local state_root=$1
    local state=$2
    local db="${state_root}/powertwin_data/sqlite/powertwin.db"
    local reports_dir="${state_root}/user_files/${state}/cleaned_reports"

    [ -f "${db}" ] || return 1
    [ -d "${reports_dir}" ] || return 1

    local total completed
    total=$(sqlite3 "${db}" "SELECT COUNT(*) FROM powertwin;" 2>/dev/null || echo 0)
    # NOTE: Python writes status='Finished' on success (see
    # pernode.py::process_single_asset and run_UOsim.py). There is no
    # 'Completed' value anywhere in the Python codebase — grep the repo
    # before changing this literal.
    completed=$(sqlite3 "${db}" "SELECT COUNT(*) FROM powertwin WHERE status='Finished';" 2>/dev/null || echo 0)
    # Defensive: an empty result (lock contention, IO error) would otherwise
    # crash the integer comparison below under set -u.
    [[ "${total}" =~ ^[0-9]+$ ]]     || total=0
    [[ "${completed}" =~ ^[0-9]+$ ]] || completed=0

    if [ "${total}" -eq 0 ]; then
        return 1
    fi
    if [ "${completed}" -ne "${total}" ]; then
        return 1
    fi

    # Count top-level entries in cleaned_reports (one per building/sensor group).
    local report_count
    report_count=$(find "${reports_dir}" -maxdepth 1 -mindepth 1 2>/dev/null | wc -l)
    local threshold=$(( total * 80 / 100 ))
    if [ "${report_count}" -lt "${threshold}" ]; then
        log "sim_is_fully_complete: DB says ${completed}/${total} complete, but cleaned_reports has only ${report_count} entries (threshold ${threshold})"
        return 1
    fi

    return 0
}

# Inject a variable assignment in the working copy of the sim script.
# Matches lines like:  KEY="value"
inject_param() {
    local key=$1 val=$2 file=$3
    if ! grep -q "^${key}=" "${file}"; then
        log "ERROR: ${key}= not found in ${file}"
        return 1
    fi
    sed -i "s|^${key}=\"[^\"]*\"|${key}=\"${val}\"|" "${file}"
}

# Clean up per-state data directories. Falls back to mv on NFS stale handles.
cleanup_state_dir() {
    local state_root=$1
    local target
    for target in "${state_root}/powertwin_data" "${state_root}/user_files"; do
        if [ -d "${target}" ]; then
            if ! rm -rf "${target}" 2>/dev/null; then
                local trash="${target}.trash_$(date +%s)"
                log "WARNING: rm failed for ${target}, moving to ${trash}"
                mv "${target}" "${trash}" 2>/dev/null || log "WARNING: mv also failed for ${target}"
            fi
        fi
    done
}

# Query the master SQLite DB for sim status counts.
check_sim_results() {
    local state_root=$1
    local db="${state_root}/powertwin_data/sqlite/powertwin.db"
    if [ ! -f "${db}" ]; then
        log "WARNING: No master DB found at ${db}"
        return 1
    fi
    log "Status counts for ${db}:"
    sqlite3 "${db}" "SELECT status, COUNT(*) FROM powertwin GROUP BY status;" 2>&1 | \
        while read -r line; do log "  ${line}"; done
    return 0
}

# Append a result line to the summary log.
record_summary() {
    echo "$(date '+%Y-%m-%dT%H:%M:%S') $*" >> "${SUMMARY_LOG}"
}

# Verify at least ${MIN_FREE_GB} GB is free at the given path.
# Returns 0 if ok, 1 if below threshold.
check_disk_space() {
    local path=$1
    local min_gb=${MIN_FREE_GB}
    if [ ! -d "${path}" ]; then
        log "ERROR: disk space check path does not exist: ${path}"
        return 1
    fi
    local available
    # -BG = 1GB blocks (base 2), --output=avail = just the available column
    available=$(df -BG --output=avail "${path}" 2>/dev/null | tail -1 | tr -d 'G ')
    if [ -z "${available}" ] || ! [[ "${available}" =~ ^[0-9]+$ ]]; then
        log "ERROR: could not determine free space at ${path}"
        return 1
    fi
    if [ "${available}" -lt "${min_gb}" ]; then
        log "ERROR: Insufficient disk space at ${path}: ${available}G free, ${min_gb}G required"
        return 1
    fi
    log "Disk space at ${path}: ${available}G free (>= ${min_gb}G required)"
    return 0
}

# Print the final summary block (processed/succeeded/failed).
# Called both at normal completion AND on fail-fast abort.
print_final_summary() {
    log ""
    log "================================================================"
    log "ORCHESTRATOR TERMINATING"
    log "  Processed: ${processed}"
    log "  Succeeded: ${succeeded}"
    log "  Failed:    ${failed}"
    log "Summary log: ${SUMMARY_LOG}"
    log "================================================================"
}

# Hard-stop the orchestrator after a state failure. Records the summary,
# cleans up the failed state's data, prints final counts, and exits 1.
# Policy: any state failure aborts the entire orchestrator run. The user
# can resume via --start-from after fixing the root cause.
fail_fast() {
    local state=$1
    local cid=$2
    local state_root=$3
    local reason=$4
    shift 4
    local extra="$*"

    failed=$((failed + 1))
    record_summary "${state} cid=${cid} RESULT=FAILED reason=${reason} ${extra}"
    log ""
    log "################################################################"
    log "HARD STOP: state ${state} (cid=${cid}) failed — reason=${reason}"
    log "Policy: orchestrator aborts on any state failure."
    log "After fixing the root cause, resume with:"
    log "  sbatch state-runner.sh --start-from ${state}"
    log "################################################################"
    if [ -n "${state_root}" ] && [ -d "${state_root}" ]; then
        log "Cleaning up failed state data..."
        cleanup_state_dir "${state_root}"
    fi
    print_final_summary
    exit 1
}

#==============================================================================
# PRE-FLIGHT CHECKS
#==============================================================================
log "===== State-by-state simulation orchestrator ====="
log "POWERTWIN_ROOT:     ${POWERTWIN_ROOT}"
log "SIM_SCRIPT:         ${SIM_SCRIPT}"
log "STATES_FILE:        ${STATES_FILE}"
log "CONSOLIDATE_SCRIPT: ${CONSOLIDATE_SCRIPT}"
[[ "${LIMIT}" =~ ^[0-9]+$ ]] || die "--limit must be a non-negative integer, got: ${LIMIT}"

log "DRY_RUN:            ${DRY_RUN}"
log "START_FROM:         ${START_FROM:-<none>}"
log "LIMIT:              ${LIMIT} $([ "${LIMIT}" -eq 0 ] && echo '(unlimited)' || echo '(smoke-test / partial run)')"

[ -f "${SIM_SCRIPT}" ]         || die "SIM_SCRIPT not found: ${SIM_SCRIPT}"
[ -f "${STATES_FILE}" ]        || die "STATES_FILE not found: ${STATES_FILE}"
[ -f "${CONSOLIDATE_SCRIPT}" ] || die "CONSOLIDATE_SCRIPT not found: ${CONSOLIDATE_SCRIPT}"

# The consolidation wrapper looks for consolidate_sensor_logs.py at this path
REPO_ROOT_PF="$( cd "$( dirname "${CONSOLIDATE_SCRIPT}" )/.." && pwd )"
CONSOLIDATE_PY="${REPO_ROOT_PF}/solver/app/modules/utils/consolidate_sensor_logs.py"
[ -f "${CONSOLIDATE_PY}" ]     || die "consolidate_sensor_logs.py not found: ${CONSOLIDATE_PY}"

grep -q '^SIMULATION_NAME=' "${SIM_SCRIPT}" || die "SIM_SCRIPT missing SIMULATION_NAME= line"
grep -q '^COLLECTION_ID='   "${SIM_SCRIPT}" || die "SIM_SCRIPT missing COLLECTION_ID= line"

# Verify sqlite3 CLI is available (used by check_sim_results and sim_is_fully_complete)
command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 CLI not found on PATH"

# Verify SLURM commands are available
for cmd in sbatch squeue sacct scancel; do
    command -v "${cmd}" >/dev/null 2>&1 || die "${cmd} not found on PATH (load slurm module?)"
done

# Verify states.conf has at least one non-comment entry
if ! grep -qE '^[^#[:space:]]' "${STATES_FILE}"; then
    die "STATES_FILE is empty or contains only comments: ${STATES_FILE}"
fi

# If --start-from was provided, make sure it actually appears in states.conf
# (otherwise the main loop would silently skip every state).
if [ -n "${START_FROM}" ]; then
    if ! awk -v want="${START_FROM}" '
        { sub(/#.*/, ""); gsub(/^[ \t]+|[ \t]+$/, "") }
        $0 == want { found = 1; exit }
        END { exit !found }
    ' "${STATES_FILE}"; then
        die "--start-from '${START_FROM}' does not appear in ${STATES_FILE}"
    fi
fi

# Verify POWERTWIN_ROOT exists and has sufficient free disk space
[ -d "${POWERTWIN_ROOT}" ] || die "POWERTWIN_ROOT does not exist: ${POWERTWIN_ROOT}"
check_disk_space "${POWERTWIN_ROOT}" || die "Pre-flight disk check failed"

mkdir -p "${WORK_DIR}"
# Prune stale working copies from prior invocations (>30 days old) so WORK_DIR
# doesn't grow unbounded across months of restarts.
find "${WORK_DIR}" -maxdepth 1 -type f -name 'sim-script-*.sh' -mtime +30 -delete 2>/dev/null || true
SIM_SCRIPT_WORK="${WORK_DIR}/sim-script-${SLURM_JOB_ID:-manual}.sh"
cp "${SIM_SCRIPT}" "${SIM_SCRIPT_WORK}"
log "Working copy of sim script: ${SIM_SCRIPT_WORK}"

SUMMARY_LOG="${SHARED_DIR}/state-runner-summary_${SLURM_JOB_ID:-manual}.log"
log "Summary log: ${SUMMARY_LOG}"
: > "${SUMMARY_LOG}"

# On dry-run, walk every state up front and report ALL missing upload dirs
# at once. Without this, the per-state loop would fail_fast on the first
# missing upload, forcing the user to fix one problem at a time.
if [ ${DRY_RUN} -eq 1 ]; then
    log "Dry-run: validating upload dirs for every state..."
    declare -i dry_cid=0 dry_missing=0
    while IFS= read -r dry_line || [ -n "${dry_line}" ]; do
        dry_state=$(echo "${dry_line}" | sed 's/#.*//' | xargs)
        [ -z "${dry_state}" ] && continue
        dry_cid=$((dry_cid + 1))
        dry_state_root="${POWERTWIN_ROOT}/${dry_cid}"
        if [ ! -d "${dry_state_root}/upload" ]; then
            log "  MISSING: ${dry_state_root}/upload (${dry_state})"
            dry_missing=$((dry_missing + 1))
            continue
        fi
        for f in asset_geometries.geojson metadata.csv; do
            if [ ! -f "${dry_state_root}/upload/${f}" ]; then
                log "  WARN:    ${dry_state_root}/upload/${f} missing (${dry_state})"
            fi
        done
    done < "${STATES_FILE}"
    if [ ${dry_missing} -gt 0 ]; then
        die "Dry run: ${dry_missing} state(s) have missing upload directories"
    fi
    log "Dry-run: all state upload dirs present"
fi

#==============================================================================
# MAIN LOOP
#==============================================================================
declare -i collection_id=0
declare -i processed=0 succeeded=0 failed=0
declare -i handled=0      # states this invocation has begun work on (for --limit)
skip_until_start=0
[ -n "${START_FROM}" ] && skip_until_start=1

while IFS= read -r raw_line || [ -n "${raw_line}" ]; do
    # Strip comments and trim
    state=$(echo "${raw_line}" | sed 's/#.*//' | xargs)
    [ -z "${state}" ] && continue

    collection_id=$((collection_id + 1))

    # Handle --start-from
    if [ ${skip_until_start} -eq 1 ]; then
        if [ "${state}" = "${START_FROM}" ]; then
            skip_until_start=0
        else
            log "Skipping ${state} (collection_id=${collection_id}) — before --start-from"
            continue
        fi
    fi

    # Enforce --limit. Counts every state we actually begin work on,
    # including already-done, dry-run, and real submissions. Skipped
    # --start-from states above do NOT count. Break (not exit) so
    # print_final_summary at the bottom still runs.
    handled=$((handled + 1))
    if [ ${LIMIT} -gt 0 ] && [ ${handled} -gt ${LIMIT} ]; then
        log "Reached --limit ${LIMIT}, stopping before ${state} (cid=${collection_id})"
        break
    fi

    state_root="${POWERTWIN_ROOT}/${collection_id}"

    log ""
    log "================================================================"
    log "STATE: ${state}  COLLECTION_ID: ${collection_id}"
    log "STATE_ROOT: ${state_root}"
    log "================================================================"

    # 1a. Idempotent resume: skip state entirely if output CSV already exists
    #     (orchestrator crashed and restarted AFTER this state was done)
    output_csv="${state_root}/${state}_sensor_logs.csv"
    if [ -f "${output_csv}" ]; then
        csv_lines=$(wc -l < "${output_csv}" 2>/dev/null || echo 0)
        if [ "${csv_lines}" -gt 1 ]; then
            log "State ${state} already complete — ${output_csv} has ${csv_lines} lines, skipping"
            record_summary "${state} cid=${collection_id} CSV_LINES=${csv_lines} RESULT=ALREADY_DONE"
            succeeded=$((succeeded + 1))
            continue
        fi
    fi

    # 1b. Verify upload dir. Missing inputs halts the orchestrator under the
    #     fail-fast policy — user must upload the data (or use --start-from
    #     to skip this state) before re-running.
    if [ ! -d "${state_root}/upload" ]; then
        log "ERROR: Upload dir missing: ${state_root}/upload"
        fail_fast "${state}" "${collection_id}" "${state_root}" "no_upload_dir"
    fi
    for f in asset_geometries.geojson metadata.csv; do
        if [ ! -f "${state_root}/upload/${f}" ]; then
            log "WARNING: Missing upload file: ${state_root}/upload/${f}"
        fi
    done

    # 1c. Per-state disk space guard — abort if free space dropped below
    #     the minimum since last check (e.g. other jobs on the cluster
    #     consumed the shared filesystem).
    if ! check_disk_space "${POWERTWIN_ROOT}"; then
        fail_fast "${state}" "${collection_id}" "${state_root}" "insufficient_disk_space"
    fi

    # 2. Inject parameters into working copy of sim script
    if ! inject_param SIMULATION_NAME "${state}"       "${SIM_SCRIPT_WORK}"; then
        fail_fast "${state}" "${collection_id}" "${state_root}" "sed_injection_name"
    fi
    if ! inject_param COLLECTION_ID   "${collection_id}" "${SIM_SCRIPT_WORK}"; then
        fail_fast "${state}" "${collection_id}" "${state_root}" "sed_injection_cid"
    fi

    if [ ${DRY_RUN} -eq 1 ]; then
        log "[DRY RUN] Would sbatch ${SIM_SCRIPT_WORK}"
        log "[DRY RUN] Would run consolidation for ${state_root}"
        record_summary "${state} cid=${collection_id} RESULT=DRY_RUN"
        continue
    fi

    # From here on we're committing to actually submit work for this state.
    processed=$((processed + 1))

    # 3. Decide: adopt running job, skip sim (data complete), or submit fresh
    #
    # Resume modes (in priority order):
    #   a) Running sim job found via job name → adopt, keep polling
    #   b) SQLite shows all buildings Completed + cleaned_reports present →
    #      sim already finished (orchestrator died before consolidation),
    #      skip sim and jump to consolidation
    #   c) Otherwise → clean previous data, submit fresh sim
    sim_job_name="pt-cid${collection_id}"
    existing_job=$(find_existing_sim_job "${collection_id}")
    sim_job_id=""
    sim_already_done=0

    if [ -n "${existing_job}" ]; then
        log "Found existing sim job ${existing_job} for cid=${collection_id}, adopting"
        sim_job_id="${existing_job}"
    elif sim_is_fully_complete "${state_root}" "${state}"; then
        log "Sim already complete for ${state} (all buildings Completed + cleaned_reports present)"
        log "Skipping sim submission, proceeding directly to consolidation"
        sim_already_done=1
        sim_job_id="RESUMED"
    else
        log "Cleaning previous sim data under ${state_root}"
        cleanup_state_dir "${state_root}"

        sim_job_id=$(sbatch --parsable --job-name="${sim_job_name}" "${SIM_SCRIPT_WORK}" 2>&1)
        if ! [[ "${sim_job_id}" =~ ^[0-9]+$ ]]; then
            log "ERROR: Failed to submit sim job: ${sim_job_id}"
            fail_fast "${state}" "${collection_id}" "${state_root}" "sbatch_submit_failed"
        fi
        log "Submitted sim job: ${sim_job_id} (name=${sim_job_name})"
    fi

    # 4. Wait for sim to finish (single attempt — solver cannot resume mid-run)
    if [ ${sim_already_done} -eq 1 ]; then
        sim_status="COMPLETED"
    else
        poll_job "${sim_job_id}" "${SIM_POLL_TIMEOUT}"
        poll_rc=$?
        if [ ${poll_rc} -eq 0 ]; then
            sim_status="COMPLETED"
        elif [ ${poll_rc} -eq 2 ]; then
            # Run-time timer exceeded: job is still running. We MUST scancel
            # before cleanup_state_dir, otherwise we rm -rf under an active
            # sim and corrupt the run. This path is only reachable if
            # SIM_POLL_TIMEOUT < the sim script's own wall limit.
            log "ERROR: Sim ${sim_job_id} exceeded orchestrator run timer — cancelling to prevent data race"
            scancel "${sim_job_id}" 2>&1 | while read -r l; do log "  scancel: ${l}"; done
            sleep 30
            sim_status=$(get_final_state "${sim_job_id}")
            log "Sim ${sim_job_id} post-cancel state: ${sim_status}"
        else
            sim_status=$(get_final_state "${sim_job_id}")
            log "Sim ${sim_job_id} ended in state ${sim_status}"
        fi

        # 5. NFS handle wait (before reading SQLite)
        log "Waiting ${NFS_WAIT}s for NFS handles to release..."
        sleep "${NFS_WAIT}"

        # 6. sacct fallback — if UNKNOWN, consult SQLite authoritative source
        #    sacct can lag on a loaded slurmdbd; don't cleanup valid data
        #    just because the SLURM accounting DB hasn't caught up.
        if [ "${sim_status}" = "UNKNOWN" ]; then
            log "sacct state UNKNOWN — checking SQLite for completion"
            if sim_is_fully_complete "${state_root}" "${state}"; then
                log "SQLite confirms full completion, treating as COMPLETED"
                sim_status="COMPLETED"
            else
                log "SQLite does not confirm completion, treating as FAILED"
                sim_status="UNKNOWN_FAILED"
            fi
        fi

        # 7. Check sim results (log-only)
        check_sim_results "${state_root}" || true
    fi

    if [ "${sim_status}" != "COMPLETED" ]; then
        fail_fast "${state}" "${collection_id}" "${state_root}" "sim_not_completed" "SIM_JOB=${sim_job_id} SIM=${sim_status}"
    fi

    # 8. Submit consolidation
    input_dir="${state_root}/user_files/${state}/cleaned_reports"

    if [ ! -d "${input_dir}" ]; then
        log "ERROR: cleaned_reports dir missing: ${input_dir}"
        fail_fast "${state}" "${collection_id}" "${state_root}" "no_cleaned_reports" "SIM_JOB=${sim_job_id} SIM=COMPLETED"
    fi

    consol_job_name="pt-consol-cid${collection_id}"
    existing_consol=$(find_existing_consol_job "${collection_id}")
    if [ -n "${existing_consol}" ]; then
        log "Found existing consolidation job ${existing_consol} for cid=${collection_id}, adopting"
        consol_job_id="${existing_consol}"
    else
        consol_job_id=$(sbatch --parsable --job-name="${consol_job_name}" \
            --export="ALL,INPUT_DIR=${input_dir},OUTPUT_FILE=${output_csv},COLLECTION_ID=${collection_id},RESAMPLE=${RESAMPLE}" \
            "${CONSOLIDATE_SCRIPT}" 2>&1)
        if ! [[ "${consol_job_id}" =~ ^[0-9]+$ ]]; then
            log "ERROR: Failed to submit consolidation job: ${consol_job_id}"
            fail_fast "${state}" "${collection_id}" "${state_root}" "consol_submit_failed" "SIM_JOB=${sim_job_id} SIM=COMPLETED"
        fi
        log "Submitted consolidation job: ${consol_job_id} (name=${consol_job_name})"
    fi

    # 9. Wait for consolidation
    poll_job "${consol_job_id}" "${CONSOL_POLL_TIMEOUT}"
    consol_rc=$?
    if [ ${consol_rc} -eq 0 ]; then
        consol_status="COMPLETED"
    elif [ ${consol_rc} -eq 2 ]; then
        log "ERROR: Consolidation ${consol_job_id} exceeded run timer — cancelling"
        scancel "${consol_job_id}" 2>&1 | while read -r l; do log "  scancel: ${l}"; done
        sleep 10
        consol_status="TIMEOUT"
    else
        consol_status="FAILED"
    fi

    # 10. Verify output CSV
    csv_lines=0
    if [ -f "${output_csv}" ]; then
        csv_lines=$(wc -l < "${output_csv}" 2>/dev/null || echo 0)
        log "Consolidated CSV: ${output_csv} (${csv_lines} lines)"
    else
        log "WARNING: Output CSV missing: ${output_csv}"
    fi

    if [ "${consol_status}" != "COMPLETED" ] || [ "${csv_lines}" -le 1 ]; then
        # Delete the partial CSV before aborting — otherwise the idempotent
        # resume check at the top of the loop would accept it as "done" on
        # the next orchestrator invocation and silently skip reprocessing.
        if [ -f "${output_csv}" ]; then
            log "Removing partial CSV to prevent false-positive resume: ${output_csv}"
            rm -f "${output_csv}"
        fi
        fail_fast "${state}" "${collection_id}" "${state_root}" "consol_issue" "SIM_JOB=${sim_job_id} SIM=COMPLETED CONSOL_JOB=${consol_job_id} CONSOL=${consol_status} CSV_LINES=${csv_lines}"
    fi

    # 11. Clean sim data (keeps upload/ and consolidated CSV)
    log "Cleaning sim data for ${state}"
    cleanup_state_dir "${state_root}"

    record_summary "${state} cid=${collection_id} SIM_JOB=${sim_job_id} CONSOL_JOB=${consol_job_id} CSV_LINES=${csv_lines} RESULT=SUCCESS"
    succeeded=$((succeeded + 1))
    log "===== Completed ${state} ====="

done < "${STATES_FILE}"

#==============================================================================
# FINAL SUMMARY
#==============================================================================
# Under the fail-fast policy, reaching this point means either:
#   - all states succeeded, or
#   - the orchestrator hit its own wall time (sbatch kills the script)
# Any per-state failure would have exited via fail_fast() already.
print_final_summary
[ ${failed} -eq 0 ] && exit 0 || exit 1
