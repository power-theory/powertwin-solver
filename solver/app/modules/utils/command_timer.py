import os
import signal
import subprocess
import time

from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger("Run Command", external_log_dir)

DEFAULT_UO_TIMEOUT_S = 129600


def uo_timeout(cap=None):
    """Seconds allowed for a uo subprocess, from URBANOPT_RUN_TIMEOUT_S. Empty, malformed, and
    non-positive values fall back to the default rather than failing every asset."""
    try:
        v = int(os.environ.get("URBANOPT_RUN_TIMEOUT_S", ""))
    except (TypeError, ValueError):
        v = DEFAULT_UO_TIMEOUT_S
    if v <= 0:
        v = DEFAULT_UO_TIMEOUT_S
    return min(v, cap) if cap else v


############################################################################################################
# Name: run_command(command, timeout=None)
# Description: This function runs a command in the shell and returns the time it takes to execute
#   the command. With `timeout` (seconds) the command is killed when it exceeds it, including every
#   descendant: the command runs in its own session so killpg reaps the whole tree (a plain
#   subprocess timeout kills only the direct shell and orphans the grandchildren).
############################################################################################################
def run_command(command, timeout=None):
    start_time = time.time()
    # Pass per-sim thread-local flags explicitly (URBANOPT_* / POWERTWIN_KEEP_DIRS)
    # instead of relying on inherited process env, so concurrent sims each hand the
    # uo subprocess their own flags without a shared-os.environ race.
    from modules.utils.sim_flags import sim_env
    proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=sim_env(), start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except BaseException as exc:
        # any exit path (timeout, Ctrl+C, decode error) must reap the detached tree
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except Exception:
            stdout, stderr = None, None
        if isinstance(exc, subprocess.TimeoutExpired):
            logger.error(f"Command '{command}' timed out after {timeout}s and its process group was killed.")
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
        raise
    end_time = time.time()
    if proc.returncode != 0:
        logger.error(f"Command '{command}' failed with error: {stderr}")
        raise subprocess.CalledProcessError(proc.returncode, command, stdout, stderr)
    logger.info(f"Command '{command}' executed successfully.")
    logger.info(f"Output: {stdout}")
    return end_time - start_time
