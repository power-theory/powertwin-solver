import shutil
import os
import re
import json
import datetime
import csv
import glob
import hmac
import threading
import contextlib

from flask import request, jsonify, render_template, send_file

from modules.simulation import initialize_uo, create_featurefiles, stop_UOsimulation
from modules.simulation.stop_UOsim import stopped_early
from modules.simulation.sim_params_spec import (
    validate_metadata, build_asset_ctx, resolve_default,
    is_dynamic_defaults_enabled, RESIDENTIAL_BUILDING_TYPES, get_param, insulation_presets, HPWH,
)
from modules.diagnostics import read_simulation_status, simulation_recovery, create_table
from modules.utils import (
    initialize_logger, send_error_to_mss,
    pack_simulation_results, atomic_write_json, write_status,
)
from modules.utils.hpc_environment import is_hpc_environment, get_hpc_info
from modules.utils.weather import get_climate_zone
from modules.utils.sim_flags import set_sim_flags, clear_sim_flags, get_sim_flag

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Views', external_log_dir)


# Define the output directory for the simulation files
DATA_DIR = os.path.join('data')

# A simulation_name is exactly one path component under DATA_DIR and LOCAL_DIR. Every
# route that takes one joined it unvalidated, and Flask's default converter lets a bare
# '..' through (only a slash is refused), so DELETE /api/simulation/delete/.. removed
# the parent of both roots: the whole user_files volume and /solver/app under the
# running server (proven in a throwaway container 2026-09-20). start/autorun took any
# string, including '../../tmp/x'. Same character class the db listener and the cluster
# scripts already produce (asset_<id>_<ts>, natv3b, qa_matrix_<fx>_<arm>).
# At most 200 characters: the name is also the stem of files written beside it
# (<name>_asset.geojson, <name>_sensor_logs.csv), and a file name caps at 255 bytes.
_SIM_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$')
# Lookups (status, results, delete, asset config) keep the old 255 cap so runs created before the 200 cap stay
# reachable and removable (review 2026-09-29).
_SIM_NAME_LOOKUP_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$')


URBANOPT_MODEL_TYPE_ID = 1   # the db's sensor_logs_pred_model_types id for URBANopt (surrogate_serving keeps the same)


def fast_estimate_module():
    """modules.simulation.surrogate_serving, or None in a build without the fast estimate (the frozen benchmark image
    leaves out the surrogate and its serving module, 2026-10-09): URBANopt requests still run, fast-estimate ones get
    a 503 instead of an ImportError."""
    try:
        from modules.simulation import surrogate_serving
    except ImportError:
        return None
    return surrogate_serving


def _valid_sim_name(name, creating=True) -> bool:
    if not isinstance(name, str) or not (_SIM_NAME_RE if creating else _SIM_NAME_LOOKUP_RE).fullmatch(name):
        return False
    for root in (DATA_DIR, os.path.join('powertwin-solver-pg', 'user_files')):
        real_root = os.path.realpath(root)
        if not os.path.realpath(os.path.join(root, name)).startswith(real_root + os.sep):
            return False
    return True


def _stopped_response(simulation_name):
    """A run halted by POST /api/simulation/stop: its batches returned early, so it must not read
    as a success to the caller that started it (409; the stop marker dies with the sim dir)."""
    logger.info(f"Simulation {simulation_name} stopped by request before completing")
    return jsonify({'error': 'stopped by request (POST /api/simulation/stop)', 'stopped': True,
                    'simulation_name': simulation_name}), 409


def _reject_sim_name(name, creating=True):
    """Response tuple for an invalid simulation_name, else None."""
    if _valid_sim_name(name, creating):
        return None
    logger.error(f"Invalid simulation_name rejected: {name!r}")
    return jsonify({'error': 'Invalid simulation_name'}), 400

# Reporting-frequency translation: user-facing labels ↔ urbanopt natives.
# Loaded once at import; the file lives alongside the other upload/ defaults.
_REPORTING_FREQ_MAP_PATH = os.path.join('upload', 'reporting_frequency_map.json')
_REPORTING_FREQ_MAP = None


def _load_reporting_freq_map():
    global _REPORTING_FREQ_MAP
    if _REPORTING_FREQ_MAP is None:
        try:
            with open(_REPORTING_FREQ_MAP_PATH, 'r') as f:
                _REPORTING_FREQ_MAP = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load {_REPORTING_FREQ_MAP_PATH}: {e}")
            _REPORTING_FREQ_MAP = {'user_to_urbanopt': {}, 'urbanopt_to_user': {}}
    return _REPORTING_FREQ_MAP


def _resolve_reporting_frequency(user_value):
    """Translate a user-facing reporting frequency (minutely/hourly/daily/monthly/yearly)
    to its urbanopt-native equivalent. Returns None when unset (= caller keeps the default).
    Raises ValueError on an unknown user label."""
    if user_value is None:
        return None
    label = str(user_value).strip().lower()
    if not label:
        return None
    fmap = _load_reporting_freq_map().get('user_to_urbanopt', {})
    native = fmap.get(label)
    if not native:
        valid = ', '.join(sorted(fmap.keys()))
        raise ValueError(f"unknown reporting_frequency '{label}' (valid: {valid})")
    return native


def _resolve_datelevel(user_value):
    """Translate a requested datelevel (hour/day/month/year, the sensor_logs_pred
    vocabulary) to the reporting frequency and resample period it runs at, from the
    datelevels table in reporting_frequency_map.json. Returns None when unset
    (= caller keeps the defaults). Raises ValueError on an unknown label."""
    if user_value is None:
        return None
    label = str(user_value).strip().lower()
    if not label:
        return None
    table = _load_reporting_freq_map().get('datelevels', {})
    entry = table.get(label)
    if not entry:
        valid = ', '.join(sorted(table.keys()))
        raise ValueError(f"unknown datelevel '{label}' (valid: {valid})")
    return entry
LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')

# Per-request URBANOPT env overrides (DYNAMIC_DEFAULTS / STOCHASTIC_SAMPLING /
# REPORTING_FREQUENCY) are read by the Python resolver and the uo subprocess at
# call time. The async endpoint returns 202 and runs the sim on a daemon thread,
# so two sims can be in flight on one gunicorn worker and share this process's
# os.environ -- without a guard, sim A's resolver observes sim B's flags, giving
# non-deterministic resolved values across runs. This lock serializes the
# env-sensitive window, enforcing the SIMULATION_CONCURRENCY=1 invariant the
# overrides have always assumed but never enforced.
@contextlib.contextmanager
def _sim_env_override(dynamic_defaults=None, stochastic=None, reporting_frequency=None,
                      keep_dirs=None, resample=None):
    """Scope per-request URBANOPT flags to THIS thread (the async sim runs on its
    own daemon thread). The flags go into thread-local state, read by the Python
    resolver (sim_params_spec) and injected into the uo subprocess env
    (command_timer.run_command). No shared os.environ mutation and no global lock,
    so concurrent sims on one gunicorn worker run in parallel with correctly
    isolated flags. A value of None means 'no override for this var'."""
    set_sim_flags(dynamic_defaults, stochastic, reporting_frequency, keep_dirs, resample)
    for name, val in (('URBANOPT_DYNAMIC_DEFAULTS', dynamic_defaults),
                      ('URBANOPT_STOCHASTIC_SAMPLING', stochastic),
                      ('URBANOPT_REPORTING_FREQUENCY', reporting_frequency),
                      ('URBANOPT_RESAMPLE', resample)):
        if val is not None:
            logger.info(f"{name} override: {val}")
    try:
        yield
    finally:
        clear_sim_flags()

def home():
    return render_template('base.html')

# 1. Simulation Managment

############################################################################################################
# Name: def start_simulation()
# Description: This function requires ASSET_GEOJSON, METADATA_CSV, and simulation_name,
# and num_cores to start the simulation. Performs error checking and creates a directory 
# based on the given simulation name. Calls the create_featurefiles and initialize_uo functions to
# generate feature files and start the UrbanOpt simulation. This function parallelizes the 
# simulation after the feature files are created.
############################################################################################################

def _cleanup_recovery_dirs(*dirs):
    """Remove partially-created recovery dirs so a retry is not 400'd (Q/A D-44)."""
    for d in dirs:
        try:
            if os.path.exists(d):
                shutil.rmtree(d)
        except Exception as e:
            logger.warning(f"could not clean recovery dir {d}: {e}")


def _is_deleted_tombstone(path):
    """True when `path` holds only the terminal 'deleted' status tombstone (Q/A D-43).

    delete_simulation deliberately leaves a status.json reading 'deleted' for late polls, but the
    write lands after the rmtree and recreates the directory -- which then made the
    name-already-exists guard reject re-use of that simulation name forever. A tombstone-only
    directory is free for re-use (the caller removes it)."""
    if not os.path.isdir(path):
        return False
    entries = os.listdir(path)
    if len(entries) != 1 or entries[0] != 'status.json':
        return False
    try:
        with open(os.path.join(path, 'status.json')) as fh:
            return json.load(fh).get('phase') == 'deleted'
    except Exception:
        return False

def start_simulation():
    logger.debug("Within start_simulation()")

    # Inputs
    ASSET_GEOJSON = request.files.get('asset_geojson_file')
    METADATA_CSV = request.files.get('metadata_csv_file')
    simulation_name = request.form.get('simulation_name')
    num_cores = int(request.form.get('num_cores', 1))
    shared_storage = request.form.get('shared_storage')
    keep_dirs = request.form.get('keep_dirs', 'false').lower() == 'true'
    # Optional per-request URBANOPT_DYNAMIC_DEFAULTS override.
    # 'true'/'false' (case-insensitive); anything else (incl. missing) leaves
    # the server-wide env untouched. Mirrors the request_reporting_frequency
    # snapshot/restore pattern in _run_asset_update_simulation().
    request_dynamic_defaults_raw = request.form.get('dynamic_defaults')
    request_dynamic_defaults = None
    if request_dynamic_defaults_raw is not None:
        lowered = str(request_dynamic_defaults_raw).strip().lower()
        if lowered in ('true', 'false'):
            request_dynamic_defaults = lowered
    request_stochastic_raw = request.form.get('stochastic_sampling')
    request_stochastic = None
    if request_stochastic_raw is not None:
        lowered = str(request_stochastic_raw).strip().lower()
        if lowered in ('true', 'false'):
            request_stochastic = lowered

    # Use centralized HPC detection
    is_hpc = is_hpc_environment()

    # POWERTWIN_KEEP_DIRS is applied via _sim_env_override below (thread-local),
    # not mutated on the global env here, so it can't leak into a concurrent sim.

    # Reference the volume directory where the local files will be stored
    # TODO: Set as global variable for consistency across all LOCAL_DIR references 
    LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    
    # Error checking
    #TODO: Metadata csv should be optional since its ideally  only required for report cleaning
    if not ASSET_GEOJSON or not METADATA_CSV or not simulation_name or num_cores <= 0:
        logger.error("Error: missing or invalid parameter.")
        return jsonify({'error': 'missing or invalid parameter'}), 400
    bad_name = _reject_sim_name(simulation_name)
    if bad_name is not None:
        return bad_name
    
    # HPC mode validation
    if is_hpc and not shared_storage:
        logger.error("Error: shared_storage is required when in HPC environment.")
        return jsonify({'error': 'shared_storage is required when in HPC environment'}), 400
    
    # Define and create Simulation directory (container) and Local directory (saved on host)
    # Local directory stores all necessary files for recovery and completed asset files
    # Simulation directory contains all files necessary for the simulation
    SIMULATION_DIR = os.path.join(DATA_DIR, f'{simulation_name}')
    LOCAL_DIR = os.path.join(LOCAL_DIR, f'{simulation_name}')
    for _d in (SIMULATION_DIR, LOCAL_DIR):
        if _is_deleted_tombstone(_d):
            shutil.rmtree(_d)  # freed name (Q/A D-43)
    if os.path.exists(SIMULATION_DIR) or os.path.exists(LOCAL_DIR):
        logger.error("Error: Simulation name already exists.")
        return jsonify({'error': 'Simulation name already exists.'}), 400
    
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    os.makedirs(LOCAL_DIR, exist_ok=True)
    logger.info(f"Upload directory: {SIMULATION_DIR}")
    logger.info(f"Local directory: {LOCAL_DIR}")
    
    # Define and save the paths for the uploaded files in Local directory
    asset_geojson_path = os.path.join(LOCAL_DIR, f'{simulation_name}_asset.geojson')
    metadata_csv_path = os.path.join(LOCAL_DIR, f'{simulation_name}_metadata.csv')

    ASSET_GEOJSON.save(asset_geojson_path)
    METADATA_CSV.save(metadata_csv_path)

    # Per-request URBANOPT env overrides. _sim_env_override scopes these to this
    # thread (not the shared os.environ), so a concurrent sim on the same worker
    # can't observe this request's flags mid-run.
    with _sim_env_override(request_dynamic_defaults, request_stochastic,
                           keep_dirs=keep_dirs):
        # Call the create_feature_files and initialize_uo to run the simulation
        # and delete the simulation directory (container)
        try:
            create_table()
            logger.debug("Calling create_feature_files from start_simulation()")
            create_featurefiles(SIMULATION_DIR, LOCAL_DIR, asset_geojson_path, metadata_csv_path, num_cores, simulation_name)
            logger.debug("Exited create_feature_files to start_simulation()")

            logger.debug("Calling initialize_uo from start_simulation()")
            initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name)
            logger.debug("Exited initialize_uo to start_simulation()")

            stopped = stopped_early(SIMULATION_DIR)
            logger.debug("Deleting simulation directory, within the container")
            get_logs()
            shutil.rmtree(SIMULATION_DIR)
        except Exception as e:
            logger.error(f"Exception: {str(e)}")
            send_error_to_mss('start_simulation', str(e))
            return jsonify({'error': str(e)}), 500

    if stopped:
        return _stopped_response(simulation_name)
    logger.debug("start_simulation() ran successfully")
    return jsonify({'confirmation': f'Simulation "{simulation_name}" ran successfully'})

############################################################################################################
# Name: def _check_api_token()
# Description: Reject requests without the shared service token (API_SOLVER_TOKEN).
# Clients send it as the 'api-token' header (the db listener does). Werkzeug 3.1 drops header
# names containing an underscore before the app sees them, so a literal 'api_token' header
# is a silent 403; the lookup below is spelled with the underscore only because Werkzeug
# normalizes both spellings to the same key on the server side.
# Fails closed: if the env var is unset on this Flask process, every request is rejected with 500
# (operator misconfig — surfaces in monitoring rather than silently authorizing all traffic).
# Returns a Flask response tuple to short-circuit the handler, or None when authorized.
############################################################################################################
def _check_api_token():
    expected = os.environ.get('API_SOLVER_TOKEN')
    if not expected:
        logger.error("API_SOLVER_TOKEN is not set; refusing request")
        return jsonify({'error': 'Server misconfigured: API_SOLVER_TOKEN not set'}), 500
    presented = request.headers.get('api_token') or ''
    if not hmac.compare_digest(presented, expected):
        return jsonify({'error': 'Unauthorized'}), 403
    return None


############################################################################################################
# Name: def process_asset_update()
# Description: Async kickoff. Validates input, writes input files + an initial
# status.json('received'), then spawns a daemon thread that runs urbanopt and
# writes status.json after each phase plus an atomic results.json on completion.
# Returns 202 Accepted with {simulation_name} immediately so the caller (the
# powertwin-db API listener) can release its connection slot. The API polls
# /api/simulation/status/<name> and pulls /api/simulation/results/<name> when
# the worker thread reports completed.
############################################################################################################
def process_asset_update():
    logger.debug("Within process_asset_update()")

    auth_err = _check_api_token()
    if auth_err is not None:
        return auth_err

    started_at = datetime.datetime.now(datetime.timezone.utc)
    try:
        request_data = request.get_json()
        if not request_data:
            return jsonify({'error': 'No JSON payload received'}), 400
        metadata_json = request_data.get('data', [])
        geojson_array = request_data.get('geojson', [])
        if not metadata_json or not geojson_array:
            return jsonify({'error': 'Missing data or geojson in payload'}), 400

        # Optional per-request window for post-processing slicing. The simulation
        # itself still runs the full URBANOPT_SIMULATION_YEAR range; these only narrow what
        # gets returned/ingested. Either bound is independent.
        request_start_date_time = (request_data.get('start_date_time') or '').strip() or None
        request_end_date_time = (request_data.get('end_date_time') or '').strip() or None

        # Optional per-request reporting frequency. Accepts user-facing labels:
        # minutely | hourly | daily | monthly | yearly. Translated to urbanopt
        # natives via upload/reporting_frequency_map.json. Unset → keep the
        # server-wide URBANOPT_REPORTING_FREQUENCY default.
        try:
            request_reporting_frequency = _resolve_reporting_frequency(
                request_data.get('reporting_frequency')
            )
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400

        # Optional per-request datelevel: hour | day | month | year. One label sets
        # both the reporting frequency and the post-process resample from the
        # datelevels table, so the server-wide URBANOPT_RESAMPLE no longer decides
        # what a request gets back. Unset keeps both defaults.
        request_resample = None
        try:
            datelevel_entry = _resolve_datelevel(request_data.get('datelevel'))
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400
        if datelevel_entry:
            request_reporting_frequency = datelevel_entry['reporting_frequency']
            request_resample = datelevel_entry['resample']

        # Optional per-request URBANOPT_DYNAMIC_DEFAULTS override on the async
        # path. Mirrors the start_simulation parsing: 'true' / 'false'
        # (case-insensitive); anything else (including missing) leaves the
        # server-wide env untouched. The override is applied + restored
        # inside _run_asset_update_simulation so it doesn't leak.
        request_dynamic_defaults_raw = request_data.get('dynamic_defaults')
        request_dynamic_defaults = None
        if request_dynamic_defaults_raw is not None:
            lowered = str(request_dynamic_defaults_raw).strip().lower()
            if lowered in ('true', 'false'):
                request_dynamic_defaults = lowered
        request_stochastic_raw = request_data.get('stochastic_sampling')
        request_stochastic = None
        if request_stochastic_raw is not None:
            lowered = str(request_stochastic_raw).strip().lower()
            if lowered in ('true', 'false'):
                request_stochastic = lowered

        asset_id = str(metadata_json[0].get('asset_id', 'unknown'))
        timestamp = started_at.strftime('%Y%m%d_%H%M%S_%f')
        simulation_name = f"asset_{asset_id}_{timestamp}"
        # asset_id comes from the JSON body and is joined into two roots below.
        bad_name = _reject_sim_name(simulation_name)
        if bad_name is not None:
            return bad_name

        # Validate programmable sim params (enum membership) before doing any
        # filesystem work. metadata_json[0].asset_metadata is the JSONB blob the
        # asset carries; reject early on invalid system_type / fuel / etc. so
        # EnergyPlus doesn't crash 90s later with a cryptic Ruby BOOST_ASSERT.
        am = metadata_json[0].get('asset_metadata') or {}
        if isinstance(am, str):
            try:
                am = json.loads(am)
            except (ValueError, TypeError):
                am = {}
        ok, errs = validate_metadata(am)
        if not ok:
            return jsonify({'error': 'invalid simulation params', 'details': errs}), 400

        # Optional prediction model (vault Surrogate Serving Plan, 2026-10-03): the db's
        # sensor_logs_pred_model_types id. Absent or 1 is URBANopt, as before; the fast-estimate id runs the
        # surrogate on the same metadata and geometry. Anything else is refused, and a fast estimate this solver
        # cannot serve (no model, or one trained for another schema / resolver / year) is a 503, never URBANopt.
        SS = fast_estimate_module()
        raw_model = request_data.get('model_type_id')
        try:
            model_type_id = URBANOPT_MODEL_TYPE_ID if raw_model in (None, '') else int(raw_model)
        except (TypeError, ValueError):
            return jsonify({'error': f'invalid model_type_id {raw_model!r}'}), 400
        surrogate_meta = None
        if SS is None and model_type_id != URBANOPT_MODEL_TYPE_ID:
            return jsonify({'error': 'fast estimate unavailable',
                            'details': 'this build carries no fast estimate (URBANopt only)'}), 503
        surrogate_id = SS.surrogate_model_type_id() if SS is not None else None
        if surrogate_id is not None and model_type_id == surrogate_id:
            # the fast estimate is hourly: a coarser reporting_frequency without a datelevel becomes the matching
            # resample (URBANopt would report at that frequency natively); a finer one cannot be served
            if request_resample is None and request_reporting_frequency is not None:
                coarser = {'Hourly': None, 'Timestep': None, 'Daily': 'D', 'Monthly': 'M', 'Runperiod': 'Y'}
                if request_reporting_frequency not in coarser:
                    return jsonify({'error': f'the fast estimate is hourly; reporting_frequency '
                                             f'{request_reporting_frequency} is finer'}), 400
                request_resample = coarser[request_reporting_frequency]
            try:
                surrogate_meta = SS.precheck()
            except SS.SurrogateUnavailable as e:
                return jsonify({'error': 'fast estimate unavailable', 'details': str(e)}), 503
            # an explicit param the model does not read, or a value beyond its training: a clear refusal now, not a
            # failed background run (2026-10-13); the caller can resubmit for URBANopt
            refused = SS.refusal(metadata_json)
            if refused:
                return jsonify({'error': 'fast estimate cannot serve this building', 'route': 'urbanopt',
                                'details': refused}), 409
        elif model_type_id != URBANOPT_MODEL_TYPE_ID:
            return jsonify({'error': f'unknown model_type_id {model_type_id}'}), 400

        logger.info(f"Accepted async simulation {simulation_name} for asset {asset_id} (model {model_type_id})")

        csv_content = convert_metadata_to_csv(metadata_json)
        geojson_content = convert_geometry_to_geojson(geojson_array)

        LOCAL_BASE = os.path.join('powertwin-solver-pg', 'user_files')
        SIMULATION_DIR = os.path.join(DATA_DIR, simulation_name)
        sim_local_dir = os.path.join(LOCAL_BASE, simulation_name)
        for _d in (SIMULATION_DIR, sim_local_dir):
            if _is_deleted_tombstone(_d):
                shutil.rmtree(_d)  # freed name (Q/A D-43; code-review: this guard missed the helper)
        if os.path.exists(SIMULATION_DIR) or os.path.exists(sim_local_dir):
            return jsonify({'error': 'Simulation name already exists.'}), 400

        os.makedirs(SIMULATION_DIR, exist_ok=True)
        os.makedirs(sim_local_dir, exist_ok=True)
        write_status(sim_local_dir, 'received',
                     simulation_name=simulation_name, asset_id=asset_id)

        asset_geojson_path = os.path.join(sim_local_dir, f'{simulation_name}_asset.geojson')
        metadata_csv_path = os.path.join(sim_local_dir, f'{simulation_name}_metadata.csv')
        with open(asset_geojson_path, 'w') as f:
            json.dump(geojson_content, f)
        with open(metadata_csv_path, 'w', newline='', encoding='utf-8') as f:
            f.write(csv_content)

        if surrogate_meta is not None:
            thread = threading.Thread(
                target=_run_asset_update_surrogate,
                args=(simulation_name, SIMULATION_DIR, sim_local_dir, asset_geojson_path, metadata_csv_path,
                      started_at, request_start_date_time, request_end_date_time, request_resample, model_type_id),
                daemon=True,
            )
        else:
            thread = threading.Thread(
                target=_run_asset_update_simulation,
                args=(simulation_name, SIMULATION_DIR, sim_local_dir,
                      asset_geojson_path, metadata_csv_path,
                      started_at, request_start_date_time, request_end_date_time,
                      request_reporting_frequency, request_dynamic_defaults,
                      request_stochastic, request_resample),
                daemon=True,
            )
        thread.start()
        return jsonify({
            'success': True,
            'simulation_name': simulation_name,
            'status': 'accepted',
            'model_type_id': model_type_id,
        }), 202

    except Exception as e:
        logger.error(f"Exception in process_asset_update: {str(e)}")
        send_error_to_mss('process_asset_update', str(e))
        return jsonify({'error': str(e)}), 500


def _run_asset_update_simulation(simulation_name, simulation_dir, sim_local_dir,
                                 asset_geojson_path, metadata_csv_path,
                                 started_at, request_start_date_time=None,
                                 request_end_date_time=None,
                                 request_reporting_frequency=None,
                                 request_dynamic_defaults=None,
                                 request_stochastic=None,
                                 request_resample=None):
    """Background worker. Runs without a Flask request context -- do not reference `request`."""
    # Per-request URBANOPT env overrides (reporting_frequency / dynamic_defaults
    # / stochastic_sampling). The resolver and the uo subprocess read these at
    # call time; _sim_env_override scopes them to this thread (not the shared
    # os.environ), so concurrent async sims on the same worker can't observe
    # each other's flags.
    with _sim_env_override(request_dynamic_defaults, request_stochastic,
                           request_reporting_frequency, resample=request_resample):
        try:
            num_cores = 1
            create_table()
            write_status(sim_local_dir, 'feature_files_starting', simulation_name=simulation_name)
            create_featurefiles(simulation_dir, sim_local_dir, asset_geojson_path,
                                metadata_csv_path, num_cores, simulation_name)
            write_status(sim_local_dir, 'urbanopt_running', simulation_name=simulation_name)
            initialize_uo(simulation_dir, sim_local_dir, simulation_name)
            write_status(sim_local_dir, 'cleaning_reports', simulation_name=simulation_name)

            if stopped_early(simulation_dir):
                write_status(sim_local_dir, 'stopped', simulation_name=simulation_name,
                             error='stopped by request (POST /api/simulation/stop)')
                logger.info(f"Async simulation stopped by request: {simulation_name}")
                return

            # prepare_record swallows per-asset failures (EnergyPlus fatal, uo process
            # crash, etc.), so an empty cleaned_reports/ is the only signal that the
            # sim didn't actually produce output. Raise so the except below writes
            # status='failed' instead of shipping a zero-row 'completed'.
            if not glob.glob(os.path.join(sim_local_dir, 'cleaned_reports', '*', 'cleaned_predicted_*.csv')):
                raise RuntimeError(
                    f"Simulation {simulation_name} produced no cleaned_reports output — "
                    "EnergyPlus/urbanopt failed (see earlier Flask logs)"
                )

            runtime_seconds = (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds()
            # URBANOPT_RESAMPLE: 'H' | 'D' | 'W' | 'M' | 'Y' | '' (native passthrough).
            # Same convention as the HPC consolidate-state.sh RESAMPLE arg; the request's
            # datelevel overrides the server-wide value through the thread-local flag.
            resample = get_sim_flag('URBANOPT_RESAMPLE')
            if resample is None:
                resample = os.environ.get('URBANOPT_RESAMPLE', '')
            resample = resample.strip() or None
            results_payload = pack_simulation_results(
                sim_local_dir,
                runtime_seconds=runtime_seconds,
                resample=resample,
                start_date_time=request_start_date_time,
                end_date_time=request_end_date_time,
            )
            response = {
                'success': True,
                'simulation_name': simulation_name,
                'results': results_payload['results'],
                'runtime_seconds': runtime_seconds,
                'datelevel': results_payload['datelevel'],
                'resample': results_payload.get('resample'),
            }
            atomic_write_json(os.path.join(sim_local_dir, 'results.json'), response)
            write_status(sim_local_dir, 'completed',
                         simulation_name=simulation_name,
                         completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         runtime_seconds=runtime_seconds,
                         datelevel=results_payload['datelevel'])
            logger.info(f"Async simulation completed: {simulation_name}")
        except Exception as e:
            logger.error(f"Async simulation failed for {simulation_name}: {str(e)}")
            try:
                write_status(sim_local_dir, 'failed',
                             simulation_name=simulation_name, error=str(e))
            except Exception:
                pass
            try:
                send_error_to_mss('process_asset_update_async', str(e))
            except Exception:
                pass
        finally:
            # Always remove the per-sim working dir, success or failure. urbanopt
            # writes hundreds of MB of intermediate state per run (IDF, eplusout,
            # measure outputs, weather caches). Leaving these on disk after a
            # failed sim used to be for debugging, but the API listener already
            # captures last_error and any structured failure context, and the
            # disk pressure under high-concurrency sweeps quickly becomes the
            # bigger problem.
            # URBANOPT_KEEP_RUN_DIR (bool, default false) disables this cleanup
            # so test verifiers can inspect in.osw / out.osw / in.idf after a sim.
            keep = os.environ.get('URBANOPT_KEEP_RUN_DIR', '').strip().lower() == 'true'
            if os.path.exists(simulation_dir) and not keep:
                shutil.rmtree(simulation_dir)

def _run_asset_update_surrogate(simulation_name, simulation_dir, sim_local_dir, asset_geojson_path,
                                metadata_csv_path, started_at, request_start_date_time=None,
                                request_end_date_time=None, request_resample=None, model_type_id=None):
    """Background worker for the fast estimate: same phases, cleaned reports, results.json shape and datelevel
    handling as _run_asset_update_simulation, so the db's ingest needs no change. Runs without a request context.
    The resolver runs in the model's training mode (dynamic defaults, stochastic sampling), whatever the request
    asked for: the estimate is only valid at the settings the model learned."""
    from modules.simulation import surrogate_serving as SS
    try:
        write_status(sim_local_dir, 'surrogate_loading', simulation_name=simulation_name)
        server = SS.get_server()
        env = server.env()
        with _sim_env_override(env['URBANOPT_DYNAMIC_DEFAULTS'], env['URBANOPT_STOCHASTIC_SAMPLING'],
                               resample=request_resample):
            write_status(sim_local_dir, 'surrogate_running', simulation_name=simulation_name)
            run = SS.run(sim_local_dir, metadata_csv_path, asset_geojson_path, server)
            runtime_seconds = (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds()
            resample = get_sim_flag('URBANOPT_RESAMPLE')
            if resample is None:
                resample = os.environ.get('URBANOPT_RESAMPLE', '')
            resample = resample.strip() or None
            results_payload = pack_simulation_results(
                sim_local_dir, runtime_seconds=runtime_seconds, resample=resample,
                start_date_time=request_start_date_time, end_date_time=request_end_date_time)
        response = {
            'success': True,
            'simulation_name': simulation_name,
            'results': results_payload['results'],
            'runtime_seconds': runtime_seconds,
            'datelevel': results_payload['datelevel'],
            'resample': results_payload.get('resample'),
            'model_type_id': model_type_id,
            'estimate_kind': 'baseline_surrogate',
            'model_name': server.meta.get('model_name'),
            'accuracy': server.meta.get('accuracy'),
            'skipped': run['skipped'],
        }
        atomic_write_json(os.path.join(sim_local_dir, 'results.json'), response)
        write_status(sim_local_dir, 'completed', simulation_name=simulation_name,
                     completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     runtime_seconds=runtime_seconds, datelevel=results_payload['datelevel'],
                     model_type_id=model_type_id)
        logger.info(f"Fast estimate completed: {simulation_name} ({run['buildings']} buildings, "
                    f"{runtime_seconds:.1f} s)")
    except Exception as e:
        logger.error(f"Fast estimate failed for {simulation_name}: {e}")
        try:
            write_status(sim_local_dir, 'failed', simulation_name=simulation_name, error=str(e),
                         model_type_id=model_type_id)
        except Exception:
            pass
        try:
            send_error_to_mss('process_asset_update_surrogate', str(e))
        except Exception:
            pass
    finally:
        if os.path.exists(simulation_dir):
            shutil.rmtree(simulation_dir, ignore_errors=True)


############################################################################################################
# Name: def convert_metadata_to_csv(metadata_json)
# Description: Converts JSON metadata array to CSV format matching expected structure
############################################################################################################
def convert_metadata_to_csv(metadata_json):
    """Convert the per-sensor JSON rows the API sends into the metadata CSV the pipeline reads:
    one row per building with a `sensors` {sensor_type_id: sensor_id} column (flow's shape
    since 2026-09-17; the asset's metadata used to be repeated on every sensor row). A building
    is an (asset, geometry) pair: the listener joins every footprint of the asset with every
    sensor, and generateFeatureFile keys on asset_geometries_properties.id, so an asset with
    several footprints keeps one row per footprint with the full sensors map on each (grouping
    by asset alone simulated one footprint of a campus asset and reported the asset as done)."""
    if not metadata_json:
        return ""
    headers = ['asset_id', 'asset_name', 'asset_subtype_id', 'sensors', 'asset_metadata', 'asset_geometries_properties']
    sensors_by_asset = {}
    by_building = {}
    for row in metadata_json:
        aid = str(row.get('asset_id', ''))
        props = row.get('asset_geometries_properties') or {}
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except ValueError:
                props = {}
        gid = str(props.get('id', '') if isinstance(props, dict) else '')
        by_building.setdefault((aid, gid), row)
        st, sid = row.get('sensor_type_id'), row.get('sensor_id')
        if st not in (None, '') and sid not in (None, ''):
            sensors_by_asset.setdefault(aid, {})[str(st)] = sid

    def q(v):
        return '"' + str(v).replace('"', '""') + '"'

    def js(v):
        return q(v if isinstance(v, str) else json.dumps(v))

    csv_lines = [','.join(headers)]
    for (aid, _gid), row in by_building.items():
        csv_lines.append(','.join([
            aid, q(row.get('asset_name', '')), str(row.get('asset_subtype_id', '')),
            js(sensors_by_asset.get(aid, {})), js(row.get('asset_metadata', {})), js(row.get('asset_geometries_properties', {})),
        ]))
    return '\n'.join(csv_lines)
############################################################################################################
# Name: def convert_geometry_to_geojson(geojson_array)
# Description: Converts geometry array to proper GeoJSON FeatureCollection
############################################################################################################
def convert_geometry_to_geojson(geojson_array):
    """Convert geometry array to GeoJSON FeatureCollection."""
    features = []
    
    for geom_item in geojson_array:
        try:
            # Parse geometry if it's a string
            if isinstance(geom_item.get('geometry'), str):
                geometry = json.loads(geom_item['geometry'])
            else:
                geometry = geom_item.get('geometry', {})
            
            # Get properties
            properties = geom_item.get('properties', {})
            
            # Ensure asset_id is in properties if available
            if 'asset_id' in geom_item and 'asset_id' not in properties:
                properties['asset_id'] = geom_item['asset_id']
            # keep id/asset_id string-typed on the intermediate geojson: they are identifiers, not
            # numbers, and are matched as strings downstream -- avoids any numeric-JSON reformatting
            if properties.get('asset_id') is not None:
                properties['asset_id'] = str(properties['asset_id'])
            if properties.get('id') is not None:
                properties['id'] = str(properties['id'])

            # Create feature
            feature = {
                'type': 'Feature',
                'geometry': geometry,
                'properties': properties
            }
            
            features.append(feature)
            
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Skipping invalid geometry item: {e}")
            continue
    
    return {
        'type': 'FeatureCollection',
        'features': features
    }

############################################################################################################
# Name: def autorun_simulation()
# Description: This function reads the simulation.json file and starts the simulation based on the given parameters.
# Calls the create_featurefiles and initialize_uo functions to generate feature files and start the UrbanOpt simulation.
# This function parallelizes the simulation after the feature files are created.
############################################################################################################
def autorun_simulation():
    logger.debug("Within autorun_simulation()")
    
    # Reference the volume directory where the local files will be stored
    # TODO: Set as global variable for consistency across all LOCAL_DIR references 
    LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')


    SIMULATION_JSON = os.path.join('upload', 'simulation.json')
    
    # Error checking
    if not os.path.exists(SIMULATION_JSON):
        logger.error("Error: simulation.json file not found.")
        return jsonify({'error': 'simulation.json file not found.'}), 404
    
    
    # Get the parameters from the simulation.json file
    with open(SIMULATION_JSON, 'r') as file:
        data = json.load(file)
    
    simulation_name = data.get('simulation_name')
    asset_geojson_path = data.get('asset_geojson_path')
    metadata_csv_path = data.get('metadata_csv_path')
    num_cores = data.get('num_cores', 1)

    # Error checking
    #TODO: Metadata csv could be merged into feature properties (no need for two seperate files)
    if not simulation_name or not asset_geojson_path or not metadata_csv_path or num_cores <= 0:
        logger.error("Error: Missing required fields in simulation.json")
        return jsonify({'error': 'Missing required fields in simulation.json'}), 400
    bad_name = _reject_sim_name(simulation_name)
    if bad_name is not None:
        return bad_name

    # Define and create Simulation directory (container) and Local directory (saved on host)
    # Local directory stores all necessary files for recovery and completed asset files
    # Simulation directory contains all files necessary for the simulation
    SIMULATION_DIR = os.path.join(DATA_DIR, f'{simulation_name}')
    LOCAL_DIR = os.path.join(LOCAL_DIR, f'{simulation_name}')
    for _d in (SIMULATION_DIR, LOCAL_DIR):
        if _is_deleted_tombstone(_d):
            shutil.rmtree(_d)  # freed name (Q/A D-43)
    if os.path.exists(SIMULATION_DIR) or os.path.exists(LOCAL_DIR):
        logger.error("Error: Simulation name already exists.")
        return jsonify({'error': 'Simulation name already exists.'}), 400
    
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    os.makedirs(LOCAL_DIR, exist_ok=True)
    logger.info(f"Upload directory: {SIMULATION_DIR}")
    

    # Copy the files to the Local directory
    ASSET_GEOJSON = os.path.join(LOCAL_DIR, f'{simulation_name}_asset.geojson')
    METADATA_CSV = os.path.join(LOCAL_DIR, f'{simulation_name}_metadata.csv')

    shutil.copy(asset_geojson_path, ASSET_GEOJSON)
    shutil.copy(metadata_csv_path, METADATA_CSV)

    # Call the create_feature_files and initialize_uo to run the simulation and 
    # delete the simulation directory (container)
    try:
        create_table()
        
        # Extract HPC-related parameters from the simulation.json
        shared_storage = data.get('shared_storage', None)
        
        # Use centralized HPC detection
        is_hpc = is_hpc_environment()
        
        logger.debug(f"HPC Environment: {is_hpc}, Shared Storage: {shared_storage}")
        
        # autorun sets no per-request override, so the resolver + uo subprocess
        # read the global URBANOPT_* env at call time. _sim_env_override applies
        # empty per-thread flags, so this run is unaffected by a concurrent sim's
        # flags on the same worker.
        with _sim_env_override():
            logger.debug("Calling create_feature_files from autorun_simulation()")
            create_featurefiles(SIMULATION_DIR, LOCAL_DIR, ASSET_GEOJSON, METADATA_CSV, num_cores, simulation_name)
            logger.debug("Exited create_feature_files")

            logger.debug("Calling initialize_uo from autorun_simulation()")
            initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name)
            logger.debug("Exited initialize_uo")
        
        stopped = stopped_early(SIMULATION_DIR)
        logger.debug("Deleting simulation directory, within the container")
        get_logs()
        shutil.rmtree(SIMULATION_DIR)
        if stopped:
            return _stopped_response(simulation_name)
        return jsonify({'message': 'Simulation completed successfully.'}), 200
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        send_error_to_mss('autorun_simulation', str(e))
        return jsonify({'error': f"Simulation failed: {str(e)}"}), 500

############################################################################################################
# Name: def stop_simulation()
# Description: This function stops the UrbanOpt simulation.
# Calls the stop_UOsimulation function to aggressively stop the simulation.
# WARNING: SIGKILLs every urbanopt/openstudio/energyplus process in the container, so every
# in-flight simulation here dies, not one. Token-gated: before 2026-09-20 an unauthenticated
# POST also killed the Werkzeug worker (the old filter spared 'app.py', the server is run.py)
# and the container exited.
############################################################################################################
def stop_simulation():
    logger.debug("Within stop_simulation()")
    auth_err = _check_api_token()
    if auth_err is not None:
        return auth_err
    # Stop the simulation
    try:
        logger.debug("Stopping the simulation")
        outcome = stop_UOsimulation()

        return jsonify({'message': 'Simulation stopped successfully', **outcome}), 200
    except Exception as e:
        logger.error(f"Exception while stopping the simulation: {str(e)}")
        send_error_to_mss('stop_simulation', str(e))
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def get_simulation_status()
# Description: This function reads the simulation status files and logs the status of the simulation.
# Calls the read_simulation_status function to read the simulation status files.
############################################################################################################ 
def get_simulation_status(simulation_name):
    logger.debug("Within simulation_status()")
    
    
    # Parameter checking
    batch_id = request.args.get('batch_id', default=None, type=int)

    if simulation_name is None:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400
    bad_name = _reject_sim_name(simulation_name, creating=False)
    if bad_name is not None:
        return bad_name
    
    # Prefer the status.json the async worker writes after each phase.
    sim_local_dir = os.path.join('powertwin-solver-pg', 'user_files', simulation_name)
    status_path = os.path.join(sim_local_dir, 'status.json')
    if os.path.isfile(status_path):
        try:
            with open(status_path, 'r') as f:
                payload = json.load(f)
            # pid/pid_start are reaper bookkeeping, not status: keep them out of the API shape
            return jsonify({k: v for k, v in payload.items() if k not in ('pid', 'pid_start')}), 200
        except Exception as e:
            logger.error(f"Failed to read status.json for {simulation_name}: {e}")
            send_error_to_mss('get_simulation_status', str(e))
            return jsonify({'error': str(e)}), 500

    # No status.json and no sim dir means this pod lost the run (restart) -- 404 so the listener
    # requeues. Keyed on sim_local_dir alone: DATA_DIR/<sim> is an emptyDir that outlives the
    # container, so requiring it too suppressed the 404 on the restart it exists for.
    if not os.path.isdir(sim_local_dir):
        logger.warning(f"No record of simulation {simulation_name} (no status.json, no working dir)")
        return jsonify({'error': 'no such simulation', 'simulation_name': simulation_name}), 404

    # Fallback to legacy diagnostics for non-async callers.
    try:
        logger.debug(f"Entering read_simulation_status() with batch_id={batch_id}")
        read_simulation_status(simulation_name, batch_id)
        return jsonify({'phase': 'unknown', 'message': 'no status.json on disk'}), 200
    except Exception as e:
        logger.error(f"Exception while reading simulation status files: {str(e)}")
        send_error_to_mss('get_simulation_status', str(e))
        return jsonify({'error': str(e)}), 500

############################################################################################################
# Name: def delete_simulation()
# Description: This function deletes the simulation directory based on the given simulation name.
# Calls the shutil.rmtree function to remove the simulation directory.
############################################################################################################
def delete_simulation(simulation_name):
    logger.debug("Within delete_simulation()")

    auth_err = _check_api_token()
    if auth_err is not None:
        return auth_err

    if simulation_name is None:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400
    bad_name = _reject_sim_name(simulation_name, creating=False)
    if bad_name is not None:
        return bad_name

    SIMULATION_DIR = os.path.join(DATA_DIR, simulation_name)
    LOCAL_DIR_SIM = os.path.join('powertwin-solver-pg', 'user_files', simulation_name)

    # URBANOPT_KEEP_RUN_DIR (bool, default false) makes this endpoint a no-op
    # so test verifiers can inspect artifacts after the listener marks the job done.
    keep = os.environ.get('URBANOPT_KEEP_RUN_DIR', '').strip().lower() == 'true'
    if keep:
        return jsonify({'message': 'cleanup skipped (URBANOPT_KEEP_RUN_DIR)', 'simulation_name': simulation_name}), 200

    removed = []
    try:
        if os.path.isdir(SIMULATION_DIR):
            shutil.rmtree(SIMULATION_DIR)
            removed.append(SIMULATION_DIR)
        if os.path.isdir(LOCAL_DIR_SIM):
            shutil.rmtree(LOCAL_DIR_SIM)
            removed.append(LOCAL_DIR_SIM)
        if not removed:
            return jsonify({'message': 'Nothing to delete', 'simulation_name': simulation_name}), 200
        # Leave a tombstone so a late or duplicate status poll reads 'deleted' rather than 404.
        # 404 means "this pod lost the run" and the listener requeues on it -- without this, a
        # straggler poll after a successful cleanup would re-run a finished simulation.
        write_status(LOCAL_DIR_SIM, 'deleted', simulation_name=simulation_name)
        logger.info(f"Simulation '{simulation_name}' cleanup removed: {removed}")
        return jsonify({'message': 'Simulation cleaned up', 'removed': removed}), 200
    except Exception as e:
        logger.error(f"Exception while trying to delete simulation: {str(e)}")
        send_error_to_mss('delete_simulation', str(e))
        return jsonify({'error': str(e)}), 500



# 2. Model and Configuration Management

############################################################################################################
# Name: def get_asset_config()
# Description: This function reads the feature files and returns the configuration of the asset.
# Searches for the feature file based on the asset ID and simulation name.
# Returns the feature file as a response. Available in the request_files directory.
############################################################################################################
def get_asset_config(simulation_name, asset_id):
    logger.debug("Within get_asset_config()")
    
    if not asset_id or not simulation_name:
        logger.error("Error: Asset ID and Simulation Name are required")
        return jsonify({'error': 'Asset ID and Simulation Name are required'}), 400
    bad_name = _reject_sim_name(simulation_name, creating=False)
    if bad_name is not None:
        return bad_name
           
    try:
        # Search to see if user_files directory exists, so that we can search for the feature file.
        # Confine to LOCAL_DIR: reject path traversal in simulation_name (e.g. ../../).
        SIMULATION_DIR = os.path.realpath(os.path.join(LOCAL_DIR, simulation_name))
        local_root = os.path.realpath(LOCAL_DIR)
        if SIMULATION_DIR != local_root and not SIMULATION_DIR.startswith(local_root + os.sep):
            logger.error("Invalid simulation_name (path traversal attempt)")
            return jsonify({'error': 'Invalid simulation_name'}), 400
        if not os.path.exists(SIMULATION_DIR):
            logger.error("Simulation directory does not exist")
            return jsonify({'error': 'Simulation directory does not exist'}), 404

        # Search for feature file dir
        FEATURE_FILE_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
        if not os.path.exists(FEATURE_FILE_DIR):
            logger.error(F"{FEATURE_FILE_DIR} directory found")
            return jsonify({'error': 'No feature files directory found'}), 404

        # Search for the asset ID in the feature files
        logger.debug(f"Searching for feature file in {FEATURE_FILE_DIR}")
        for file_name in os.listdir(FEATURE_FILE_DIR):
            if file_name.startswith(f"{asset_id}_") and file_name.endswith('.json'):
                file_path = os.path.join(FEATURE_FILE_DIR, file_name)
                
                # Define the path to save the requested configuration file
                DOWNLOAD_DIR = os.path.join(LOCAL_DIR, 'requested_files')
                os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                requested_file_path = os.path.join(DOWNLOAD_DIR, file_name)
                
                # Copy the configuration file to the requested_files directory
                shutil.copy(file_path, requested_file_path)
                
                response = send_file(requested_file_path, as_attachment=True)
                return response
    
        logger.error(f"No feature file found for asset ID: {asset_id}")
        return jsonify({'error': f'No feature file found for asset ID: {asset_id}'}), 404
    
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        send_error_to_mss('get_asset_config', str(e))
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def get_simulation_data()
# Description: This function reads the simulation statistics and returns the statistics of the simulation.
# Calls the get_asset_stats function to get the asset statistics from the database.
# Returns the statistics as a CSV file in the requested_files directory.
############################################################################################################
def get_simulation_data():
    from modules.diagnostics import get_asset_stats
    
    logger.debug("Within get_simulation_data()")
    csv_path = None  # Define outside try block so it's available in except
    
    try:
        # Get asset statistics from the database
        assets_list, filename = get_asset_stats()
        
        if not assets_list:
            return jsonify({'error': 'No assets found for the specified simulation'}), 404
            
        # Create directory for requested files if it doesn't exist
        requested_files_dir = os.path.join(LOCAL_DIR, 'requested_files')
        os.makedirs(requested_files_dir, exist_ok=True)
        
        # Define CSV file path
        csv_path = os.path.join(requested_files_dir, filename)
        
        # Write data to CSV file
        with open(csv_path, 'w', newline='') as csvfile:
            # Create CSV writer
            csvwriter = csv.writer(csvfile)
            
            # Write header row
            csvwriter.writerow([
                'Asset ID', 'Batch', 'Order Rank', 'Simulation Name', 'State', 'Weather File', 
                'Floor Area', 'Number of Stories', 'Complexity', 'UO Run Time', 
                'UO Process Time', 'Asset Name', 'Subtype','Status', 'Total Time'
            ])
            
            # Write data rows - extract values from dictionaries in proper order
            for asset in assets_list:
                csvwriter.writerow([
                    asset['asset_id'],
                    asset['batch'],
                    asset['order_rank'],
                    asset['simulation_name'],
                    asset['state'],
                    asset['weather_file'],
                    asset['floor_area'],
                    asset['number_of_stories'],
                    asset['complexity'],
                    asset['uorun_time'],
                    asset['uoprocess_time'],
                    asset['asset_name'],
                    asset['subtype'],
                    asset['status'],
                    asset['total_time']
                ])
        
        logger.info(f"Successfully created CSV file with {len(assets_list)} assets at {csv_path}")
        
        # Return success response with file path
        return jsonify({
            'message': 'Simulation stats exported successfully',
            'file_path': csv_path,
            'asset_count': len(assets_list)
        }), 200  # Fixed status code
        
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        if csv_path and os.path.exists(csv_path):
            os.remove(csv_path)  # Use remove() for files, not rmdir()
        return jsonify({'error': str(e)}), 500
    

# 3. Diagnostics and Logs

############################################################################################################
# Name: def recovery()
# Description: Recovers a corrupted simulation by KEEPING the unfinished assets ("Processing" /
#   "Not Processed Yet" / "Failed") and re-running only those; Finished assets' feature files are
#   pruned from the recovery set and their results carried over from the corrupted run.
# Calls the simulation_recovery function to recover the corrupted simulation.
############################################################################################################
def recovery():
    
    logger.debug("Within recovery()")
    corrupted_simulation_name = request.form.get('corrupted_simulation_name')
    recover_simulation_name = request.form.get('recover_simulation_name')
    batch_id = request.form.get('recover_batch_id', default=None, type=int)
    num_cores = int(request.form.get('recover_num_cores', 1))
    keep_dirs = request.form.get('keep_dirs', 'false').lower() == 'true'
    # POWERTWIN_KEEP_DIRS is applied via _sim_env_override around the recovery
    # run below (serialized + restored), not leaked onto the global env here.

    if not corrupted_simulation_name:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    if not recover_simulation_name:
        logger.error("Error: Recover simulation name is required.")
        return jsonify({'error': 'Recover simulation name is required.'}), 400
    for simulation_name in (corrupted_simulation_name, recover_simulation_name):
        bad_name = _reject_sim_name(simulation_name)
        if bad_name is not None:
            return bad_name
    
    # TODO: Set as global variable for consistency across all LOCAL_DIR references 
    LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    
    CORRUPTED_SIMULATION_DIR = os.path.join(LOCAL_DIR, corrupted_simulation_name)
    if not os.path.exists(CORRUPTED_SIMULATION_DIR):
        logger.error("Simulation directory not found")
        return jsonify({'error': 'Simulation directory not found'}), 404

    RECOVERY_DIR_LOCAL = os.path.join(LOCAL_DIR, f'{recover_simulation_name}')
    RECOVERY_DIR_CONTAINER = os.path.join(DATA_DIR, f'{recover_simulation_name}')
    if os.path.exists(RECOVERY_DIR_LOCAL) or os.path.exists(RECOVERY_DIR_CONTAINER):
        logger.debug("Recovery directory already exists")
        return jsonify({'error': 'Recovery directory already exists'}), 400

    os.makedirs(RECOVERY_DIR_CONTAINER, exist_ok=True)
    os.makedirs(RECOVERY_DIR_LOCAL, exist_ok=True)

    metadata_csv_path = os.path.join(CORRUPTED_SIMULATION_DIR, f'{corrupted_simulation_name}_metadata.csv')
    geojson_path = os.path.join(CORRUPTED_SIMULATION_DIR, f'{corrupted_simulation_name}_asset.geojson')

    # Both inputs are load-bearing (Q/A R-025: the old comment claimed only the metadata needed
    # handling; a missing GeoJSON raised a bare 500 with no MSS error).
    if not os.path.exists(metadata_csv_path):
        logger.error("Metadata CSV file not found in the corrupted simulation directory")
        _cleanup_recovery_dirs(RECOVERY_DIR_LOCAL, RECOVERY_DIR_CONTAINER)
        return jsonify({'error': 'Metadata CSV file not found in the corrupted simulation directory'}), 404
    if not os.path.exists(geojson_path):
        logger.error("GeoJSON file not found in the corrupted simulation directory")
        _cleanup_recovery_dirs(RECOVERY_DIR_LOCAL, RECOVERY_DIR_CONTAINER)
        return jsonify({'error': 'GeoJSON file not found in the corrupted simulation directory'}), 404

    # Copy and rename the metadata CSV and geojson to the recovery directory
    new_metadata_csv_path = os.path.join(RECOVERY_DIR_LOCAL, f'{recover_simulation_name}_metadata.csv')
    new_geojson_name_path = os.path.join(RECOVERY_DIR_LOCAL, f'{recover_simulation_name}_asset.geojson')

    try:
        shutil.copy(metadata_csv_path, new_metadata_csv_path)
        shutil.copy(geojson_path, new_geojson_name_path)
        
        logger.debug("Calling simulation_recovery from recovery()")
        with _sim_env_override(keep_dirs=keep_dirs):
            simulation_recovery(RECOVERY_DIR_CONTAINER, RECOVERY_DIR_LOCAL, CORRUPTED_SIMULATION_DIR, corrupted_simulation_name, recover_simulation_name, num_cores, batch_id)
        
        logger.debug("Exited simulation_recovery to recovery(), deleting recovery directory within the container")
        stopped = stopped_early(RECOVERY_DIR_CONTAINER)
        get_logs()
        shutil.rmtree(RECOVERY_DIR_CONTAINER)
        if stopped:
            return _stopped_response(recover_simulation_name)
        return jsonify({'message': 'Simulation recovery process completed successfully'}), 200


    except Exception as e:
        logger.error(f"Exception during simulation recovery: {str(e)}")
        send_error_to_mss('recovery', str(e))
        # Q/A D-44: leave nothing behind that would 400 the retry with 'already exists' --
        # but ONLY when the failed attempt renamed no rows. If batches already moved to the
        # recovery name, a same-name retry would rebuild its asset list WITHOUT them and
        # prune their feature files, stranding those rows (code-review). In that partial
        # state, keep the dirs so the retry 400s and a human resolves the names.
        try:
            from modules.diagnostics.db import get_bulk_batchids
            partially_renamed = bool(get_bulk_batchids(recover_simulation_name))
        except Exception:
            partially_renamed = True  # cannot verify -> keep dirs, fail safe
        if partially_renamed:
            logger.error(
                f"Recovery failed AFTER renaming rows to {recover_simulation_name}; "
                "keeping recovery directories so a blind same-name retry cannot strand them")
        else:
            _cleanup_recovery_dirs(RECOVERY_DIR_LOCAL, RECOVERY_DIR_CONTAINER)
        return jsonify({'error': str(e)}), 500



############################################################################################################
# Name: def get_logs()
# Description: This function zips the logs directory and sends the zip file as a response for download.
# Supports HPC mode by using shared storage when specified.
############################################################################################################
def get_logs(shared_storage=None):
    logger.debug("Within get_logs()")
    
    # Use centralized HPC detection
    is_hpc = is_hpc_environment()
    
    # Set up paths differently based on whether we're in HPC mode
    if is_hpc and shared_storage:
        logger.debug(f"Running in HPC mode with shared storage: {shared_storage}")
        # In HPC mode, we'll write logs to the shared storage directory
        REQUESTED_FILES_DIR = os.path.join(shared_storage, 'logs', 'requested_files')
        LOGS_DIR = os.path.join(shared_storage, 'logs')
    else:
        # Regular container mode
        REQUESTED_FILES_DIR = os.path.join(LOCAL_DIR, 'requested_files')
        LOGS_DIR = os.path.join('logs')
    
    # Create directories if they don't exist
    os.makedirs(REQUESTED_FILES_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    REQUESTED_LOG_FILE = os.path.join(REQUESTED_FILES_DIR, 'dev.log')
    LOG_FILE = os.path.join(LOGS_DIR, 'dev.log')
    
    if not os.path.exists(LOG_FILE):
        logger.warning(f"Log file does not exist at {LOG_FILE}, creating empty file")
        # Create empty log file if it doesn't exist
        with open(LOG_FILE, 'w') as file:
            file.write(f"Log file created at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    try:
        with open(LOG_FILE, 'r') as file:
            logs = file.read()
        # Save the log file to the requested_files directory
        with open(REQUESTED_LOG_FILE, 'w') as file:
            file.write(logs)

        logger.debug(f"Log file saved to {REQUESTED_LOG_FILE}")
    except Exception as e:
        logger.error(f"Exception while reading/writing log file: {str(e)}")
        if not is_hpc:  # Only return response in non-HPC mode
            return jsonify({'error': str(e)}), 500
    
    # Only try to render template in non-HPC mode
    if not is_hpc:
        with open(REQUESTED_LOG_FILE, 'r') as file:
            logs = file.read()
        # Render the logs in the template
        return render_template('logs.html', logs=logs)


############################################################################################################
# Name: def update_asset()
# Description: This function forces an asset's status to 'Failed' so it will be reprocessed during recovery.
# Calls the update_status function to set the asset status to 'Failed'.
############################################################################################################
def update_asset():
    from modules.diagnostics.db import update_status
    
    logger.debug("Within update_asset()")
    
    # Get parameters from request
    asset_id = request.form.get('asset_id')
    simulation_name = request.form.get('simulation_name')
    
    # Validate parameters
    if not asset_id:
        logger.error("Missing asset_id parameter")
        return jsonify({'error': 'Missing asset_id parameter'}), 400
        
    if not simulation_name:
        logger.error("Missing simulation_name parameter")
        return jsonify({'error': 'Missing simulation_name parameter'}), 400
    
    try:
        # Update the asset status to Failed
        result = update_status('Failed', asset_id, simulation_name)
        
        if result:
            logger.info(f"Successfully marked asset {asset_id} in simulation {simulation_name} as Failed")
            return jsonify({
                'success': True, 
                'message': f'Asset {asset_id} has been marked as Failed and will be reprocessed during recovery'
            })
        else:
            logger.error(f"Failed to update status for asset {asset_id}")
            return jsonify({'error': f'Failed to update status for asset {asset_id}'}), 500
            
    except Exception as e:
        logger.error(f"Error updating asset {asset_id}: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

############################################################################################################
# Name: def log_message()
# Description: This function logs a message to the dev.log file.
# Calls the log_message function to log a message to the dev.log file.
############################################################################################################
def log_message():
    data = request.get_json()
    message = data.get('message')
    log_type = data.get('type', 'log')
    
    # Create a timestamp for the log entry
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Define the log file path
    log_txt = os.path.join('logs','dev.log')
    os.makedirs(os.path.dirname(log_txt), exist_ok=True)

    # Append the log entry to the log file
    with open(log_txt, 'a') as log_file:
        log_file.write(f"[{timestamp}] [{log_type.upper()}] {message}\n")

    return jsonify({'status': 'success', 'log_file': log_txt}), 200


############################################################################################################
# Name: def get_simulation_results()
# Description: Serve the persisted results.json for the API to ingest. 404 when the simulation is still
# in flight (no results.json yet) or never existed. The API polls until completed and then reads this.
############################################################################################################
def get_simulation_results(simulation_name):
    auth_err = _check_api_token()
    if auth_err is not None:
        return auth_err
    if simulation_name is None:
        return jsonify({'error': 'Simulation name is required.'}), 400
    bad_name = _reject_sim_name(simulation_name, creating=False)
    if bad_name is not None:
        return bad_name
    sim_local_dir = os.path.join('powertwin-solver-pg', 'user_files', simulation_name)
    results_path = os.path.join(sim_local_dir, 'results.json')
    if not os.path.isfile(results_path):
        return jsonify({'error': 'results.json not found', 'simulation_name': simulation_name}), 404
    try:
        with open(results_path, 'r') as f:
            return jsonify(json.load(f))
    except Exception as e:
        logger.error(f"Failed to read results.json for {simulation_name}: {e}")
        send_error_to_mss('get_simulation_results', str(e))
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def simulation_datelevels()
# Description: The datelevel table (label -> reporting frequency, resample,
# datelevel) and its default, read from upload/reporting_frequency_map.json. The db api
# and the frontend build their datelevel options from this instead of a copy.
############################################################################################################
def simulation_datelevels():
    auth_err = _check_api_token()
    if auth_err is not None:
        return auth_err
    fmap = _load_reporting_freq_map()
    return jsonify({
        'datelevels': fmap.get('datelevels', {}),
        'default': fmap.get('default_datelevel'),
    })


############################################################################################################
# Name: def resolve_defaults_preview()
# Description: Given an asset's metadata + building_type, return the map of
# dynamic-default values that resolve_default() would produce. Single source
# of truth for the resolver -- db-api proxies here instead of duplicating the
# logic + the reference_data JSONs. Response includes dynamic_defaults_enabled
# so callers can distinguish "feature off" from "no override available", plus
# a per-field `levels` map tagging how each value was resolved (vintage_specific
# / region_all / building_type_only / flat_default). The frontend uses this
# to surface confidence indicators like "(based on age + region)" vs
# "(estimated, region average)" vs "(national default)".
############################################################################################################
DYNAMIC_FIELDS = [
    'number_of_occupants',
    'heating_system_fuel_type',
    'cooling_system_fuel_type',
    'service_water_heating_fuel_type',
    'heating_system_type',
    'water_heater_type',
    'window_type',
    'wall_material', 'roof_material',
    'wall_r_value', 'roof_r_value', 'window_to_wall_ratio',
    'floor_height',   # commercial: the prototype's typical story, or 3.6 m for Vacant and under 1,000 ft2 (2026-09-30); houses get the 8 ft line below
    'duct_location', 'duct_leakage_pct', 'duct_insulation_r',
    'heating_setpoint_f', 'cooling_setpoint_f', 'infiltration_ach50',
    'system_type',
    'com_heating_adjustment_f', 'com_cooling_adjustment_f', 'com_airtightness_75pa',
    'pv_system_kw', 'pv_azimuth_deg',
    'lighting_led_fraction', 'lighting_cfl_fraction',   # resolver 3.5: a house's county LED / CFL lighting shares
    'bedrooms_per_unit',
    'foundation_type', 'garage_cars',
    'cooling_type_detail', 'cooling_fraction_served',
    'clothes_dryer_fuel', 'cooking_range_fuel',
    'misc_gas_grill', 'misc_gas_lighting', 'misc_gas_fireplace',
    'heating_equipment_detail', 'heating_has_ducts',
    'weekday_start_time', 'weekday_duration',
    'weekend_start_time', 'weekend_duration',
]


def resolve_defaults_preview():
    auth_err = _check_api_token()
    if auth_err is not None:
        return auth_err
    try:
        payload = request.get_json(silent=True) or {}
        # build_asset_ctx lowercases keys; drop a field by the same key it reads (review 2026-09-29)
        metadata = {str(k).lower(): v for k, v in (payload.get('metadata') or {}).items()}
        building_type = payload.get('building_type')
        if not isinstance(building_type, str):
            building_type = None        # a list or number here used to raise inside the resolvers (review 2026-09-30)
        # The simulation keys its context on the IECC zone of the building's coordinates (generateFeatureFile
        # read_metadata -> get_location -> get_climate_zone), and commercial envelope R, among others, is zone-keyed.
        # Without it the preview fell back to census region: a Teton office showed a Standard R-4 wall that the
        # simulation builds as a Super Insulated R-17, so re-picking the shown tier would have downgraded it (2026-09-29).
        climate_zone = older_climate_zone = None
        if metadata.get('latitude') not in (None, '') and metadata.get('longitude') not in (None, ''):
            try:
                lat, lon = float(metadata['latitude']), float(metadata['longitude'])
                climate_zone = get_climate_zone(lat, lon)
                older_climate_zone = get_climate_zone(lat, lon, '15')   # the zone map the code-minimum presets use
            except (TypeError, ValueError, OverflowError):
                climate_zone = older_climate_zone = None
        ctx = build_asset_ctx(metadata, building_type=building_type, climate_zone=climate_zone)
        resolved = {}
        levels = {}
        for f in DYNAMIC_FIELDS:
            # A field's default is what it resolves to when the user leaves it unset: resolve it from the metadata
            # without that field (every other saved choice still applies). Resolving it with its own saved value in
            # the ctx showed 'furnace' on a house whose saved 'boiler' hid an electric-resistance default, so
            # re-picking the shown default rebuilt the house (review 2026-09-28, reproduced on Teton 15973);
            # build_asset_ctx's confirm check compares against the same unset value.
            # only a field build_asset_ctx carries into the ctx can condition its own default; rebuild just for those
            f_ctx = build_asset_ctx({k: v for k, v in metadata.items() if k != f}, building_type=building_type,
                                    climate_zone=climate_zone) \
                if f in ctx and metadata.get(f) not in (None, '') else ctx
            value, level = resolve_default(f, f_ctx)
            if value is not None:
                resolved[f] = value
                levels[f] = level
            else:
                levels[f] = 'flat_default'
        # An unset floor_height shows what URBANopt builds (a house's 8 ft ceiling, a commercial 3.6 m story), which
        # get_param now returns for a typed building whether or not dynamic defaults are on (2026-09-30), so
        # re-picking the shown value changes nothing.
        if 'floor_height' not in resolved and ctx.get('building_type'):
            resolved['floor_height'] = get_param({}, 'floor_height', ctx)
            levels['floor_height'] = 'building_type_only'
        # A saved heat pump water heater makes the water-heating fuel electricity (get_param, DD on or off), so the form
        # shows that whatever filled the fuel above: with DD off nothing resolves it (the flat 'natural gas', live check
        # 2026-09-30), and with DD on the commercial fuel resolver fills the donor's gas first (review 2026-10-03).
        if metadata.get('water_heater_type') == HPWH:
            resolved['service_water_heating_fuel_type'] = 'electricity'
            levels['service_water_heating_fuel_type'] = 'building_type_only'
        # R-value presets for the modal (Insulation Presets Plan): picking one fills wall_r_value / roof_r_value.
        # A missing or unpulled presets table drops the presets, not the whole preview (review 2026-10-03).
        try:
            cec = None
            if ctx.get('state') == 'CA' and is_dynamic_defaults_enabled():
                from modules.utils.cec_zones import get_cec_climate_zone
                cec = get_cec_climate_zone(metadata.get('latitude'), metadata.get('longitude'), metadata.get('postcode'))
            presets = insulation_presets(ctx, older_climate_zone, cec)
        except Exception as exc:   # noqa: BLE001
            logger.warning(f"resolve_defaults_preview: insulation presets unavailable ({type(exc).__name__}: {exc})")
            presets = {}
        return jsonify({
            'dynamic_defaults_enabled': is_dynamic_defaults_enabled(),
            'context': ctx,
            'resolved': resolved,
            'levels': levels,
            'presets': presets,
        })
    except Exception as e:
        logger.error(f"resolve_defaults_preview error: {e}")
        return jsonify({'error': str(e)}), 500

