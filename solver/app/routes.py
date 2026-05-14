from flask import Blueprint
from .views import *

solver_bp = Blueprint('Solver', __name__)

routes = [
    ('/', home, ['GET']),
    ('/api/simulation/start', start_simulation, ['POST']),
    ('/api/simulation/autorun_simulation', autorun_simulation, ['POST']),
    ('/api/simulation/stop', stop_simulation, ['POST']),
    ('/api/simulation/status/<simulation_name>', get_simulation_status, ['GET']),
    ('/api/simulation/delete/<simulation_name>', delete_simulation, ['DELETE']),
    ('/api/asset/config/<simulation_name>/<asset_id>', get_asset_config, ['GET']),
    ('/api/simulation/data',get_simulation_data, ['GET']),
    ('/api/simulation/asset_update', process_asset_update, ['POST']),
    ('/api/simulation/results/<simulation_name>', get_simulation_results, ['GET']),
    ('/api/diagnostics/recovery', recovery, ['POST']),
    ('/api/diagnostics/update_asset', update_asset, ['POST']),
    ('/logs', get_logs, ['GET']),
    ('/api/diagnostics/log', log_message, ['POST']),
]

for route, view_func, methods in routes:
    solver_bp.route(route, methods=methods)(view_func)
