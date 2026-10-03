import os
import glob
import json
import time
import zipfile
import signal
import subprocess
import shutil

from .parallel import run_parallel_batches
from modules.utils import initialize_logger
from modules.utils.hpc_environment import is_hpc_environment
from modules.utils.check_uo import get_urbanopt_command
from modules.utils.command_timer import uo_timeout
from modules.simulation.stop_UOsim import stop_requested, mark_stopped_early

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Initialize UOSim', external_log_dir)

# Set the mapper file path based on the environment
if os.environ.get('SLURM_JOB_ID'):
    MAPPER_FILE = os.path.join('/solver', 'upload', 'PowerTwin.rb')
else:
    MAPPER_FILE = os.path.join('upload', 'PowerTwin.rb')

STANDARDS_PATCH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'patches',
                                               'patch_openstudio_standards.py'))

# urbanopt-scenario's ScenarioCSV#load_mapper_files, verbatim in effect: resolve the bundle path
# from runner.conf and install the project bundle with the extension Runner only when that path is
# missing or empty. Prints the path last.
_PROJECT_BUNDLE_RB = r"""
require 'bundler'
require 'pathname'
require 'openstudio/extension'
root = Dir.pwd
opts = OpenStudio::Extension::RunnerConfig.default_config(root)
if File.exist?(File.join(root, OpenStudio::Extension::RunnerConfig::FILENAME))
  conf = OpenStudio::Extension::RunnerConfig.new(root)
  opts = opts.merge(conf.options.reject { |_k, v| v.nil? || (v.is_a?(String) && v == '') })
end
if !opts.key?(:bundle_install_path) || opts[:bundle_install_path] == ''
  opts[:bundle_install_path] = File.join(root, '.bundle/install/')
else
  opts[:bundle_install_path] = Pathname.new(opts[:bundle_install_path]).cleanpath.to_s
end
path = opts[:bundle_install_path]
Bundler.with_unbundled_env { OpenStudio::Extension::Runner.new(root, [], opts) } if !Dir.exist?(path) || Dir.empty?(path)
puts path
"""


def ensure_standards_patched():
    """On the cluster, patch the job's shared gem home (GEM_HOME / BUNDLE_PATH) before any simulation runs. The job
    script patches it after its bundle prewarm, but a campaign launched from an older per-campaign script copy
    does not (review 2026-09-28): with this every batch process of a current image patches it itself. The patch
    is idempotent and its writes atomic, so parallel batches on one gem home are safe. Raises when the patch
    cannot apply (the job's gems would reproduce the failures it fixes)."""
    roots = [os.environ[k] for k in ('BUNDLE_PATH', 'GEM_HOME') if os.environ.get(k)]
    if not roots:
        return False
    done = subprocess.run(['python3', STANDARDS_PATCH, *sorted(set(roots))], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"openstudio-standards patch failed on {roots}: {(done.stderr or done.stdout).strip()}")
    logger.info(f"openstudio-standards patch on {roots}: {done.stdout.strip()}")
    return True


def _run_bundle_step(project_dir):
    """Run _PROJECT_BUNDLE_RB in project_dir in its own process group, bounded like `uo process`
    (uo_timeout, 2 h cap); returns (returncode, stdout, stderr)."""
    proc = subprocess.Popen(['ruby', '-e', _PROJECT_BUNDLE_RB], cwd=project_dir, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=uo_timeout(cap=7200))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise RuntimeError(f"project bundle install in {project_dir} exceeded {uo_timeout(cap=7200)} s")
    return proc.returncode, out or '', err or ''


def _run_group(cmd, cwd):
    """Run a bundle command in its own process group, bounded like the other bundle steps (uo_timeout, 2 h cap);
    on a timeout kill the whole group, not just the bundle process. Returns (rc, out, err), rc None on timeout."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=uo_timeout(cap=7200))
        return proc.returncode, out or '', err or ''
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return None, '', ''


def _offline_project_bundle(project_dir):
    """Build the project bundle from the image's own gems, without the network (2026-09-29, requested from Al's
    benchmark session): the extension Runner runs `bundle lock --add_platform ruby` and `bundle update` against
    rubygems.org, about 580 MB per run, and under many concurrent fetches on a lossy or rootless link the downloads
    corrupt or time out (Zlib::DataError, Net::OpenTimeout, TLS bad record mac), failing every building. Every gem
    the template needs is installed with its .gem cached in the image, so `--local` resolves and installs the same
    versions (parallel 1.19.1) the online path does. Writes .bundle/config and Gemfile.lock exactly where the Runner
    would, so it then finds a complete bundle and never runs. Returns (ok, reason); on failure the partial state is
    cleared and the caller falls back to the online install."""
    conf = os.path.join(project_dir, 'runner.conf')
    custom = ''
    if os.path.isfile(conf):
        try:
            with open(conf) as fh:
                data = json.load(fh)
            custom = (data.get('bundle_install_path') or '') if isinstance(data, dict) else ''
        except (OSError, ValueError):
            custom = ''
    if custom:
        return False, f"runner.conf sets bundle_install_path {custom!r}"
    # absolute: callers pass the project relative to /solver, and Bundler resolves a relative configured path
    # inside the project, so the gems landed in a nested data/... tree, `uo run` found .bundle/install empty and
    # fell back into the Runner (Gem::ConflictError on parallel, 2026-09-29, reproduced end to end)
    project_dir = os.path.abspath(project_dir)
    path = os.path.join(project_dir, '.bundle', 'install')
    cache = os.path.join(project_dir, 'vendor', 'cache')
    gem_caches = [os.path.join(d, 'cache') for d in dict.fromkeys(
        filter(None, [os.environ.get('GEM_HOME')] + os.environ.get('GEM_PATH', '').split(os.pathsep)))]

    def clear(partial):
        shutil.rmtree(cache, ignore_errors=True)
        if os.path.isdir(os.path.dirname(cache)) and not os.listdir(os.path.dirname(cache)):
            os.rmdir(os.path.dirname(cache))
        if partial:
            shutil.rmtree(os.path.join(project_dir, '.bundle'), ignore_errors=True)
            lock = os.path.join(project_dir, 'Gemfile.lock')
            if os.path.exists(lock):
                os.remove(lock)

    # Resolve against the image's installed gems first: once a project path is configured, `--local` sees only
    # gems under that path. Then install from the image's cached .gem files, linked in as the app cache.
    steps = (['bundle', 'lock', '--local', '--add-platform', 'ruby'],
             ['bundle', 'config', 'set', '--local', 'path', path],
             'link-cache',
             ['bundle', 'install', '--local'])
    try:
        for cmd in steps:
            if cmd == 'link-cache':
                os.makedirs(cache, exist_ok=True)
                for d in gem_caches:
                    for g in glob.glob(os.path.join(d, '*.gem')):
                        dst = os.path.join(cache, os.path.basename(g))
                        if not os.path.lexists(dst):
                            os.symlink(g, dst)
                continue
            rc, out, err = _run_group(cmd, project_dir)
            if rc != 0:
                why = 'timed out' if rc is None else ' | '.join((err or out).strip().splitlines()[-3:])
                clear(partial=True)
                return False, f"`{' '.join(cmd[:2])}` {why}"
    except Exception as e:   # anything else (a bad link, a full disk): clean up and let the online install run
        clear(partial=True)
        return False, f'{type(e).__name__}: {e}'
    clear(partial=False)
    return True, path


def prepare_project_bundle(project_dir, simulation_dir=None):
    """Install the project's gem bundle before any batch runs, exactly where and how the first
    `uo run` would, then patch it. `uo run` installs a fresh openstudio-standards into the project
    when the bundle is missing, so a patch baked into the image never reaches a simulation
    (2026-09-24); patching here covers every building of the run. Not on the cluster: its job
    script installs the bundle into the per-job BUNDLE_PATH after this runs and patches it there.

    Returns False when POST /api/simulation/stop killed the install (its command line carries the
    stop sweep's 'bundle'/'openstudio' markers): the run then ends 'stopped' through the batch
    loop's marker check instead of 'failed', which the db listener would requeue (review
    2026-09-28, reproduced). Bounded like `uo process` (uo_timeout, 2 h cap) and killed as a
    process group, a real failure reports Ruby's own error rather than the program text, gems come
    over https, and a partial bundle (failed fetches the Runner swallows) is reinstalled once from a
    clean state, then fails loud with the fetch errors."""
    if is_hpc_environment():
        return True
    # urbanopt's project template fetches gems over plain http; on a lossy link (exodus' NIC corrupts
    # frames) a damaged .gem arrives undetected and Bundler crashes mid-install (Zlib::DataError,
    # 'is not an octal string', 2026-09-29). https fails such a transfer and retries it. Same gems.
    gemfile = os.path.join(project_dir, 'Gemfile')
    if os.path.isfile(gemfile):
        with open(gemfile) as fh:
            src = fh.read()
        if "source 'http://rubygems.org'" in src:
            with open(gemfile, 'w') as fh:
                fh.write(src.replace("source 'http://rubygems.org'", "source 'https://rubygems.org'"))
        ok, detail = _offline_project_bundle(project_dir)
        if ok:
            logger.info(f"Project bundle for {project_dir} built offline from the image's gems")
        elif simulation_dir and stop_requested(simulation_dir):
            logger.info(f"Project bundle install in {project_dir} ended by a stop request")
            return False
        else:
            logger.warning(f"Offline project bundle for {project_dir} not built ({detail}); installing online")
    for attempt in (1, 2):
        rc, out, err = _run_bundle_step(project_dir)
        if rc != 0:
            if simulation_dir and stop_requested(simulation_dir):
                logger.info(f"Project bundle install in {project_dir} ended by a stop request")
                return False
            tail = ' | '.join((err or out or '').strip().splitlines()[-5:])
            raise RuntimeError(f"project bundle install failed in {project_dir} (exit {rc}): {tail}")
        lines = out.strip().splitlines()
        if not lines:
            raise RuntimeError(f"project bundle step in {project_dir} printed no bundle path: {' | '.join(err.strip().splitlines()[-5:])}")
        bundle_path = lines[-1]
        # The extension Runner swallows a failed `bundle update` (download timeouts or a corrupt gem on a flaky
        # link, 2026-09-29) and still exits 0 with a partial .bundle/install; `uo run` then sees a non-empty
        # bundle, never reinstalls, and every building fails on a misleading Gem::ConflictError. Check the
        # bundle; on a partial one clear it and install once more, then fail with the fetch errors.
        try:
            check = subprocess.run(['bundle', 'check'], cwd=project_dir, capture_output=True, text=True,
                                   timeout=uo_timeout(cap=7200))
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"`bundle check` in {project_dir} exceeded {uo_timeout(cap=7200)} s")
        if check.returncode == 0:
            break
        if simulation_dir and stop_requested(simulation_dir):
            logger.info(f"Project bundle install in {project_dir} ended by a stop request")
            return False
        reasons = [l.strip() for l in (out + err).splitlines()
                   if any(k in l for k in ('Error running command', 'FetchError', 'Could not', 'Zlib::', 'Errno::'))][:3]
        if attempt == 2:
            raise RuntimeError(f"project bundle in {project_dir} is incomplete after two installs: "
                               f"{' | '.join(reasons) or check.stdout.strip()[-300:]}")
        logger.warning(f"Project bundle in {project_dir} incomplete ({' | '.join(reasons)[:300]}); reinstalling once")
        # the Runner decides to install from .bundle/config and Gemfile.lock existing, not from the gems being
        # there: clearing only the install dir makes the rerun a silent no-op
        shutil.rmtree(bundle_path, ignore_errors=True)
        shutil.rmtree(os.path.join(project_dir, '.bundle'), ignore_errors=True)
        lock = os.path.join(project_dir, 'Gemfile.lock')
        if os.path.exists(lock):
            os.remove(lock)
    roots = [bundle_path] + [os.environ[k] for k in ('BUNDLE_PATH', 'GEM_HOME') if os.environ.get(k)]
    patched = subprocess.run(['python3', STANDARDS_PATCH, *roots], capture_output=True, text=True)
    if patched.returncode != 0:
        raise RuntimeError(f"openstudio-standards patch failed for {project_dir}: "
                           f"{(patched.stderr or patched.stdout).strip()}")
    logger.debug(f"Project bundle {bundle_path}: {patched.stdout.strip()}")
    return True


############################################################################################################
# Name: prepare_record(SIMULATION_DIR,LOCAL_DIR,simulation_name)
# Description: This function prepares the record for the simulation by creating the UrbanOpt project and copying the mapper file.
#   It then runs the simulations in parallel.
#   The function returns the total number of batches and assets.
############################################################################################################
def prepare_record(SIMULATION_DIR, LOCAL_DIR, simulation_name):
    
    # Use centralized HPC detection
    is_hpc = is_hpc_environment()
    from modules.diagnostics import get_asset_total, get_batch_total, update_status, get_bulk_assets, bulk_update_status, get_failed_assets
    

    
    batches = get_batch_total(simulation_name)
    assets = get_asset_total(simulation_name=simulation_name)

    logger.debug(f"Total batches: {batches}, Total assets in database: {assets}\n" 
                        f"Preparing to run simulations..."
    )
    
    # If no batches exist yet, we need to wait for asset analysis to complete
    if batches == 0:
        logger.warning(f"No batches found for simulation {simulation_name}. Asset analysis may not have completed yet or database may have issues.")
        
        # Check if we have assets but no batches (batch distribution didn't happen)
        if assets > 0:
            logger.info(f"Found {assets} assets but no batches. Attempting to distribute assets to batches.")
            try:
                from modules.diagnostics import distribute_assets_to_batches
                # Use total cores available for batch calculation
                import multiprocessing
                requested_cores = int(os.environ.get('SLURM_CPUS_PER_TASK', multiprocessing.cpu_count()))
                num_cores = max(1, min(requested_cores, assets))

                if num_cores != requested_cores:
                    logger.warning(
                        f"Requested cores ({requested_cores}) exceeds total assets ({assets}). "
                        f"Using {num_cores} core(s) instead."
                    )
                else:
                    logger.info(f"Using {num_cores} cores for batch distribution")

                distribute_assets_to_batches(num_cores, simulation_name)
                
                # Re-check batch count
                batches = get_batch_total(simulation_name)
                logger.info(f"After distribution: {batches} batches created")
            except Exception as e:
                logger.error(f"Failed to distribute assets to batches: {e}")
                return []
        else:
            logger.error(f"No assets found for simulation {simulation_name}. Feature file generation may have failed.")
            return []
    
    UO_SIMULATION_DIR = os.path.join(SIMULATION_DIR,'urbanopt_simulation')
    MAPPER_DESTINATION = os.path.join(UO_SIMULATION_DIR, 'mappers')
    WEATHER_DESTINATION = os.path.join(UO_SIMULATION_DIR, 'weather')

    
    # Create PowerTwin UrbanOpt Project if it doesn't exist (it shouldnt)
    if not os.path.exists(UO_SIMULATION_DIR):
        logger.debug(f"Creating UrbanOpt project at {UO_SIMULATION_DIR}")
        
        try:
            uo_cmd = get_urbanopt_command()
            logger.debug(f"Using UrbanOpt command: {uo_cmd}")

            # Always scaffold with --combined so both commercial and residential
            # workflows are available per-asset. PowerTwin.rb picks the right
            # branch per building_type at runtime; the residential-measures tree
            # is required for the residential branch, ignored for commercial.
            result = subprocess.run(
                f"{uo_cmd} create --combined --project-folder {UO_SIMULATION_DIR}",
                shell=True,
                check=True,
                capture_output=True,
                text=True
            )
            logger.debug(f"UrbanOpt project creation output: {result.stdout}")

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create UrbanOpt project: {e}")
            logger.error(f"Command output: {e.output}")
            logger.error(f"Command stderr: {e.stderr}")
            raise
        except Exception as e:
            logger.error(f"Error discovering or running UrbanOpt command: {e}")
            raise
        os.makedirs(MAPPER_DESTINATION, exist_ok=True)
        
        shutil.rmtree(WEATHER_DESTINATION)

        # NOTE: Baseline ruby file should never be deleted, it is the parent file
        # for rb_file in glob.glob(os.path.join(MAPPER_DESTINATION, "*.rb")):
        #         if os.path.basename(rb_file) != "Baseline.rb":
        #             os.remove(rb_file)
                    
        # Remove all existing .rb files to prevent conflicts with batch-specific mappers
        for rb_file in glob.glob(os.path.join(MAPPER_DESTINATION, "*.rb")):
            os.remove(rb_file)
            logger.debug(f"Removed existing mapper file: {os.path.basename(rb_file)}")

    # Deploy the mapper, its siblings and the custom measures on EVERY run, not only when the
    # project is first created: project dirs persist between runs (the cleanup keeps mappers/ and
    # measures/), so gating these on the project's absence would pin the first copy forever -- a
    # re-vendored measure or mapper would never reach an existing sim dir, and <project>/measures
    # shadows the gem at runtime. All three are content-syncs: no-ops when already identical.
    logger.debug(f"Deploying mapper + measures into {UO_SIMULATION_DIR}")
    from modules.simulation.mapper_setup import deploy_mapper, deploy_measures
    upload_dir = os.path.dirname(MAPPER_FILE)
    deploy_mapper(upload_dir, MAPPER_DESTINATION)

    # Overlay our customized base_workflow.osw (adds set_window_construction,
    # set_service_water_heating_fuel, set_people_per_floor_area, the roof
    # insulation step, etc.) on top of urbanopt-cli's stock template.
    custom_osw = os.path.join(upload_dir, 'base_workflow.osw')
    if os.path.exists(custom_osw):
        shutil.copy(custom_osw, os.path.join(MAPPER_DESTINATION, 'base_workflow.osw'))

    # Stage our custom measures under <project>/measures/ so openstudio CLI
    # can resolve them by measure_dir_name without explicit measure_paths.
    deploy_measures(upload_dir, UO_SIMULATION_DIR)
    if not prepare_project_bundle(UO_SIMULATION_DIR, SIMULATION_DIR):
        # a stop killed the install: record it here, since run_batch only marks a run it finds assets to skip in
        # (review 2026-09-29: a run with no assets left would answer 'ran successfully')
        mark_stopped_early(SIMULATION_DIR)

    # Initialize all assets with "Not Processed Yet" status using bulk update
    try:
        assets_data = get_bulk_assets(simulation_name)
        if assets_data:
            # Handle both return types: List[int] (PostgreSQL) or List[Dict] (SQLite)
            if isinstance(assets_data[0], dict):
                # SQLite/HPC environment returns dictionaries
                asset_ids = [asset['asset_id'] for asset in assets_data]
            else:
                # PostgreSQL/Docker environment returns list of integers directly
                asset_ids = assets_data

            # Don't reset assets already recorded Failed (e.g. no-coords buildings excluded from the
            # run): Failed is terminal -- resetting to 'Not Processed Yet' would lose it.
            # Finished is protected inside bulk_update_status itself (Q/A R-027b), so a
            # same-name re-submission no longer re-runs completed work.
            failed_ids = set(get_failed_assets(simulation_name) or [])
            asset_ids = [a for a in asset_ids if a not in failed_ids]

            logger.info(f"Setting initial status 'Not Processed Yet' for {len(asset_ids)} assets in {simulation_name}")
            bulk_update_status(asset_ids, "Not Processed Yet", simulation_name)
        else:
            logger.warning(f"No assets found for simulation {simulation_name} to initialize status")
    except Exception as e:
        logger.error(f"Failed to initialize asset status for {simulation_name}: {e}")

    if is_hpc: return list(range(batches))
        
    # Run simulations in parallel (Docker mode only)
    try:
        logger.info(f"Running {batches} batches of simulations in Docker mode...")
        batch_range = list(range(batches))
        run_parallel_batches(
            batch_range, 
            SIMULATION_DIR, 
            LOCAL_DIR, 
            simulation_name
        )
    except Exception as e:
        logger.error(f"Error running simulations: {e}")
        return

def pre_copy_weather_files(SIMULATION_DIR, simulation_name):
    """Pre-copy all weather files before parallel execution to prevent race conditions."""
    from modules.diagnostics import get_distinct_weather_files

    weather_files = get_distinct_weather_files(simulation_name)
    if not weather_files:
        logger.warning(f"No weather files found for simulation {simulation_name}")
        return

    UO_SIMULATION_DIR = os.path.join(SIMULATION_DIR, 'urbanopt_simulation')
    WEATHER_DESTINATION = os.path.join(UO_SIMULATION_DIR, 'weather')
    os.makedirs(WEATHER_DESTINATION, exist_ok=True)

    if os.environ.get('SLURM_JOB_ID') and os.environ.get('HPC_SHARED_STORAGE'):
        weather_files_base_dir = os.path.join(os.environ.get('HPC_SHARED_STORAGE'), 'weather_files')
    elif os.environ.get('SLURM_JOB_ID'):
        weather_files_base_dir = os.path.join('/solver', 'app', 'urbanopt', 'weather_files')
    else:
        weather_files_base_dir = os.path.join('app', 'urbanopt', 'weather_files')

    logger.info(f"Weather source: SLURM_JOB_ID={os.environ.get('SLURM_JOB_ID')}, "
                f"HPC_SHARED_STORAGE={os.environ.get('HPC_SHARED_STORAGE')}, "
                f"base_dir={weather_files_base_dir}")

    copied = 0
    for weather_file in weather_files:
        # DB stores weather_file with .epw extension — strip it for path construction
        if weather_file.endswith('.epw'):
            weather_file = weather_file[:-4]
        base_path = os.path.join(weather_files_base_dir, weather_file, weather_file)
        for ext in ["ddy", "stat", "epw"]:
            src = f"{base_path}.{ext}"
            dst = os.path.join(WEATHER_DESTINATION, f"{weather_file}.{ext}")
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
                copied += 1

    logger.info(f"Pre-copied weather files for {len(weather_files)} stations ({copied} files) to {WEATHER_DESTINATION}")


############################################################################################################
# Name: initialize_uo(SIMULATION_DIR,LOCAL_DIR, simulation_name)
# Description: This function initializes the UrbanOpt simulation by extracting the feature files from the zip file.
#   It then prepares the record for the simulation by creating the UrbanOpt project and copying the mapper file.
#   The function then runs the simulations in parallel.
#   Returns the batch range in HPC mode (batches run externally via SLURM); returns None
#   locally, where the batches are executed in-process before returning.
############################################################################################################
def initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name):
    start_time = time.time()
    
    # Use centralized HPC detection
    is_hpc = is_hpc_environment()
    

    FEATURE_FILE_ZIP = os.path.join(SIMULATION_DIR, 'feature_files.zip')
    OUTPUT_FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, "feature_files")
    LOCAL_UOSIMULATION_DIR = os.path.join(LOCAL_DIR, 'urbanopt_simulation')
    os.makedirs(LOCAL_UOSIMULATION_DIR, exist_ok=True)
        
    # Extract the feature files
    if FEATURE_FILE_ZIP.endswith('.zip'):
        logger.debug(f"Extracting feature files from {FEATURE_FILE_ZIP}")
        os.makedirs(OUTPUT_FEATURE_FILES_DIR, exist_ok=True)
        # Ensure write permissions
        os.chmod(OUTPUT_FEATURE_FILES_DIR, 0o777)
        
        with zipfile.ZipFile(FEATURE_FILE_ZIP, 'r') as zip_ref:
            zip_ref.extractall(OUTPUT_FEATURE_FILES_DIR)
        
        # Get a list of all feature files in the directory
        feature_files = glob.glob(os.path.join(OUTPUT_FEATURE_FILES_DIR, "*.json"))
        total_feature_files = len(feature_files)
        logger.info(f"Total feature files extracted: {total_feature_files}")
    else:
        logger.error(f"No zip file found named: {FEATURE_FILE_ZIP}")
        return

    # Prepare the database and setup UrbanOpt project
    batch_range = prepare_record(SIMULATION_DIR, LOCAL_DIR, simulation_name)
    if is_hpc_environment() and not batch_range:
        # Q/A D-48: an empty batch range means asset analysis or distribution failed; proceeding
        # used to run a zero-batch campaign that reported success. Fail loud instead.
        raise RuntimeError(
            f"prepare_record produced no batches for {simulation_name}; "
            "asset analysis/distribution failed -- refusing to run an empty campaign")

    # Pre-copy weather files before parallel execution to prevent race conditions
    pre_copy_weather_files(SIMULATION_DIR, simulation_name)

    # In HPC mode, we return the batch range for external parallel execution
    if is_hpc:
        logger.info(f"HPC mode active - returning batch range for external parallel execution")
        return batch_range

    end_time = time.time()
    duration_seconds = end_time - start_time

    # Calculate hours, minutes, and seconds
    hours = int(duration_seconds // 3600)
    minutes = int((duration_seconds % 3600) // 60)
    seconds = duration_seconds % 60
    
    logger.info(f"\n{'='*67}\n"
        "URBANOPT SIMULATION FINISHED\n"
        f"Completed after {hours} hours, {minutes} minutes, and {seconds:.2f} seconds.\n"
        f"{'='*67}")

############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    feature_file_zip = "powertwin-solver-pg/user_files/feature_files.zip"
    SIMULATION_DIR = "powertwin-solver-pg/user_files"
    LOCAL_DIR = ""
    initialize_uo(SIMULATION_DIR,LOCAL_DIR,feature_file_zip)
    