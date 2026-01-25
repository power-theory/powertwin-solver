"""
Performance monitoring system for tracking Solver metrics across high-load scenarios.
Monitors log sizes, database performance, and system health.
"""

import os
import json
import psutil
from datetime import datetime, timedelta
from threading import RLock
from collections import deque

from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Performance Monitor', external_log_dir)


class PerformanceMonitor:
    """
    Monitors system and application performance metrics.
    Tracks log file sizes, database query performance, and system resources.
    """
    
    def __init__(self, max_history=1000):
        """
        Initialize performance monitor.
        
        Args:
            max_history: Maximum number of metric samples to keep in memory
        """
        self._lock = RLock()
        self._max_history = max_history
        self._metrics_history = deque(maxlen=max_history)
        self._log_file_stats = {}
        self._db_query_stats = {
            'total_queries': 0,
            'failed_queries': 0,
            'avg_query_time_ms': 0,
            'slowest_query_ms': 0
        }
        self._system_alerts = []
        self._thresholds = {
            'log_file_size_mb': 100,  # Alert if log file > 100MB
            'cpu_percent': 85,  # Alert if CPU > 85%
            'memory_percent': 80,  # Alert if memory > 80%
            'disk_percent': 90,  # Alert if disk > 90%
            'db_query_time_ms': 5000  # Alert if query > 5s
        }
    
    def record_query_metric(self, query_type, duration_ms, success=True):
        """
        Record database query performance metric.
        
        Args:
            query_type: Type of query (SELECT, INSERT, UPDATE, etc.)
            duration_ms: Query duration in milliseconds
            success: Whether query succeeded
        """
        with self._lock:
            self._db_query_stats['total_queries'] += 1
            if not success:
                self._db_query_stats['failed_queries'] += 1
            
            # Update average
            avg = self._db_query_stats['avg_query_time_ms']
            total = self._db_query_stats['total_queries']
            self._db_query_stats['avg_query_time_ms'] = (
                (avg * (total - 1) + duration_ms) / total
            )
            
            # Update slowest
            if duration_ms > self._db_query_stats['slowest_query_ms']:
                self._db_query_stats['slowest_query_ms'] = duration_ms
            
            # Check for slow query alert
            if duration_ms > self._thresholds['db_query_time_ms']:
                self._add_alert(
                    'SLOW_QUERY',
                    f'{query_type} query took {duration_ms}ms',
                    'WARNING'
                )
            
            # Record metric
            self._metrics_history.append({
                'timestamp': datetime.now(datetime.timezone.utc).isoformat(),
                'type': 'db_query',
                'query_type': query_type,
                'duration_ms': duration_ms,
                'success': success
            })
    
    def check_log_file_health(self, log_file_path):
        """
        Check log file size and health.
        
        Args:
            log_file_path: Path to log file
            
        Returns:
            Dictionary with file stats
        """
        with self._lock:
            try:
                if not os.path.exists(log_file_path):
                    return None
                
                file_stats = os.stat(log_file_path)
                file_size_mb = file_stats.st_size / (1024 * 1024)
                
                stats = {
                    'file_path': log_file_path,
                    'size_mb': round(file_size_mb, 2),
                    'size_bytes': file_stats.st_size,
                    'modified': datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                    'created': datetime.fromtimestamp(file_stats.st_ctime).isoformat(),
                    'health': 'HEALTHY'
                }
                
                # Check thresholds
                if file_size_mb > self._thresholds['log_file_size_mb']:
                    stats['health'] = 'WARNING'
                    self._add_alert(
                        'LARGE_LOG_FILE',
                        f'Log file {log_file_path} is {file_size_mb}MB (threshold: {self._thresholds["log_file_size_mb"]}MB)',
                        'WARNING'
                    )
                
                self._log_file_stats[log_file_path] = stats
                return stats
                
            except Exception as e:
                logger.error(f"Error checking log file health: {str(e)}")
                return None
    
    def check_system_resources(self):
        """
        Check system resource usage (CPU, memory, disk).
        
        Returns:
            Dictionary with system metrics
        """
        with self._lock:
            try:
                # CPU metrics
                cpu_percent = psutil.cpu_percent(interval=1)
                cpu_count = psutil.cpu_count()
                
                # Memory metrics
                memory = psutil.virtual_memory()
                memory_percent = memory.percent
                
                # Disk metrics (root partition)
                disk = psutil.disk_usage('/')
                disk_percent = disk.percent
                
                stats = {
                    'timestamp': datetime.now().isoformat(),
                    'cpu': {
                        'percent': cpu_percent,
                        'count': cpu_count,
                        'health': 'HEALTHY' if cpu_percent < self._thresholds['cpu_percent'] else 'WARNING'
                    },
                    'memory': {
                        'percent': memory_percent,
                        'available_mb': memory.available / (1024 * 1024),
                        'total_mb': memory.total / (1024 * 1024),
                        'health': 'HEALTHY' if memory_percent < self._thresholds['memory_percent'] else 'WARNING'
                    },
                    'disk': {
                        'percent': disk_percent,
                        'free_mb': disk.free / (1024 * 1024),
                        'total_mb': disk.total / (1024 * 1024),
                        'health': 'HEALTHY' if disk_percent < self._thresholds['disk_percent'] else 'WARNING'
                    }
                }
                
                # Add alerts for thresholds
                if cpu_percent > self._thresholds['cpu_percent']:
                    self._add_alert('HIGH_CPU', f'CPU usage at {cpu_percent}%', 'WARNING')
                
                if memory_percent > self._thresholds['memory_percent']:
                    self._add_alert('HIGH_MEMORY', f'Memory usage at {memory_percent}%', 'WARNING')
                
                if disk_percent > self._thresholds['disk_percent']:
                    self._add_alert('HIGH_DISK', f'Disk usage at {disk_percent}%', 'WARNING')
                
                # Record metric
                self._metrics_history.append({
                    'timestamp': stats['timestamp'],
                    'type': 'system_resources',
                    'cpu_percent': cpu_percent,
                    'memory_percent': memory_percent,
                    'disk_percent': disk_percent
                })
                
                return stats
                
            except Exception as e:
                logger.error(f"Error checking system resources: {str(e)}")
                return None
    
    def _add_alert(self, alert_type, message, severity='INFO'):
        """Add system alert."""
        with self._lock:
            alert = {
                'timestamp': datetime.now().isoformat(),
                'type': alert_type,
                'message': message,
                'severity': severity
            }
            self._system_alerts.append(alert)
            
            # Keep only recent alerts (last 100)
            if len(self._system_alerts) > 100:
                self._system_alerts = self._system_alerts[-100:]
            
            logger.warning(f"[{alert_type}] {message}")
    
    def get_alerts(self, since_minutes=None, severity=None):
        """
        Get system alerts.
        
        Args:
            since_minutes: Only return alerts from the last N minutes
            severity: Filter by severity (INFO, WARNING, CRITICAL)
            
        Returns:
            List of alerts
        """
        with self._lock:
            alerts = self._system_alerts.copy()
            
            if since_minutes:
                cutoff = datetime.now() - timedelta(minutes=since_minutes)
                alerts = [
                    a for a in alerts
                    if datetime.fromisoformat(a['timestamp']) > cutoff
                ]
            
            if severity:
                alerts = [a for a in alerts if a['severity'] == severity]
            
            return alerts
    
    def get_comprehensive_report(self):
        """Get comprehensive performance report."""
        with self._lock:
            report = {
                'timestamp': datetime.now().isoformat(),
                'database': {
                    'total_queries': self._db_query_stats['total_queries'],
                    'failed_queries': self._db_query_stats['failed_queries'],
                    'avg_query_time_ms': round(self._db_query_stats['avg_query_time_ms'], 2),
                    'slowest_query_ms': self._db_query_stats['slowest_query_ms'],
                    'success_rate_percent': (
                        ((self._db_query_stats['total_queries'] - self._db_query_stats['failed_queries']) / 
                         self._db_query_stats['total_queries'] * 100)
                        if self._db_query_stats['total_queries'] > 0 else 0
                    )
                },
                'log_files': self._log_file_stats,
                'recent_alerts': self.get_alerts(since_minutes=60),
                'metrics_samples': len(self._metrics_history)
            }
            return report
    
    def set_threshold(self, metric_name, threshold_value):
        """Set performance threshold."""
        with self._lock:
            if metric_name in self._thresholds:
                self._thresholds[metric_name] = threshold_value
                logger.info(f"Threshold updated: {metric_name} = {threshold_value}")
                return True
            return False
    
    def get_thresholds(self):
        """Get current performance thresholds."""
        with self._lock:
            return self._thresholds.copy()


# Global instance
_global_monitor = None


def get_monitor():
    """Get or create global performance monitor instance."""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = PerformanceMonitor(max_history=1000)
        logger.info("Performance monitor initialized")
    return _global_monitor


def record_query_metric(query_type, duration_ms, success=True):
    """Record database query metric."""
    return get_monitor().record_query_metric(query_type, duration_ms, success)


def check_log_health(log_file_path):
    """Check log file health."""
    return get_monitor().check_log_file_health(log_file_path)


def check_system_health():
    """Check system resource health."""
    return get_monitor().check_system_resources()


def get_performance_report():
    """Get comprehensive performance report."""
    return get_monitor().get_comprehensive_report()


def get_recent_alerts(since_minutes=60, severity=None):
    """Get recent alerts."""
    return get_monitor().get_alerts(since_minutes, severity)


def set_alert_threshold(metric_name, threshold_value):
    """Set alert threshold."""
    return get_monitor().set_threshold(metric_name, threshold_value)
