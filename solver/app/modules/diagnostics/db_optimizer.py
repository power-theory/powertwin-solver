"""
Database optimization layer with connection pooling, batching, and caching.
Designed for high-load scenarios with efficient query batching.
"""

import os
from threading import RLock, Thread
from queue import Queue
from time import sleep, time as current_time
from datetime import datetime, timedelta

from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Database Optimizer', external_log_dir)


class BatchedUpdateManager:
    """
    Manages batched database updates for improved performance under high load.
    Accumulates updates and flushes them in optimized batches.
    """
    
    def __init__(self, max_batch_size=500, flush_interval_seconds=5):
        """
        Initialize the batched update manager.
        
        Args:
            max_batch_size: Maximum updates to accumulate before flushing
            flush_interval_seconds: Maximum time before flushing even if batch not full
        """
        self._pending_updates = {}  # {table: [updates]}
        self._lock = RLock()
        self._max_batch_size = max_batch_size
        self._flush_interval = flush_interval_seconds
        self._last_flush_time = current_time()
        self._stats = {
            'total_updates': 0,
            'total_batches': 0,
            'avg_batch_size': 0,
            'updates_accumulated': 0
        }
    
    def add_update(self, table_name, update_query, params, flush_if_ready=False):
        """
        Add an update to the batch queue.
        
        Args:
            table_name: Table being updated
            update_query: SQL query (parameterized)
            params: Query parameters
            flush_if_ready: If True, check if batch should be flushed
            
        Returns:
            True if update added successfully
        """
        with self._lock:
            if table_name not in self._pending_updates:
                self._pending_updates[table_name] = []
            
            self._pending_updates[table_name].append({
                'query': update_query,
                'params': params
            })
            
            self._stats['updates_accumulated'] += 1
            
            # Check if flush is needed
            if flush_if_ready:
                self._check_flush_needed()
            
            return True
    
    def add_bulk_updates(self, table_name, updates):
        """
        Add multiple updates at once.
        
        Args:
            table_name: Table being updated
            updates: List of (query, params) tuples
            
        Returns:
            Number of updates added
        """
        with self._lock:
            if table_name not in self._pending_updates:
                self._pending_updates[table_name] = []
            
            for query, params in updates:
                self._pending_updates[table_name].append({
                    'query': query,
                    'params': params
                })
            
            count = len(updates)
            self._stats['updates_accumulated'] += count
            
            # Check if flush is needed
            self._check_flush_needed()
            
            return count
    
    def _check_flush_needed(self):
        """Check if batch should be flushed based on size or time."""
        total_updates = sum(len(updates) for updates in self._pending_updates.values())
        time_since_flush = current_time() - self._last_flush_time
        
        if (total_updates >= self._max_batch_size or 
            time_since_flush >= self._flush_interval):
            return True
        
        return False
    
    def flush(self, db_connection_func):
        """
        Flush all pending updates to the database.
        
        Args:
            db_connection_func: Callable that returns a database connection
            
        Returns:
            Dictionary with flush statistics
        """
        with self._lock:
            if not self._pending_updates:
                return {'updates_flushed': 0, 'tables_updated': 0}
            
            try:
                conn = db_connection_func()
                cur = conn.cursor()
                
                total_updates = 0
                tables_updated = 0
                
                # Execute all pending updates in a transaction
                for table_name, updates in self._pending_updates.items():
                    for update in updates:
                        try:
                            cur.execute(update['query'], update['params'])
                            total_updates += 1
                        except Exception as e:
                            logger.error(f"Error executing update for {table_name}: {str(e)}")
                            conn.rollback()
                            raise
                    
                    tables_updated += 1
                
                # Commit all changes at once
                conn.commit()
                
                # Update statistics
                if self._stats['total_batches'] > 0:
                    self._stats['avg_batch_size'] = (
                        (self._stats['avg_batch_size'] * self._stats['total_batches'] + total_updates) /
                        (self._stats['total_batches'] + 1)
                    )
                else:
                    self._stats['avg_batch_size'] = total_updates
                
                self._stats['total_updates'] += total_updates
                self._stats['total_batches'] += 1
                
                logger.info(f"Flushed {total_updates} updates across {tables_updated} tables")
                
                # Clear pending updates
                self._pending_updates.clear()
                self._last_flush_time = current_time()
                
                return {
                    'updates_flushed': total_updates,
                    'tables_updated': tables_updated,
                    'success': True
                }
                
            except Exception as e:
                logger.error(f"Error flushing batch updates: {str(e)}")
                return {
                    'updates_flushed': 0,
                    'error': str(e),
                    'success': False
                }
            finally:
                if cur:
                    cur.close()
                if conn:
                    conn.close()
    
    def get_stats(self):
        """Get batch manager statistics."""
        with self._lock:
            pending_count = sum(len(updates) for updates in self._pending_updates.values())
            
            return {
                'total_updates_flushed': self._stats['total_updates'],
                'total_batches': self._stats['total_batches'],
                'avg_batch_size': round(self._stats['avg_batch_size'], 2),
                'pending_updates': pending_count,
                'tables_with_pending': len(self._pending_updates)
            }


class QueryCache:
    """
    Simple query result cache with TTL for frequently accessed data.
    """
    
    def __init__(self, ttl_seconds=300):
        """
        Initialize query cache.
        
        Args:
            ttl_seconds: Cache time-to-live in seconds
        """
        self._cache = {}
        self._lock = RLock()
        self._ttl = ttl_seconds
    
    def get(self, cache_key):
        """
        Get cached value if present and not expired.
        
        Args:
            cache_key: Cache key (usually a query hash)
            
        Returns:
            Cached value or None if not found/expired
        """
        with self._lock:
            if cache_key in self._cache:
                cached_data = self._cache[cache_key]
                age = datetime.now() - cached_data['timestamp']
                
                if age < timedelta(seconds=self._ttl):
                    return cached_data['value']
                else:
                    # Cache expired
                    del self._cache[cache_key]
            
            return None
    
    def set(self, cache_key, value):
        """
        Set cache value.
        
        Args:
            cache_key: Cache key
            value: Value to cache
        """
        with self._lock:
            self._cache[cache_key] = {
                'value': value,
                'timestamp': datetime.now()
            }
    
    def invalidate(self, cache_key_pattern=None):
        """
        Invalidate cache entries.
        
        Args:
            cache_key_pattern: If provided, only invalidate keys matching this pattern
        """
        with self._lock:
            if cache_key_pattern is None:
                self._cache.clear()
            else:
                keys_to_delete = [
                    key for key in self._cache.keys()
                    if cache_key_pattern in key
                ]
                for key in keys_to_delete:
                    del self._cache[key]
    
    def get_stats(self):
        """Get cache statistics."""
        with self._lock:
            return {
                'cache_size': len(self._cache),
                'ttl_seconds': self._ttl
            }


# Global instances
_batch_manager = None
_query_cache = None


def get_batch_manager():
    """Get or create global batch manager instance."""
    global _batch_manager
    if _batch_manager is None:
        _batch_manager = BatchedUpdateManager(max_batch_size=500, flush_interval_seconds=5)
        logger.info("Batch update manager initialized")
    return _batch_manager


def get_query_cache():
    """Get or create global query cache instance."""
    global _query_cache
    if _query_cache is None:
        _query_cache = QueryCache(ttl_seconds=300)
        logger.info("Query cache initialized with 300s TTL")
    return _query_cache


def add_batched_update(table_name, update_query, params):
    """Add update to batch queue."""
    return get_batch_manager().add_update(table_name, update_query, params, flush_if_ready=True)


def add_bulk_updates(table_name, updates):
    """Add multiple updates at once."""
    return get_batch_manager().add_bulk_updates(table_name, updates)


def flush_batch_updates(db_connection_func):
    """Flush all pending batch updates."""
    return get_batch_manager().flush(db_connection_func)


def cache_query_result(cache_key, value):
    """Cache a query result."""
    return get_query_cache().set(cache_key, value)


def get_cached_query(cache_key):
    """Get cached query result."""
    return get_query_cache().get(cache_key)


def invalidate_cache(pattern=None):
    """Invalidate cache entries."""
    return get_query_cache().invalidate(pattern)


def get_optimization_stats():
    """Get comprehensive optimization statistics."""
    return {
        'batch_manager': get_batch_manager().get_stats(),
        'query_cache': get_query_cache().get_stats()
    }
