"""Single source of truth for what must travel with the URBANopt mapper when it
is copied into a per-run mappers/ directory.

PowerTwin.rb `require_relative`s powertwin_refs.rb and PowerTwinRefs reads JSONs
from <mapper_dir>/reference_data/, so all three must be deployed together -- a
copied PowerTwin.rb without its siblings raises LoadError at sim time. Every
deploy site (run_UOsim, pernode, initialize_UOsim, the e2e test) calls this so
the invariant lives in one place.
"""
import os
import shutil

# Files/dirs that must sit next to PowerTwin.rb in the mappers directory.
MAPPER_SIBLINGS = ("powertwin_refs.rb", "reference_data")


def deploy_mapper(upload_dir, mappers_dir):
    """Copy PowerTwin.rb and its required siblings from upload_dir into
    mappers_dir. Idempotent: skips anything already present."""
    os.makedirs(mappers_dir, exist_ok=True)
    mapper_dst = os.path.join(mappers_dir, "PowerTwin.rb")
    if not os.path.exists(mapper_dst):
        shutil.copy(os.path.join(upload_dir, "PowerTwin.rb"), mapper_dst)
    for sib in MAPPER_SIBLINGS:
        src = os.path.join(upload_dir, sib)
        dst = os.path.join(mappers_dir, sib)
        if os.path.exists(dst) or not os.path.exists(src):
            continue
        (shutil.copytree if os.path.isdir(src) else shutil.copy)(src, dst)


def deploy_measures(upload_dir, project_dir):
    """Stage the custom OpenStudio measures (upload/measures/*) under
    <project>/measures/ so the OSW resolves them by measure_dir_name. base_workflow
    .osw references set_window_construction / set_service_water_heating_fuel /
    set_heating_fuel / set_people_per_floor_area, which are NOT in the urbanopt
    gem set -- without this the workflow fails Initialization with 'Cannot find
    measure'. Idempotent. Copies every subdir (incl. shared helpers like _tools
    that measures require_relative), matching the original production behavior."""
    src_measures = os.path.join(upload_dir, "measures")
    if not os.path.isdir(src_measures):
        return
    dst_measures = os.path.join(project_dir, "measures")
    os.makedirs(dst_measures, exist_ok=True)
    for m in os.listdir(src_measures):
        src = os.path.join(src_measures, m)
        dst = os.path.join(dst_measures, m)
        if not os.path.isdir(src) or os.path.isdir(dst):
            continue
        shutil.copytree(src, dst)
