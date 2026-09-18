import os
import threading

# Thread-scoped per-simulation URBANOPT flag overrides. The async endpoint runs
# each sim on its own daemon thread, so storing the per-request flags in
# thread-local state (instead of the shared process os.environ) lets concurrent
# sims on one gunicorn worker run in parallel with correctly isolated flags.
# This replaces the old _SIM_ENV_LOCK, which serialized every sim precisely
# because the flags lived in the shared environment. Readers fall back to
# os.environ, so unset flags and non-sim/global contexts behave exactly as
# before (e.g. a deployment-wide URBANOPT_DYNAMIC_DEFAULTS still applies).
_local = threading.local()

_FLAG_ENV = (
    'URBANOPT_DYNAMIC_DEFAULTS',
    'URBANOPT_STOCHASTIC_SAMPLING',
    'URBANOPT_REPORTING_FREQUENCY',
    'URBANOPT_RESAMPLE',
    'POWERTWIN_KEEP_DIRS',
)


def set_sim_flags(dynamic_defaults=None, stochastic=None,
                  reporting_frequency=None, keep_dirs=None, resample=None):
    """Set per-simulation flag overrides for the CURRENT thread. None means 'no
    override' (readers fall back to os.environ). keep_dirs is a bool mapped to
    the POWERTWIN_KEEP_DIRS '1'/'' convention."""
    keep_val = None if keep_dirs is None else ('1' if keep_dirs else '')
    _local.flags = {
        'URBANOPT_DYNAMIC_DEFAULTS': dynamic_defaults,
        'URBANOPT_STOCHASTIC_SAMPLING': stochastic,
        'URBANOPT_REPORTING_FREQUENCY': reporting_frequency,
        'URBANOPT_RESAMPLE': resample,
        'POWERTWIN_KEEP_DIRS': keep_val,
    }


def clear_sim_flags():
    """Drop this thread's overrides (called on sim exit; the daemon thread ends
    anyway, this just keeps a reused thread clean)."""
    _local.flags = None


def get_sim_flag(name):
    """This thread's override for env var `name`, or None if unset."""
    flags = getattr(_local, 'flags', None)
    if not flags:
        return None
    return flags.get(name)


def sim_env(base=None):
    """A copy of `base` (default os.environ) with this thread's non-None flag
    overrides applied. Pass as subprocess env= so the uo CLI sees per-sim flags
    without mutating the shared process environment."""
    env = dict(os.environ if base is None else base)
    flags = getattr(_local, 'flags', None)
    if flags:
        for name in _FLAG_ENV:
            val = flags.get(name)
            if val is not None:
                env[name] = val
    return env
