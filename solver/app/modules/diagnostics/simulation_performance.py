# ======================================================================================
# Simulation Performance Tracker Module
# Tracks simulation metrics over time including timing, throughput, and failure rates
# ======================================================================================

import os
import time
from datetime import datetime, timedelta, timezone
from threading import RLock
from collections import deque
from modules.utils import initialize_logger
from modules.diagnostics.db import get_status_stats

# Initialize logger for this module
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Simulation Performance', external_log_dir)

# Global performance tracker instance
_performance_tracker = None

class SimulationPerformanceTracker:
    """Tracks simulation performance metrics over time for visualization."""
    
    def __init__(self, max_history_points=1000):
        """
        Initialize the performance tracker.
        
        Args:
            max_history_points: Maximum number of historical data points to retain
        """
        self._lock = RLock()
        self._max_history = max_history_points
        
        # Time-series data for charts
        self._history = deque(maxlen=max_history_points)
        
        # Track last update time
        self._last_update = None
        
        # Track simulation start time for ETA calculation
        self._simulation_start_times = {}
        
        logger.info(f"Initialized SimulationPerformanceTracker with {max_history_points} max history points")
    
    def record_performance_snapshot(self, simulation_name=None):
        """
        Record a performance snapshot using the same data sources as the dashboard.
        Gets simulation status from the working dashboard endpoint.
        
        Args:
            simulation_name: Optional simulation name (ignored, uses current from dashboard)
            
        Returns:
            dict: Performance snapshot data
        """
        with self._lock:
            try:
                # Import here to avoid circular imports
                from app.views import get_current_simulation_status
                
                # Call the WORKING dashboard endpoint
                response = get_current_simulation_status()
                
                # response is a tuple (response_object, status_code)
                response_data = response[0]
                status_code = response[1]
                
                # Get JSON from Flask response
                if hasattr(response_data, 'get_json'):
                    data = response_data.get_json()
                else:
                    data = response_data
                
                if status_code != 200 or not data.get('has_active_simulation'):
                    logger.debug("No active simulation")
                    return {
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'simulation_name': None,
                        'total_assets': 0,
                        'completed': 0,
                        'failed': 0,
                        'in_progress': 0,
                        'completion_percentage': 0,
                        'success_rate': 0,
                        'avg_simulation_time': 0,
                        'throughput': 0,
                        'eta_seconds': None
                    }
                
                # Use the dashboard's data
                sim_name = data.get('simulation_name')
                progress = data.get('progress', {})
                total = progress.get('total_assets', 0)
                processed = progress.get('assets_processed', 0)
                
                timestamp = datetime.now(timezone.utc)
                
                # Calculate metrics from the counts
                completion_percentage = (processed / total * 100) if total > 0 else 0
                success_rate = 100 if processed > 0 else 0  # Optimistic until we have failure data
                
                throughput = self._calculate_throughput(processed, timestamp)
                avg_simulation_time = self._calculate_avg_simulation_time(sim_name, processed, timestamp)
                
                eta_seconds = None
                if avg_simulation_time and avg_simulation_time > 0:
                    remaining = total - processed
                    eta_seconds = remaining * avg_simulation_time
                
                snapshot = {
                    'timestamp': timestamp.isoformat(),
                    'simulation_name': sim_name,
                    'total_assets': total,
                    'completed': processed,
                    'failed': 0,
                    'in_progress': 0,
                    'completion_percentage': round(completion_percentage, 2),
                    'success_rate': round(success_rate, 2),
                    'avg_simulation_time': round(avg_simulation_time, 2) if avg_simulation_time else 0,
                    'throughput': round(throughput, 2),
                    'eta_seconds': eta_seconds
                }
                
                # Add to history
                self._history.append(snapshot)
                self._last_update = timestamp
                
                return snapshot
                
            except Exception as e:
                logger.error(f"Error recording performance snapshot: {str(e)}", exc_info=True)
                return {
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'simulation_name': None,
                    'total_assets': 0,
                    'completed': 0,
                    'failed': 0,
                    'in_progress': 0,
                    'completion_percentage': 0,
                    'success_rate': 0,
                    'avg_simulation_time': 0,
                    'throughput': 0,
                    'eta_seconds': None
                }
    
    def _calculate_avg_simulation_time(self, simulation_name, completed, current_time):
        """
        Calculate average simulation time per asset using actual processing times from database.
        
        Args:
            simulation_name: Name of simulation
            completed: Number of completed assets
            current_time: Current timestamp
            
        Returns:
            float: Average time per asset in seconds (from actual processing times)
        """
        if not simulation_name or completed == 0:
            return 0
        
        try:
            # Query actual processing times from database for finished assets
            from modules.database.sqlite_manager import get_sqlite_manager
            import sqlite3
            manager = get_sqlite_manager()
            conn = sqlite3.connect(manager.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            table_name = os.environ.get('PGDATABASE', 'powertwin')
            
            cursor = conn.execute(f"""
                SELECT AVG(processing_time_seconds) as avg_time
                FROM {table_name}
                WHERE simulation_name = ? AND status = 'Finished' AND processing_time_seconds IS NOT NULL
            """, (simulation_name,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row and row['avg_time']:
                return float(row['avg_time'])
            
            return 0
        except Exception as e:
            logger.debug(f"Error querying avg processing time: {e}")
            # Fallback to elapsed/count if DB query fails
            if simulation_name not in self._simulation_start_times:
                self._simulation_start_times[simulation_name] = current_time
                return 0
            
            start_time = self._simulation_start_times[simulation_name]
            elapsed_seconds = (current_time - start_time).total_seconds()
            
            if completed > 0:
                return elapsed_seconds / completed
            
            return 0
    
    def _calculate_throughput(self, completed, current_time):
        """
        Calculate current throughput in assets per minute.
        
        Uses 30-minute rolling window for calculation.
        
        Args:
            completed: Number of completed assets
            current_time: Current timestamp
            
        Returns:
            float: Throughput in assets per minute
        """
        # Need at least 1 historical data point
        if len(self._history) < 1:
            return 0
        
        # Find data point from 30 minutes ago
        thirty_min_ago = current_time - timedelta(minutes=30)
        
        # Find the oldest snapshot within 30-minute window
        oldest_snapshot = None
        for snapshot in self._history:
            snapshot_time = datetime.fromisoformat(snapshot['timestamp'].replace('Z', '+00:00'))
            if snapshot_time >= thirty_min_ago:
                oldest_snapshot = snapshot
                break
        
        # If no data from 30 min ago, use oldest available
        if oldest_snapshot is None:
            oldest_snapshot = self._history[0]
        
        old_completed = oldest_snapshot.get('completed', 0)
        old_time = datetime.fromisoformat(oldest_snapshot['timestamp'].replace('Z', '+00:00'))
        
        # Calculate delta over the time window
        delta_completed = completed - old_completed
        delta_seconds = (current_time - old_time).total_seconds()
        
        if delta_seconds > 0:
            # Convert to assets per minute
            return (delta_completed / delta_seconds) * 60
        
        return 0
    
    def get_historical_data(self, time_range_hours=None):
        """
        Get historical performance data.
        
        Args:
            time_range_hours: Optional filter for time range (1, 6, 24, or None for all)
            
        Returns:
            list: Historical data points
        """
        with self._lock:
            if not time_range_hours:
                return list(self._history)
            
            # Filter by time range
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=time_range_hours)
            filtered_data = [
                point for point in self._history
                if datetime.fromisoformat(point['timestamp'].replace('Z', '+00:00')) >= cutoff_time
            ]
            
            return filtered_data
    
    def get_latest_snapshot(self):
        """Get the most recent performance snapshot."""
        with self._lock:
            if self._history:
                return self._history[-1]
            return None
    
    def reset_simulation(self, simulation_name):
        """Reset tracking for a specific simulation."""
        with self._lock:
            if simulation_name in self._simulation_start_times:
                del self._simulation_start_times[simulation_name]
            logger.info(f"Reset tracking for simulation: {simulation_name}")
    
    def clear_history(self):
        """Clear all historical data."""
        with self._lock:
            self._history.clear()
            self._simulation_start_times.clear()
            logger.info("Cleared performance history")


def get_performance_tracker():
    """Get or create the global performance tracker instance."""
    global _performance_tracker
    if _performance_tracker is None:
        _performance_tracker = SimulationPerformanceTracker(max_history_points=1000)
    return _performance_tracker
