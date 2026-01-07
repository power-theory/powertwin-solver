"""
Database module for PowerTwin Solver.
Provides database operations for both SQLite (HPC) and PostgreSQL (Docker) environments.
"""

# This module provides database operations that automatically route to the appropriate
# database backend based on the detected environment (HPC vs Docker)