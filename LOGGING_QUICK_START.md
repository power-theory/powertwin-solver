# Quick Start: Using Modern Logging and Status System

## For Developers

### 1. Get Module Instances

```python
# Logging
from modules.utils import initialize_logger
logger = initialize_logger('MyModule')

# Status tracking
from modules.diagnostics.status_tracker import get_tracker
tracker = get_tracker()

# Performance monitoring
from modules.diagnostics.performance_monitor import get_monitor
monitor = get_monitor()
```

### 2. Update Simulation Status

```python
from modules.diagnostics.status_tracker import set_status, batch_update_statuses

# Single update (immediate cache)
set_status('sim_1', {'status': 'running', 'progress': 45}, asset_id=101)

# Batch update (efficient for many changes)
set_status('sim_1', {'status': 'running'}, asset_id=101, batch=True)
set_status('sim_1', {'status': 'running'}, asset_id=102, batch=True)
set_status('sim_1', {'status': 'running'}, asset_id=103, batch=True)
# Auto-flushes after 5 seconds or 500 updates
```

### 3. Monitor Database Performance

```python
from modules.diagnostics.performance_monitor import record_query_metric
import time

start = time.time()
# ... execute query ...
duration_ms = (time.time() - start) * 1000
record_query_metric('SELECT', duration_ms, success=True)
```

### 4. Batch Database Updates

```python
from modules.diagnostics.db_optimizer import add_batched_update

# Add updates to queue (auto-flushes when batch fills or interval passes)
add_batched_update('powertwin',
    'UPDATE powertwin SET status = %s WHERE asset_id = %s',
    ('completed', asset_id))
```

---

## For Operations/Monitoring

### 1. Check Current System Health

```bash
# Get all health metrics
curl http://localhost:8080/api/monitoring/performance

# Check specific component
curl http://localhost:8080/api/monitoring/system-health

# Get recent alerts
curl http://localhost:8080/api/monitoring/alerts?since_minutes=60

# Full diagnostics
curl http://localhost:8080/api/diagnostics/full-report
```

### 2. View Logs Efficiently

```bash
# Get last 100 log lines
curl http://localhost:8080/api/logs/tail?lines=100

# Get errors only
curl http://localhost:8080/api/logs/paginated?level=ERROR

# Search logs
curl http://localhost:8080/api/logs/paginated?search=database

# Get logs from 09:00-11:00
curl http://localhost:8080/api/logs/time-range?start=2026-01-12T09:00:00&end=2026-01-12T11:00:00

# Log file statistics
curl http://localhost:8080/api/logs/stats
```

### 3. Monitor Simulation Progress

```bash
# Get status summary (efficient, avoids multiple DB queries)
curl http://localhost:8080/api/simulation/status-summary/simulation_1

# Returns:
# {
#   "simulation_name": "simulation_1",
#   "total_assets": 500,
#   "completed": 250,
#   "in_progress": 150,
#   "failed": 10,
#   "completion_percentage": 50.0,
#   "success_rate": 98.0
# }
```

### 4. Check Optimization Stats

```bash
# Database batch manager stats
curl http://localhost:8080/api/monitoring/db-optimization

# Returns:
# {
#   "optimization": {
#     "batch_manager": {
#       "total_updates_flushed": 10000,
#       "total_batches": 25,
#       "avg_batch_size": 400,
#       "pending_updates": 127,
#       "tables_with_pending": 1
#     },
#     "query_cache": {
#       "cache_size": 45,
#       "ttl_seconds": 300
#     }
#   }
# }
```

### 5. Cache Performance

```bash
# Status tracker cache stats
curl http://localhost:8080/api/tracker/stats

# Returns:
# {
#   "cache_size": 450,
#   "batch_queue_size": 0,
#   "cache_hits": 4200,
#   "cache_misses": 600,
#   "hit_rate_percent": 87.5,
#   "avg_batch_size": 125,
#   "total_flushes": 42,
#   "db_queries_avoided": 4200
# }
```

---

## Common Workflows

### Monitor Long-Running Simulation

```bash
#!/bin/bash

SIMULATION="my_big_sim"
INTERVAL=30  # seconds

while true; do
    echo "=== Status at $(date) ==="
    
    # Check progress
    curl -s http://localhost:8080/api/simulation/status-summary/$SIMULATION \
        | jq '.completion_percentage, .success_rate'
    
    # Check for errors
    curl -s http://localhost:8080/api/logs/paginated?level=ERROR \
        | jq '.lines | length'
    
    # Check system health
    curl -s http://localhost:8080/api/monitoring/system-health \
        | jq '.cpu.percent, .memory.percent'
    
    sleep $INTERVAL
done
```

### Automated Health Check

```bash
#!/bin/bash

# Get full diagnostics
curl -s http://localhost:8080/api/diagnostics/full-report > diagnostics.json

# Extract key metrics
jq '.performance.database.avg_query_time_ms' diagnostics.json
jq '.system_health.cpu.percent' diagnostics.json
jq '.alerts | length' diagnostics.json

# Alert if issues found
ALERTS=$(jq '.alerts | length' diagnostics.json)
if [ $ALERTS -gt 10 ]; then
    echo "WARNING: $ALERTS alerts detected"
    # Send notification, etc.
fi
```

### Debug Slow Simulation

```bash
# 1. Check system resources
curl -s http://localhost:8080/api/monitoring/system-health | jq .

# 2. Look at slow queries
curl -s http://localhost:8080/api/monitoring/performance | jq '.database'

# 3. Check recent errors
curl -s http://localhost:8080/api/logs/paginated?level=ERROR | jq '.lines[-10:]'

# 4. Get performance report
curl -s http://localhost:8080/api/diagnostics/full-report | jq '.performance'
```

---

## Log File Locations

- **All messages**: `logs/dev_logs.txt` (rotated at 10MB, max 100MB)
- **User messages**: `logs/user_logs.txt` (INFO and above only)
- **Structured JSON**: `logs/structured_logs.jsonl` (machine-readable)

---

## Troubleshooting

### High Database Query Time
```bash
# Check batch stats
curl http://localhost:8080/api/monitoring/db-optimization | jq '.optimization.batch_manager'

# If avg_query_time_ms > 1000ms, increase batch size or reduce load
```

### Large Log Files
```bash
# Check log stats
curl http://localhost:8080/api/logs/stats | jq '.file_size_mb'

# Logs auto-rotate at 10MB, but if size keeps growing:
# 1. Check error frequency: curl .../api/logs/paginated?level=ERROR
# 2. Investigate errors and fix root cause
```

### Poor Cache Hit Rate
```bash
# Check cache stats
curl http://localhost:8080/api/tracker/stats | jq '.hit_rate_percent'

# If < 70%, increase cache TTL in status_tracker.py
# cache_ttl_seconds=30 -> cache_ttl_seconds=60
```

### System Resource Issues
```bash
# Get detailed system health
curl http://localhost:8080/api/monitoring/system-health | jq .

# For CPU/memory issues:
# 1. Reduce number of concurrent simulations
# 2. Increase page size in log pagination
# 3. Increase batch update sizes
```

---

## Configuration Tips

### For Small Deployments (< 100 concurrent assets)
```python
cache_ttl_seconds = 60          # Longer cache TTL
batch_flush_interval = 10       # Less frequent flushes
max_batch_size = 1000           # Larger batches
```

### For Large Deployments (> 1000 concurrent assets)
```python
cache_ttl_seconds = 15          # Shorter cache TTL
batch_flush_interval = 2        # Frequent flushes
max_batch_size = 250            # Smaller batches
page_size = 500                 # Smaller log pages
```

### For Long-Running Simulations
```python
log_file_max_bytes = 50 * 1024 * 1024  # 50MB files
log_backup_count = 20                    # Keep more history
cache_ttl_seconds = 300                  # Long cache TTL
```
