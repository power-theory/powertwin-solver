"""Single source of truth for what must travel with the URBANopt mapper when it
is copied into a per-run mappers/ directory.

PowerTwin.rb `require_relative`s powertwin_refs.rb and PowerTwinRefs reads JSONs
from <mapper_dir>/reference_data/, so all three must be deployed together -- a
copied PowerTwin.rb without its siblings raises LoadError at sim time. Every
deploy site (run_UOsim, pernode, initialize_UOsim, the e2e test) calls this so
the invariant lives in one place.

Deploys SYNC on content, not presence: project dirs persist between runs (the
documented cleanup keeps mappers/ and measures/), so a skip-if-present copy
would pin a stale mapper or vendored measure forever -- the runtime-shadowing
failure the Dockerfile drift gate exists to prevent, one layer down. CALLERS
MUST NOT re-gate these on the destination's absence, or the sync never runs.

The sync is per FILE and atomic: each changed file is staged beside its target
(unique temp name) and os.replace'd onto it. Nothing is ever deleted first, so
concurrent readers -- joblib workers on one node, and multiple HPC nodes sharing
one mappers dir on /gscratch -- always see the whole old file or the whole new
one, never a missing tree. Files retired from the source are LEFT in place;
PowerTwinRefs loads reference_data by name, so an orphan is inert.
"""
import filecmp
import os
import shutil
import tempfile

# Files/dirs that must sit next to PowerTwin.rb in the mappers directory.
MAPPER_SIBLINGS = ("powertwin_refs.rb", "reference_data")


def _sync_file(src, dst):
    if os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False):
        return
    d = os.path.dirname(dst) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".sync-")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        shutil.copymode(src, tmp)
        os.replace(tmp, dst)  # atomic: readers see old or new, never partial
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _sync(src, dst):
    """Copy src onto dst when absent or different; no-op when already identical."""
    if os.path.isfile(src):
        _sync_file(src, dst)
        return
    for root, _dirs, files in os.walk(src):
        target_root = os.path.join(dst, os.path.relpath(root, src))
        for fn in files:
            _sync_file(os.path.join(root, fn), os.path.join(target_root, fn))


def deploy_mapper(upload_dir, mappers_dir):
    """Sync PowerTwin.rb and its required siblings from upload_dir into mappers_dir."""
    os.makedirs(mappers_dir, exist_ok=True)
    _sync(os.path.join(upload_dir, "PowerTwin.rb"), os.path.join(mappers_dir, "PowerTwin.rb"))
    for sib in MAPPER_SIBLINGS:
        src = os.path.join(upload_dir, sib)
        if os.path.exists(src):
            _sync(src, os.path.join(mappers_dir, sib))


def deploy_measures(upload_dir, project_dir):
    """Stage the custom OpenStudio measures (upload/measures/*) under
    <project>/measures/ so the OSW resolves them by measure_dir_name. base_workflow
    .osw references set_window_construction / set_service_water_heating_fuel /
    set_heating_fuel / set_people_per_floor_area, which are NOT in the urbanopt
    gem set -- without this the workflow fails Initialization with 'Cannot find
    measure'. Also carries the VENDORED default_feature_reports that shadows the
    reporting gem via measure_paths precedence. Copies every subdir (incl. shared
    helpers like _tools that measures require_relative)."""
    src_measures = os.path.join(upload_dir, "measures")
    if not os.path.isdir(src_measures):
        return
    dst_measures = os.path.join(project_dir, "measures")
    os.makedirs(dst_measures, exist_ok=True)
    for m in os.listdir(src_measures):
        src = os.path.join(src_measures, m)
        if os.path.isdir(src):
            _sync(src, os.path.join(dst_measures, m))
