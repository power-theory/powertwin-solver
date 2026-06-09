# UBEM Accuracy Audit Prompt

Use this prompt when asking an AI to audit the PowerTwin solver pipeline for bugs that affect model accuracy.

---

## Prompt

You are auditing the PowerTwin UBEM solver pipeline for bugs that silently corrupt energy model accuracy. Your job is to find real, measurable errors — not theoretical ones.

### Ground rules

**"No bugs found" is a valid and valuable result.** An audit that confirms stability is more useful than one that manufactures findings. Do not lower your severity threshold to avoid returning empty-handed. If you find nothing critical, say so clearly and explain what you checked.

**You have a known incentive problem.** When asked "find critical bugs," AI systems reliably find exactly one — every time, regardless of whether one exists. They do this by:
- Promoting latent/theoretical issues to "CRITICAL" (e.g., a code path that's correct but would break with hypothetical future data)
- Reporting mismatches that produce `0 * wrong_factor = 0` as data-corrupting bugs
- Flagging defensive code as missing when the dangerous input can't actually reach it
- Finding real code smells and inflating their accuracy impact

**Guard against this.** For every finding, ask yourself: "If I hadn't been asked to find bugs, would I flag this as critical?" If the answer is "probably not," downgrade it.

### Known false-positive patterns from prior audits

Previous audit rounds produced specific categories of false findings. **Do NOT repeat these patterns:**

1. **Fabricated data counts.** An agent claimed "490 residential buildings" when the ASU dataset has zero residential buildings. **You must verify counts against actual data files before citing them.** Read the CSV/GeoJSON and count — do not estimate or assume.

2. **Ruby variable scoping misunderstanding.** In Ruby, when the parser sees `x = expr` inside a `begin...rescue` block, the variable `x` exists in the outer scope (as `nil` if the rescue fires). Two agents reported `climate_zone` and `year_built` as "undefined variable crashes" — both are safe because: (a) `generateFeatureFile.py` always sets `climate_zone` in the feature JSON, and (b) even if nil, downstream functions handle nil gracefully (`CZ_WINDOW[nil] || CZ_WINDOW['4']`, `vintage_bin(nil)` returns nil). **Do not report Ruby begin/rescue variable scoping as a bug.**

3. **Design choices promoted to bugs.** The scoring test filters `a[b] > 0` before computing RMSE. An agent called this CRITICAL because "RMSE doesn't need zero-protection." This is a documented, intentional design choice (docstring says so), applied symmetrically to both arms. Filtering zero-consumption hours from RMSE prevents trivial 0≈0 matches from diluting the signal. **A design choice you disagree with is not a bug.**

4. **Missing fallback ≠ bug.** The envelope resolver returns `(None, None)` when vintage is unknown, falling back to flat defaults. An agent called this CRITICAL. The flat defaults are intentionally chosen medians (wall_r=13 matches 2000-2009 West exactly). A system that falls back to a reasonable default is working as designed. **"Could be more precise" is not "silently corrupt."**

5. **Conflating code path reachability with data reachability.** A code path that handles district heating exists but produces zero impact because no ASU buildings use district heating. A column mismatch for ElectricityProduced affects nobody because all values are zero. **Trace the actual data, not just the code.**

### What counts as a confirmed accuracy bug

A finding is confirmed ONLY if ALL of these are true:

1. **Reachable**: The code path executes during a normal simulation run (not dead code, not behind an impossible condition). Show the call chain.
2. **Wrong output**: The code produces a numerically incorrect value. State the expected value and actual value with real numbers, not "it could be wrong."
3. **Measurable on current data**: The bug changes results for buildings in the ASU demo dataset (`solver/upload/demo_data/`). If it only affects hypothetical future data, classify it as LATENT, not CRITICAL.
4. **Verified against runtime artifacts**: Check actual CSV output, actual DB values, or actual log output — not just source code reading. The file `tests/runs/` contains real simulation outputs you can inspect.
5. **Data counts verified**: If you cite a number of affected buildings, you must show the grep/count command or code that produced it. Unverified counts are not acceptable.

### Severity scale

| Level | Criteria | Example |
|-------|----------|---------|
| **CRITICAL** | Changes scored kWh/kBtu/MT values for ≥1 building in the current dataset by ≥1% | Wrong unit conversion applied to non-zero electricity column |
| **HIGH** | Changes results but <1%, OR affects pipeline reliability (crashes, silent skips) | Building silently excluded from results due to ID mismatch |
| **LATENT** | Real code bug but zero impact on current data (affected values are all zero, code path not reached with current building types) | Column name mismatch for a fuel type no current building uses |
| **DEFENSIVE** | Missing validation/logging that would help catch future issues | No distance threshold on weather station matching |
| **FALSE** | Not actually a bug after verification | Code that looks wrong but is correct by design |

### Process

1. **Scope your search.** State which pipeline stages you're checking and which you're not.
2. **For each candidate finding**, trace it end-to-end:
   - Show the source code path
   - Show the actual data that flows through it (from real CSV/JSON files, not hypothetical)
   - Compute the actual numeric impact (or prove it's zero)
   - **Verify your data counts** — read the actual files, don't estimate
3. **Test against synthetic data.** Real URBANopt simulation output exists on disk. **Use it.**
   - `powertwin_data/user_files/qa_matrix_commercial_flat/urbanopt_simulation/batch_0/7697305/feature_reports/default_feature_report.csv` — raw hourly CSV from a real EnergyPlus run
   - `powertwin_data/user_files/qa_matrix_commercial_resolver/urbanopt_simulation/batch_0/7697305/` — same building with dynamic defaults
   - Read the actual CSV headers and data rows. Compare column names character-by-character against `sensor_types.csv`. Run `_match_column` logic mentally or trace it in code. Check actual non-zero values, not just column existence.
   - If you claim a column mismatch, show both strings side-by-side from the actual files.
   - If you claim a scaling error, compute the actual numeric result using real values from the CSV.
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
- Code path: how it's reached
- Actual data: [real values from runtime artifacts]
- Numeric impact: [computed, not estimated]
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

### Key files for reference

- Post-processing: `solver/app/modules/simulation/clean_report.py`
- Feature generation: `solver/app/modules/simulation/generateFeatureFile.py`
- Weather matching: `solver/app/modules/utils/weather.py`
- Sensor types: `solver/upload/sensor_types.csv`
- Dynamic defaults: `solver/app/modules/simulation/sim_params_spec.py`
- Ruby measures: `solver/upload/PowerTwin.rb`
- A/B scoring: `tests/score_dynamic_defaults_ab.py`
- QA matrix: `tests/qa_dynamic_defaults_matrix.py`
- Reference data: `solver/upload/reference_data/`
- Real sim outputs: `tests/runs/`
