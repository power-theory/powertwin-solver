"""
Modern status tracking system with in-memory cache and database persistence.
Optimized for high-load scenarios with minimal database queries.
"""

import os
import json
from datetime import datetime, timedelta
from threading import RLock
from collections import defaultdict
from functools import lru_cache
import time

from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Status Tracker', external_log_dir)


# =====================================================================================
# High-Performance Status Tracker with Caching
# Minimizes database queries through memory caching and batch operations
# =====================================================================================
class StatusTracker:
    """High-performance status tracking with caching and batching."""
    
    def __init__(self, cache_ttl_seconds=30, batch_flush_interval=5):
        """
        Initialize the status tracker.
        
        Args:
            cache_ttl_seconds: Cache time-to-live in seconds
            batch_flush_interval: Interval (in seconds) for flushing batched updates
        """
        self._cache = {}
        self._batch_queue = defaultdict(dict)
        self._lock = RLock()
        self._cache_ttl = cache_ttl_seconds
        self._batch_flush_interval = batch_flush_interval
        self._last_flush = time.time()
        self._stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'db_queries': 0,
            'batch_size_avg': 0,
            'flush_count': 0
        }
        
    def get_status(self, simulation_name, asset_id=None, batch_id=None, use_cache=True):
        """
        Get status with optional caching.
        
        Args:
            simulation_name: Name of the simulation
            asset_id: Optional asset ID
            batch_id: Optional batch ID
            use_cache: Whether to use cache (default: True)
            
        Returns:
            Status dictionary or None if not found
        """
        with self._lock:
            cache_key = self._build_cache_key(simulation_name, asset_id, batch_id)
            
            # Check cache first
            if use_cache and cache_key in self._cache:
                cached_data = self._cache[cache_key]
                if datetime.now(datetime.timezone.utc) - cached_data['timestamp'] < timedelta(seconds=self._cache_ttl):
                    self._stats['cache_hits'] += 1
                    return cached_data['data']
                else:
                    # Cache expired, remove it
                    del self._cache[cache_key]
            
            self._stats['cache_misses'] += 1
            return None
    
    def set_status(self, simulation_name, status_data, asset_id=None, batch_id=None, batch=False):
        """
        Set status with optional batching.
        
        Args:
            simulation_name: Name of the simulation
            status_data: Status data dictionary
            asset_id: Optional asset ID
            batch_id: Optional batch ID
            batch: If True, batch the update instead of immediate cache update
        """
        with self._lock:
            cache_key = self._build_cache_key(simulation_name, asset_id, batch_id)
            
            if batch:
                # Add to batch queue
                self._batch_queue[simulation_name][cache_key] = {
                    'data': status_data,
                    'timestamp': datetime.now()
                }
                
                # Check if we should flush
                if time.time() - self._last_flush > self._batch_flush_interval:
                    self._flush_batch_queue()
            else:
                # Immediate cache update
                self._cache[cache_key] = {
                    'data': status_data,
                    'timestamp': datetime.now()
                }
    
    def batch_update_statuses(self, updates):
        """
        Update multiple statuses at once with batching.
        
        Args:
            updates: List of tuples (simulation_name, status_data, asset_id, batch_id)
        """
        with self._lock:
            for sim_name, status_data, asset_id, batch_id in updates:
                cache_key = self._build_cache_key(sim_name, asset_id, batch_id)
                self._batch_queue[sim_name][cache_key] = {
                    'data': status_data,
                    'timestamp': datetime.now()
                }
            
            # Flush if needed
            if time.time() - self._last_flush > self._batch_flush_interval:
                self._flush_batch_queue()
    
    def get_simulation_status_summary(self, simulation_name):
        """
        Get aggregated status for entire simulation without multiple queries.
        
        Args:
            simulation_name: Name of the simulation
            
        Returns:
            Dictionary with simulation status summary
        """
        with self._lock:
            summary = {
                'simulation_name': simulation_name,
                'total_assets': 0,
                'completed': 0,
                'in_progress': 0,
                'failed': 0,
                'pending': 0,
                'timestamp': datetime.now().isoformat()
            }
            
            # Count from cache
            for cache_key, cached_entry in self._cache.items():
                if simulation_name in cache_key:
                    status = cached_entry['data'].get('status', 'unknown')
                    summary['total_assets'] += 1
                    
                    if status == 'completed':
                        summary['completed'] += 1
                    elif status == 'in_progress':
                        summary['in_progress'] += 1
                    elif status == 'failed':
                        summary['failed'] += 1
                    else:
                        summary['pending'] += 1
            
            # Count from batch queue
            if simulation_name in self._batch_queue:
                for cache_key, entry in self._batch_queue[simulation_name].items():
                    status = entry['data'].get('status', 'unknown')
                    summary['total_assets'] += 1
                    
                    if status == 'completed':
                        summary['completed'] += 1
                    elif status == 'in_progress':
                        summary['in_progress'] += 1
                    elif status == 'failed':
                        summary['failed'] += 1
                    else:
                        summary['pending'] += 1
            
            # Calculate percentages
            if summary['total_assets'] > 0:
                summary['completion_percentage'] = (summary['completed'] / summary['total_assets']) * 100
                summary['success_rate'] = ((summary['completed'] + summary['pending']) / summary['total_assets']) * 100
            else:
                summary['completion_percentage'] = 0
                summary['success_rate'] = 0
            
            return summary
    
    def _flush_batch_queue(self):
        """Flush pending batch updates to cache."""
        if not self._batch_queue:
            return
        
        with self._lock:
            total_items = 0
            for sim_name, updates in self._batch_queue.items():
                for cache_key, entry in updates.items():
                    self._cache[cache_key] = entry
                    total_items += 1
            
            if total_items > 0:
                self._stats['batch_size_avg'] = (
                    (self._stats['batch_size_avg'] * self._stats['flush_count'] + total_items) / 
                    (self._stats['flush_count'] + 1)
                ) if self._stats['flush_count'] > 0 else total_items
                self._stats['flush_count'] += 1
            
            self._batch_queue.clear()
            self._last_flush = time.time()
            logger.debug(f"Flushed {total_items} cached status updates")
    
    def clear_expired_cache(self):
        """Remove expired cache entries."""
        with self._lock:
            now = datetime.now()
            expired_keys = [
                key for key, entry in self._cache.items()
                if now - entry['timestamp'] > timedelta(seconds=self._cache_ttl * 2)
            ]
            
            for key in expired_keys:
                del self._cache[key]
            
            if expired_keys:
                logger.debug(f"Cleared {len(expired_keys)} expired cache entries")
    
    def get_cache_stats(self):
        """Get cache performance statistics."""
        with self._lock:
            total_requests = self._stats['cache_hits'] + self._stats['cache_misses']
            hit_rate = (self._stats['cache_hits'] / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'cache_size': len(self._cache),
                'batch_queue_size': sum(len(v) for v in self._batch_queue.values()),
                'cache_hits': self._stats['cache_hits'],
                'cache_misses': self._stats['cache_misses'],
                'hit_rate_percent': round(hit_rate, 2),
                'avg_batch_size': round(self._stats['batch_size_avg'], 2),
                'total_flushes': self._stats['flush_count'],
                'db_queries_avoided': self._stats['cache_hits']
            }
    
    @staticmethod
    def _build_cache_key(simulation_name, asset_id=None, batch_id=None):
        """Build a cache key from status parameters."""
        parts = [simulation_name]
        if asset_id is not None:
            parts.append(f"asset_{asset_id}")
        if batch_id is not None:
            parts.append(f"batch_{batch_id}")
        return ":".join(parts)


# Global instance for application-wide status tracking
_global_tracker = None


def get_tracker():
    """Get or create the global status tracker instance."""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = StatusTracker(cache_ttl_seconds=30, batch_flush_interval=5)
        logger.info("Status tracker initialized with 30s cache TTL and 5s flush interval")
    return _global_tracker


def get_status(simulation_name, asset_id=None, batch_id=None, use_cache=True):
    """Get status from global tracker."""
    return get_tracker().get_status(simulation_name, asset_id, batch_id, use_cache)


def set_status(simulation_name, status_data, asset_id=None, batch_id=None, batch=False):
    """Set status in global tracker."""
    return get_tracker().set_status(simulation_name, status_data, asset_id, batch_id, batch)


def batch_update_statuses(updates):
    """Batch update multiple statuses."""
    return get_tracker().batch_update_statuses(updates)


def get_simulation_summary(simulation_name):
    """Get simulation status summary."""
    return get_tracker().get_simulation_status_summary(simulation_name)


def get_tracker_stats():
    """Get tracker statistics."""
    return get_tracker().get_cache_stats()
