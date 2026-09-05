# County reference tables (*.json.gz)

Sixteen builder-generated tables (CR campaign) are GITIGNORED: they are binary,
~9 MB combined, and fully reproducible. Regenerate from the repo root with the
matching `tests/tools/build_county_*.py` script (each needs the local ResStock /
ComStock parquets under /mnt/h/data/ and stamps `_source` provenance).

They live HERE, not under tests/, because `sim_params_spec._load_ref` and the
docker image COPY read this directory; a fresh clone must run the builders (or
copy the files from a built image) BEFORE building an image, or the resolver
silently degrades to division/RECS fallbacks for every county field.
