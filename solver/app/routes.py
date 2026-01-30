from flask import Blueprint
from .views import *

solver_bp = Blueprint('Solver', __name__)

routes = [
    ('/', home, ['GET']),
    ('/api/simulation/start', start_simulation, ['POST']),
    ('/api/simulation/autorun_simulation', autorun_simulation, ['POST']),
    ('/api/simulation/stop', stop_simulation, ['POST']),
    ('/api/simulation/status/<simulation_name>', get_simulation_status, ['GET']),
    ('/api/simulation/current-status', get_current_simulation_status, ['GET']),
    ('/api/simulation/batch-progress', get_batch_progress, ['GET']),
    ('/api/simulation/delete/<simulation_name>', delete_simulation, ['DELETE']),
    ('/api/simulation/<simulation_name>/assets', get_simulation_assets, ['GET']),
    ('/api/asset/config/<simulation_name>/<asset_id>', get_asset_config, ['GET']),
    ('/api/simulation/data',get_simulation_data, ['GET']),
    ('/api/simulation/asset_update', process_asset_update, ['POST']),
    ('/api/diagnostics/recovery', recovery, ['POST']),
    ('/api/diagnostics/update_asset', update_asset, ['POST']),
    ('/logs', get_logs, ['GET']),
    ('/api/diagnostics/log', log_message, ['POST']),
    # Modern streaming and status endpoints
    ('/api/logs/paginated', get_logs_paginated, ['GET']),
    ('/api/logs/tail', get_logs_tail, ['GET']),
    ('/api/logs/time-range', get_logs_by_time, ['GET']),
    ('/api/logs/stats', get_log_stats, ['GET']),
    ('/api/logs/get-current', get_current_logs, ['GET']),
    ('/api/logs/refresh', refresh_logs, ['POST']),
    ('/api/logs/available-batches', get_available_batch_logs, ['GET']),
    ('/api/simulation/status-summary/<simulation_name>', get_simulation_status_summary, ['GET']),
    ('/api/tracker/stats', get_status_tracker_stats, ['GET']),
    # Performance monitoring endpoints
    ('/api/monitoring/performance', get_performance_metrics, ['GET']),
    ('/api/monitoring/simulation-performance', get_simulation_performance, ['GET']),
    ('/api/monitoring/system-health', get_system_health, ['GET']),
    ('/api/monitoring/alerts', get_system_alerts, ['GET']),
    ('/api/monitoring/db-optimization', get_db_optimization_stats, ['GET']),
    ('/api/diagnostics/full-report', get_full_diagnostics, ['GET']),
]

for route, view_func, methods in routes:
    solver_bp.route(route, methods=methods)(view_func)
