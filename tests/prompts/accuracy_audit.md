# UBEM Accuracy Audit Prompt

Use this prompt when asking an AI to audit the PowerTwin solver pipeline for bugs that affect model accuracy.

---

## Prompt

You are auditing the PowerTwin UBEM solver pipeline for bugs that silently corrupt energy model accuracy. Your job is to find real, measurable errors — not theoretical ones.

### What the solver is for (read first — this defines severity)

PowerTwin resolves simulation defaults for **two equally first-class workflows**:

- **Residential** — Single-Family Detached, Single-Family Attached, Multifamily, via the HPXML / `BuildResidentialModel` path.
- **Commercial** — Office, Retail, Warehouse, Lodging, Education, etc., via the `create_typical` / BAR path.

The goal is correct defaults for **arbitrary building stock in any U.S. county**, residential and commercial. Severity is judged against that intended deployment surface — **not** against whatever buildings happen to be in the demo fixture.

**The ASU demo dataset (`solver/upload/demo_data/`) is one test fixture, and it is 100% commercial.** It is a convenient place to prove reachability and pull real numbers, but it is NOT the definition of "real impact." A bug that only the residential path hits is exactly as severe as one the commercial path hits — score it by its impact on the workflow it belongs to, using representative inputs for that workflow (real or synthetic). "The ASU set doesn't exercise this" is a **coverage note, never a severity downgrade.** Treating commercial-only demo data as ground truth has previously caused real residential bugs (heating-fuel resolution, multifamily occupants, electric-furnace efficiency, seasonal vacancy) to be mislabeled LATENT — do not repeat that.

### Required input metadata (missing fields = silent bias, not a crash)

Resolution quality is only as good as the per-building metadata, and missing fields fail **silently** — they fall back to flat national defaults, not errors. Score a missing-field finding by which tier it breaks. Critically: **a missing division key (`state`/`county_fips`) is HIGH/CRITICAL, not DEFENSIVE** — real footprint-sourced ingest (OpenStreetMap / Microsoft Building Footprints) routinely omits `state`, so this is the common production case, not an edge case. (A prior audit rated exactly this LOW and it turned out to be the single largest error in the Teton accuracy report.)

- **Tier 1 — required to simulate** (missing → building excluded + marked Failed): `id` (geojson / `asset_geometries_properties`), `area` (`asset_metadata`), and `latitude`+`longitude` (`asset_metadata`, for weather-station assignment).
- **Tier 2 — required for accurate, unbiased defaults** (missing → SILENT fallback to flat national averages):
  - `county_fips` **or** `state` (`asset_metadata`) — sets census division/region → the local fuel mix, envelope, and system-type marginals (RECS/CBECS/ACS). Missing **both** → `division = None` → the county-first heating reconciliation aborts to the flat `natural gas` default (~100% gas heating), discarding the correct ACS county marginal. `county_fips` is preferred (finer; unlocks the ACS county fuel table; `build_asset_ctx` derives `state` from its first two digits).
  - `asset_subtype_id` (CSV column) — building type/archetype; unknown → Single-Family Detached (by design, see 4b).
  - `year_built` (`asset_metadata`) — vintage; missing → all-vintage marginal (coarser, acceptable).
- **Tier 3 — derived, not required**: `climate_zone` (from the weather station), `floor_count` (defaults to 1), `number_of_units`/`bedrooms` (from area + subtype).

When auditing, inspect the shape of the *production* metadata, not just the fixture — `read_metadata` logs how many buildings lack a division key or vintage. A field that degrades to a flat default is a silent-bias risk weighted by how often it is actually absent in production, never by whether it crashes.

### Ground rules

**"No bugs found" is a valid and valuable result.** An audit that confirms stability is more useful than one that manufactures findings. Do not lower your severity threshold to avoid returning empty-handed. If you find nothing critical, say so clearly and explain what you checked.

**You have a known incentive problem.** When asked "find critical bugs," AI systems reliably find exactly one — every time, regardless of whether one exists. They do this by:
- Promoting latent/theoretical issues to "CRITICAL" (e.g., a code path that's correct but would only misfire on impossible/malformed input). Note the distinction: input that is *impossible* (a building with negative area) is theoretical; a *workflow the solver serves* (residential stock in a seasonal county) is real even if the demo fixture doesn't contain it — that is CRITICAL, not theoretical.
- Reporting mismatches that produce `0 * wrong_factor = 0` as data-corrupting bugs
- Flagging defensive code as missing when the dangerous input can't actually reach it
- Finding real code smells and inflating their accuracy impact

**Guard against this.** For every finding, ask yourself: "If I hadn't been asked to find bugs, would I flag this as critical?" If the answer is "probably not," downgrade it.

### Known false-positive patterns from prior audits

Previous audit rounds produced specific categories of false findings. **Do NOT repeat these patterns:**

1. **Fabricated data counts.** An agent claimed "490 residential buildings in ASU" when the ASU fixture has zero. **You must verify any count against the actual data you are citing before stating it** — read the CSV/GeoJSON/probe output and count; do not estimate or assume. (This is about not inventing numbers — it is NOT a reason to discount residential findings. If you assert "N buildings affected," show the command that produced N, whether the data is the ASU fixture or a synthetic residential sample you generated.)

2. **Ruby variable scoping misunderstanding.** In Ruby, when the parser sees `x = expr` inside a `begin...rescue` block, the variable `x` exists in the outer scope (as `nil` if the rescue fires). Two agents reported `climate_zone` and `year_built` as "undefined variable crashes" — both are safe because: (a) `generateFeatureFile.py` always sets `climate_zone` in the feature JSON, and (b) even if nil, downstream functions handle nil gracefully (`CZ_WINDOW[nil] || CZ_WINDOW['4']`, `vintage_bin(nil)` returns nil). **Do not report Ruby begin/rescue variable scoping as a bug.**

3. **Design choices promoted to bugs.** The scoring test filters `a[b] > 0` before computing RMSE. An agent called this CRITICAL because "RMSE doesn't need zero-protection." This is a documented, intentional design choice (docstring says so), applied symmetrically to both arms. Filtering zero-consumption hours from RMSE prevents trivial 0≈0 matches from diluting the signal. **A design choice you disagree with is not a bug.**

4. **Missing fallback ≠ bug.** The envelope resolver returns `(None, None)` when vintage is unknown, falling back to flat defaults. An agent called this CRITICAL. The flat defaults are intentionally chosen medians (wall_r=13 matches 2000-2009 West exactly). A system that falls back to a reasonable default is working as designed. **"Could be more precise" is not "silently corrupt."**

4b. **Most-common-archetype fallback is the design, not a bug.** This solver is built for NATIONAL-SCALE readiness: when a building's subtype/type is missing or unknown, it intentionally falls back to the most common national archetype — **Single-Family Detached** (the dominant U.S. building type). So an unknown/`NULL` `asset_subtype_id` resolving to SFD (`DEFAULT_SUBTYPE_ID`) is BY DESIGN — it maximizes national coverage rather than dropping the building. The fact that a specific fixture (e.g. ASU's commercial campus) carries `NULL` subtypes on some buildings is an UPSTREAM METADATA gap to fix at the source (assign real subtypes), NOT a resolver bug. **Do not report unknown-subtype → SFD (or any unknown → most-common-archetype) fallback as CRITICAL/HIGH.** Report it at most as a DEFENSIVE note about upstream metadata quality.

4c. **Biogenic wood CO2 excluded from the CO2 sensor is correct, not a gap.** The CO2 Emissions sensor (id 6) sums fossil + electricity emissions (Electricity, Natural_Gas, Propane, FuelOilNo2). **Wood/biomass is intentionally absent**: biogenic CO2 is treated as carbon-neutral and reported separately under GHG-Protocol / EPA convention, and `add_ems_emissions_reporting` does not emit a wood factor. Do NOT flag "CO2 omits wood/OtherFuels" as a bug. (District-heating CO2 is the genuinely-open question — but it depends on what the emissions measure emits, which is verifiable only against a real sim; see `tests/e2e/`.)

5. **Conflating code-path reachability with *workflow* reachability — in BOTH directions.** Two distinct mistakes:
   - **(a) Inflation:** claiming impact for a path that is genuinely unreachable in *any* supported workflow (e.g. a column mismatch where all values are structurally zero, or a branch behind an impossible condition). That is FALSE/LATENT.
   - **(b) Deflation (the one that bit us):** dismissing a real bug as zero-impact because *the ASU fixture* doesn't trigger it, when a **supported workflow does**. "No ASU building uses district heating / is residential / is in a seasonal county" means the demo set doesn't cover it — it does NOT mean zero impact. If the residential or commercial workflow reaches the path with representative inputs, the impact is real. **Trace the actual data the relevant workflow would produce (real fixture OR synthetic representative sample), not just whatever is in the demo set.**

### What counts as a confirmed accuracy bug

A finding is confirmed ONLY if ALL of these are true:

1. **Reachable in a supported workflow**: The code path executes during a normal residential OR commercial simulation run (not dead code, not behind an impossible condition). Show the call chain, and name which workflow(s) reach it.
2. **Wrong output**: The code produces a numerically incorrect value. State the expected value and actual value with real numbers, not "it could be wrong."
3. **Measurable on representative inputs**: The bug changes results for buildings the solver is intended to handle — demonstrated by execution against either the ASU fixture (commercial) OR a synthetic representative sample of the affected workflow (e.g. residential SFD/MF in the relevant county). Build the sample and run it; "would affect future data" hand-waving is not enough, but neither is "the ASU set doesn't contain it." If a real supported workflow produces the wrong number, it is measurable.
4. **Verified by execution**: Check actual output — runtime CSV/DB/log artifacts where they exist (`tests/runs/`, `powertwin_data/`), or a probe you ran against the resolver/feature-generation logic with representative inputs. Not source-reading alone. **Integration-layer claims** (EnergyPlus meter/column names, HPXML consumption, post-processing math) must be checked against a real sim artifact; if none exists, label the finding **UNVERIFIED** — never assert a column/meter name you did not observe.
5. **Counts verified**: If you cite a number of affected buildings, show the grep/count/probe that produced it — against whatever data you are citing (real or synthetic). Unverified counts are not acceptable.

### Severity scale

Severity is judged against the **intended deployment surface** (any residential or commercial building in any U.S. county), demonstrated on representative inputs — NOT against the commercial-only demo fixture. Which workflow a bug lives in does not change its severity; only its numeric impact does.

| Level | Criteria | Example |
|-------|----------|---------|
| **CRITICAL** | Changes scored kWh/kBtu/MT by ≥1% for ≥1 building in **any supported workflow**, shown by execution on real or representative-synthetic inputs | Electric furnaces modeled at 80% AFUE (+25% heating electricity) for residential stock; wrong unit conversion on a non-zero commercial column |
| **HIGH** | Changes results but <1%, OR affects pipeline reliability (crashes, silent skips, non-determinism) for either workflow | Building silently excluded due to ID mismatch; per-request env race across concurrent sims |
| **LATENT** | Real code bug with **provably zero impact in BOTH workflows** — values structurally always zero, or a path no supported workflow can reach. NOT "the demo set happens not to trigger it." | Column-name mismatch for a fuel type whose values are always zero by construction |
| **DEFENSIVE** | Missing validation/logging that would help catch future issues; no current wrong output | No hard distance cap on weather-station matching |
| **FALSE** | Not actually a bug after verification | Code that looks wrong but is correct by design |

When a bug is real but the **ASU fixture doesn't exercise it**, classify by true workflow impact (often CRITICAL/HIGH) and add a one-line coverage note ("not reached by the all-commercial ASU demo set; shown on synthetic residential sample"). Do not let fixture coverage lower the severity.

### Audit lenses

Reasoning aids, not a checklist — ask each question; don't hunt for a specific known bug.

- **Adjacency / symmetry:** for any change, what does it make newly reachable downstream, and what is the mirror case (handled the electric path → did you handle wood? the county path → the no-county path? captured one meter → the sibling meters)?
- **Composition vs marginal:** when the resolver composes reference tables, does the composed distribution reproduce the direct marginal table — at like-for-like granularity?
- **Magnitude honesty:** before citing a deviation, control for binning/granularity mismatches; separate genuine error from a finer-resolution artifact.
- **Cross-project contract:** is any new field/value/enum/id synced to every consumer (DB seed, frontend enum, sensor + units registry), or is the gap flagged?
- **Design intent first:** check a candidate against the documented design intents (e.g. most-common-archetype fallback, biogenic wood) before scoring — by-design is not a bug.

### Process

1. **Scope your search.** State which pipeline stages you're checking and which you're not.
2. **For each candidate finding**, trace it end-to-end:
   - Show the source code path and which workflow(s) (residential / commercial) reach it
   - Show the actual data that flows through it — from real fixtures where they exist, OR a synthetic representative sample you generate for the affected workflow (do not hand-wave with "hypothetical future data")
   - Compute the actual numeric impact (or prove it's zero in BOTH workflows)
   - **Verify your counts** — read the files / run the probe, don't estimate
3. **Exercise both workflows with real and synthetic inputs.**
   - **Commercial:** real URBANopt output exists on disk — use it. e.g. `powertwin_data/user_files/qa_matrix_commercial_flat/.../default_feature_report.csv` (raw hourly from a real EnergyPlus run) and `.../qa_matrix_commercial_resolver/...` (same building, dynamic defaults). Compare column names character-by-character against `sensor_types.csv`; trace `_match_column`; check non-zero values, not just column existence.
   - **Residential:** the ASU fixture has NO residential buildings, so you must generate representative residential inputs yourself (SFD/SFA/MF across divisions, vintages, and counties — including electric-heat and seasonal-vacancy counties) and run the resolver / feature-generation logic over them. A residential bug is fully confirmable this way even with zero residential rows in ASU.
   - If you claim a column mismatch, show both strings side-by-side. If you claim a scaling error, compute the actual numeric result from real or generated values.
4. **Classify honestly.** Use the severity scale above. Most findings in a reasonably maintained codebase will be LATENT or DEFENSIVE, not CRITICAL.
5. **Report FALSE findings too.** Showing what you investigated and ruled out builds more confidence than only showing what you "found."
6. **Summarize with counts by severity.** If the count of CRITICAL findings is 0, say so proudly — that's a healthy codebase signal.

### Output format

```
## Audit scope
[What you checked, what you didn't]

## Findings

### [SEVERITY] Short title
- File: path:line
- Workflow: [residential / commercial / both]
- Code path: how it's reached
- Actual data: [real values from runtime artifacts, or the synthetic representative sample you ran — show the probe]
- Numeric impact: [computed, not estimated]
- Fixture coverage: [does the ASU demo set exercise this? if not, say so — this is a note, not a severity input]
- Recommendation: [fix / monitor / accept]

## Investigated and rejected
| Candidate | Verdict | Why it's not a bug |
|-----------|---------|-------------------|

## Summary
- CRITICAL: N
- HIGH: N
- LATENT: N
- DEFENSIVE: N
- FALSE: N (investigated and rejected)

## Confidence statement
[How confident are you that no CRITICAL bugs remain in the areas you checked? What would increase your confidence?]
```

### Ground-truth ledger (check findings against this FIRST)

`tests/assumptions_ledger.yaml` is the single source of truth: every programmable field tied to its authoritative **source assumption** (RECS/CBECS/ACS/IECC/ASHRAE + extraction dates) and its **oracle status** (PROVEN / RESOLVER_PROVEN_INTEGRATION_PARTIAL / _UNVERIFIED / STATIC / PROPERTY). Before scoring a finding: (1) check the field's `source_assumption` and `design_notes` — if your "bug" contradicts a documented design choice or trust boundary, it's a FALSE positive; (2) the `coverage_summary.open_uncertainty` list and any `UNVERIFIED` rows ARE the known gap surface — a finding there is real but already-known, score it accordingly. The four `trust_boundaries` (EnergyPlus physics, survey representativeness, design choices, FP) are accepted, not bugs.

### Key files for reference

- Post-processing: `solver/app/modules/simulation/clean_report.py`
- Feature generation: `solver/app/modules/simulation/generateFeatureFile.py`
- Weather matching: `solver/app/modules/utils/weather.py`
- Sensor types: `solver/upload/sensor_types.csv`
- Dynamic defaults: `solver/app/modules/simulation/sim_params_spec.py`
- Ruby measures: `solver/upload/PowerTwin.rb`
- A/B scoring: `tests/tools/score_dynamic_defaults_ab.py`
- QA matrix: `tests/tools/qa_dynamic_defaults_matrix.py`
- Reference data: `solver/upload/reference_data/`
- Real sim outputs: `tests/runs/`
