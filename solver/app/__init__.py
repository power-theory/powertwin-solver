from flask import Flask
from flask_cors import CORS
from .routes import solver_bp
# `modules.*`, not `.modules.*`: the rest of the app imports the top-level package, and mixing
# the two loads modules/utils twice as distinct objects (two weather caches, two of everything).
from modules.utils.pack_results import reap_orphaned_sims

def create_app():
    app = Flask(__name__)
    CORS(app)

    # Register
    app.register_blueprint(solver_bp)

    # Runs per gunicorn worker; skips sims a live sibling worker still owns.
    reap_orphaned_sims()

    return app
