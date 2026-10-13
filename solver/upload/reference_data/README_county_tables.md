# County reference tables (*.json.gz)

Builder-generated tables (CR campaign) read by `sim_params_spec._load_ref`. They are binary and fully reproducible,
and since 2026-09-30 they are **Git LFS objects** (`.gitattributes`), like `solver/upload/demo_data`. Before that
they were gitignored and fetched from the GitLab package registry (`county-tables`, last version
sha-25908614c7dbec0f), which is retired.

`county_tables.sha256` (sha256sum format, tracked as plain text) pins each table. It is checked in three places, so
a missing table or an unhydrated LFS pointer fails loudly:
- the CI test job, before the tests;
- the CI build_flask job, before `docker build`;
- the `solver/Dockerfile` itself, right after `COPY . .`, so a local or `git archive` build cannot ship an image
  without the tables. Without them the resolver silently degrades to division/RECS fallbacks for every county field.

Fresh checkout: `git lfs pull`, then `cd solver/upload/reference_data && sha256sum -c county_tables.sha256`.

Regeneration: run the matching `tests/tools/build_county_*.py` from the repo root (each needs the local ResStock /
ComStock parquets and stamps `_source` provenance), rewrite the pins with
`cd solver/upload/reference_data && sha256sum *.json.gz comstock2024_state_code_adoption.json comstock_lighting_lpd.json insulation_presets.json > county_tables.sha256` (the three plain-JSON resolver 3.3 tables are pinned too, since 2026-10-03), and commit the tables and the pins
together. A new table also goes in the resolver's required list (`sim_params_spec`, the import-time guard).

Since resolver 3.4 (2026-10-11, County Prior Pooling Plan) the builders pool every table: each area's cell is blended
toward its parent area's (state toward division, PUMA toward state, county toward its PUMA or its state) with a strength
k estimated per table, tier and part from the source data, and a part with no signal beyond sampling noise ships no
cells at that tier (the resolver walks to the parent). Each table's `_pooling` block records rho, the shuffled-label null, k and the cells
used; a state tier (`_states`, `state_ref`) sits between PUMA and division. Method, citations and tests:
`tests/assumptions_ledger.yaml` (COUNTY_POOLING) and `tests/tools/build_county_priors.py`.

They live HERE, not under tests/, because `_load_ref` and the docker image COPY read this directory.
