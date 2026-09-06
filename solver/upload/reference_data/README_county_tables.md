# County reference tables (*.json.gz)

Sixteen builder-generated tables (CR campaign) are GITIGNORED: they are binary,
~11 MB combined, and fully reproducible. Two tracked pin files sit beside them:
`county_tables.sha256` (sha256sum format) and `county_tables.version` (the
GitLab generic-package version they were published under, package `county-tables`).

Fresh checkout (and CI): `python tests/tools/county_tables_registry.py fetch`
downloads every table that is missing or mismatching from this project's package
registry and verifies it against the pins. `... verify` (or, from any shell,
`cd solver/upload/reference_data && sha256sum -c county_tables.sha256`) proves the
directory is complete; build_flask in CI runs exactly that before `docker build`.

Regeneration: run the matching `tests/tools/build_county_*.py` from the repo root
(each needs the local ResStock / ComStock parquets under /mnt/h/data/ and stamps
`_source` provenance), then `python tests/tools/county_tables_registry.py publish`
uploads the new set under a content-derived version and rewrites the two pin files;
commit the pins with the change so CI and other clones pick up the same tables.

They live HERE, not under tests/, because `sim_params_spec._load_ref` and the
docker image COPY read this directory; the resolver's import-time guard logs an
error when any of the sixteen is absent, because it would otherwise silently
degrade to division/RECS fallbacks for every county field.
