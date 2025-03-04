from flask import Flask
from flask_cors import CORS
from .routes import solver_bp

def create_app():
    app = Flask(__name__)
    CORS(app)

    # Register
    app.register_blueprint(solver_bp)

    return app
