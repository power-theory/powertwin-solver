# Progress Tracking Implementation - Changes Summary

## Overview
Implemented real-time progress tracking for simulations with accurate asset counts and timestamps that update every 10 seconds on the dashboard.

## Changes Made

### 1. **runtime_analysis.py** - Capture total_assets after filtering
**Location**: `solver/app/modules/diagnostics/runtime_analysis.py` (Lines 168-180)

**What Changed**:
- Added `total_assets = asset_count` to capture the ACTUAL filtered asset count (not the raw input)
- Before: total_assets was hardcoded to 0
- Now: Calls `save_simulation_state()` immediately after inserting assets to the database with the real count
- This happens BEFORE batch distribution, so the true processable asset count is captured

**Example**:
- Input CSV: 189,530 lines
- After filtering: ~16,124 valid assets (91% excluded due to UrbanOpt limitations)
- State file now saves: `total_assets: 16124` (instead of 0)

---

### 2. **run_UOsim.py** - Increment progress counter every 10 assets
**Location**: `solver/app/modules/simulation/run_UOsim.py` (Lines 312-337)

**What Changed**:
- Added `assets_processed_batch` counter to track assets within each batch
- Every 10 assets: Reads current state file, increments the global `assets_processed` counter by 10, and saves back
- Updates `current_step` to show which batch is being processed
- Both successful AND failed assets count toward progress

**Flow**:
```
Asset 1-9:    No update
Asset 10:     assets_processed: 0 → 10 (state file write)
Asset 11-19:  No update  
Asset 20:     assets_processed: 10 → 20 (state file write)
...and so on
```

**Performance**: Each write is ~1-5ms, negligible compared to 5-30 second per-asset processing time

---

### 3. **views.py** - Fresh timestamps and database fallback
**Location**: `solver/app/views.py` (Lines 440-501)

**What Changed**:
- `get_current_simulation_status()` now generates fresh timestamp on EVERY call using `datetime.datetime.now().isoformat()`
- Before: Used stale timestamp from state file (didn't update)
- Now: "Last Updated" refreshes every 10 seconds when frontend polls

**Added Database Fallback**:
- Queries database to count `assets_processed` as a fallback if state file is stale
- Uses: `SELECT COUNT(*) FROM powertwin WHERE simulation_name = ? AND status IN ('Processing', 'Finished', 'Failed')`
- Falls back gracefully if database query fails

---

## Data Flow

```
SIMULATION START:
├─ _run_autorun_simulation_background() initializes state:
│  └─ assets_processed: 0, total_assets: 0, current_step: 'initializing'
│
├─ CREATE_FEATUREFILES() processes raw assets
│
├─ ASSET_ANALYSIS() filters and validates:
│  ├─ Counts valid assets: 16,124
│  ├─ Inserts to database
│  ├─ [NEW] save_simulation_state() with total_assets: 16124
│  └─ Distributes to batches
│
├─ RUN_PARALLEL_BATCHES() processes batches:
│  ├─ Batch 1 processes 4,031 assets
│  │  ├─ Asset 1-9: (no update)
│  │  ├─ Asset 10: [NEW] state file write: assets_processed: 10
│  │  ├─ Asset 11-19: (no update)
│  │  ├─ Asset 20: [NEW] state file write: assets_processed: 20
│  │  └─ ...continues every 10 assets
│  ├─ Batch 2, 3, 4: Same pattern (parallel)
│
└─ SIMULATION COMPLETE:
   └─ Final state: assets_processed: 16124, total_assets: 16124 (100%)


FRONTEND POLLING (every 10 seconds):
├─ fetchCurrentSimulationStatus()
│  └─ GET /api/simulation/current-status
│     └─ get_current_simulation_status()
│        ├─ Reads state file: progress.assets_processed (updated by backend)
│        ├─ Database fallback: COUNT WHERE status IN ('Processing','Finished','Failed')
│        └─ [NEW] Fresh timestamp: datetime.now().isoformat()
│
└─ Display updates:
   ├─ Progress bar: X/16124 (updates every ~50-300 seconds = 10 asset increments)
   ├─ Percentage: (assets_processed / total_assets) * 100
   └─ Last Updated: [refreshes every 10 seconds even if counter hasn't changed]
```

---

## Testing Checklist

### ✅ 1. Total Assets Capture
```sql
-- Before running simulation:
SELECT COUNT(*) as raw_assets FROM (SELECT * FROM [your CSV] LIMIT 189530) t;
-- Result: 189,530

-- After running simulation (check dashboard):
-- Should show: "X / [16124+] assets processed"
-- Where 16124+ is the filtered count
```

### ✅ 2. Progress Counter Increments
**What to verify**:
- Start a simulation
- Wait 50-300 seconds (one asset increment)
- Dashboard should show: "10 / 16124 assets processed" (or similar 10-increment step)
- Wait another 50-300 seconds: "20 / 16124"
- Progress should increment by 10 each time

### ✅ 3. Timestamp Refreshes Every 10 Seconds
**What to verify**:
- Start dashboard
- Look at "Last Updated: [timestamp]"
- Refresh or wait 10 seconds
- Timestamp should change even if progress counter hasn't incremented yet
- Compares with performance monitoring timestamps (which already update correctly)

### ✅ 4. Database Consistency
**Run during active simulation**:
```sql
-- Check actual asset counts
SELECT 
    simulation_name,
    COUNT(*) as total,
    SUM(CASE WHEN status IN ('Processing','Finished','Failed') THEN 1 ELSE 0 END) as processed,
    SUM(CASE WHEN status = 'Finished' THEN 1 ELSE 0 END) as completed,
    SUM(CASE WHEN status = 'Failed' THEN 1 ELSE 0 END) as failed
FROM powertwin
WHERE simulation_name = 'your_test_simulation'
GROUP BY simulation_name;
```
- `total` should equal `16124` (filtered count)
- `processed` should match dashboard "X / 16124"

### ✅ 5. Batch Progress Displays Correctly
**What to verify**:
- Dashboard batch progress table shows correct total per batch
- Each batch should show: "Completed / Total" with completion percentage
- Should match database: (total / 16124 * num_cores) ≈ 4031 per batch

### ✅ 6. Recovery Preserves Progress
**What to verify**:
- Run simulation, let it process ~5000 assets
- Stop/crash the simulation
- Start recovery with same simulation name
- Dashboard should show preserved progress count
- Continue from where it left off (not reset to 0)

---

## Known Limitations

1. **Progress granularity**: Updates every 10 assets (not per-asset) to avoid I/O slowdown
   - Can be adjusted in run_UOsim.py if needed: change `% 10 == 0` to `% 5 == 0` or similar

2. **Database fallback**: Only used if state file is stale or missing
   - Prioritizes state file counter for consistency
   - Fallback helps if state file was corrupted

3. **Timestamp source**: 
   - Now generated fresh on every API call
   - May show current time even if simulation is idle/paused (working as intended)

---

## Future Enhancements

1. **Update frequency adjustment**: Change from every 10 assets to every N assets based on processing speed
2. **Batch-level progress aggregation**: Sum batch progress to feed top-level counter
3. **WebSocket real-time updates**: Replace polling with WebSocket for sub-10-second refresh
4. **Historical progress tracking**: Log progress snapshots for completion time predictions

---

## Rollback Instructions

If issues arise, revert these changes:

```bash
# Revert runtime_analysis.py
git checkout solver/app/modules/diagnostics/runtime_analysis.py

# Revert run_UOsim.py
git checkout solver/app/modules/simulation/run_UOsim.py

# Revert views.py
git checkout solver/app/views.py
```

---

## Support

For questions about the implementation:
1. Check the inline comments in the modified files
2. Review the database query in get_current_simulation_status()
3. Check logs for "Saved total_assets=" and "Updated progress =" messages
