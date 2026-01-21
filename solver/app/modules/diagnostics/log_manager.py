"""
Modern log streaming and management system for efficient handling of large log files.
Supports pagination, filtering, and structured log analysis.
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from functools import lru_cache
import json

from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Log Manager', external_log_dir)

# Log level hierarchy for filtering
LOG_LEVELS = {
    'DEBUG': 0,
    'INFO': 1,
    'WARNING': 2,
    'ERROR': 3,
    'CRITICAL': 4
}


class LogStreamer:
    """Efficient log file streaming with pagination and filtering."""
    
    def __init__(self, log_file_path, page_size=1000):
        """
        Initialize log streamer.
        
        Args:
            log_file_path: Path to the log file
            page_size: Number of lines per page
        """
        self.log_file_path = log_file_path
        self.page_size = page_size
        self._file_size = None
        self._line_count = None
        self._modification_time = None
        
    def get_logs_paginated(self, page=1, page_size=None, level_filter=None, search_text=None):
        """
        Get logs with pagination and filtering.
        
        Args:
            page: Page number (1-indexed)
            page_size: Lines per page (uses default if None)
            level_filter: Filter by log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            search_text: Search for text in log lines
            
        Returns:
            Dictionary with paginated results and metadata
        """
        if page_size is None:
            page_size = self.page_size
        
        if page < 1:
            page = 1
        
        if not os.path.exists(self.log_file_path):
            return {
                'error': 'Log file not found',
                'page': page,
                'page_size': page_size,
                'lines': [],
                'total_lines': 0,
                'total_pages': 0
            }
        
        try:
            # Get file metadata
            file_stats = os.stat(self.log_file_path)
            file_size_mb = file_stats.st_size / (1024 * 1024)
            
            # Read and filter logs
            lines = self._read_filtered_logs(level_filter, search_text)
            total_lines = len(lines)
            total_pages = (total_lines + page_size - 1) // page_size
            
            # Ensure page is within bounds
            if page > total_pages and total_pages > 0:
                page = total_pages
            
            # Extract page lines
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_lines = lines[start_idx:end_idx]
            
            return {
                'page': page,
                'page_size': page_size,
                'total_lines': total_lines,
                'total_pages': total_pages,
                'lines': page_lines,
                'file_size_mb': round(file_size_mb, 2),
                'last_modified': datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                'filters_applied': {
                    'level': level_filter,
                    'search': search_text
                }
            }
        except Exception as e:
            logger.error(f"Error reading log file: {str(e)}")
            return {
                'error': str(e),
                'page': page,
                'page_size': page_size,
                'lines': [],
                'total_lines': 0,
                'total_pages': 0
            }
    
    def get_logs_tail(self, num_lines=100, level_filter=None):
        """
        Get the last N lines of the log file (efficient tail).
        
        Args:
            num_lines: Number of lines to retrieve from the end
            level_filter: Optional log level filter
            
        Returns:
            List of log lines
        """
        if not os.path.exists(self.log_file_path):
            return []
        
        try:
            # Read entire file if filtering, otherwise use tail-specific approach
            if level_filter:
                lines = self._read_filtered_logs(level_filter)
                return lines[-num_lines:] if lines else []
            else:
                # Efficient tail without reading entire file
                with open(self.log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    # Seek to end and read backwards
                    f.seek(0, 2)  # Seek to end
                    file_size = f.tell()
                    
                    # Start from end and read backwards in chunks
                    buffer_size = 8192
                    lines_found = 0
                    lines = []
                    position = file_size
                    
                    while position >= 0 and lines_found < num_lines:
                        position = max(0, position - buffer_size)
                        f.seek(position)
                        chunk = f.read(min(buffer_size, file_size - position))
                        chunk_lines = chunk.split('\n')
                        
                        # Add lines in reverse order
                        for line in reversed(chunk_lines):
                            if line.strip():
                                lines.insert(0, line)
                                lines_found += 1
                                if lines_found >= num_lines:
                                    break
                    
                    return lines[:num_lines]
        except Exception as e:
            logger.error(f"Error reading log tail: {str(e)}")
            return []
    
    def get_logs_by_time_range(self, start_time=None, end_time=None, page=1, page_size=None):
        """
        Get logs within a specific time range.
        
        Args:
            start_time: Start datetime (ISO format or datetime object)
            end_time: End datetime (ISO format or datetime object)
            page: Page number for results
            page_size: Lines per page
            
        Returns:
            Paginated logs within the time range
        """
        if page_size is None:
            page_size = self.page_size
        
        if not os.path.exists(self.log_file_path):
            return {
                'error': 'Log file not found',
                'page': page,
                'page_size': page_size,
                'lines': [],
                'total_lines': 0,
                'total_pages': 0
            }
        
        # Parse datetime strings if needed
        if isinstance(start_time, str):
            try:
                start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            except:
                start_time = None
        
        if isinstance(end_time, str):
            try:
                end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            except:
                end_time = None
        
        try:
            filtered_lines = self._read_logs_by_time(start_time, end_time)
            total_lines = len(filtered_lines)
            total_pages = (total_lines + page_size - 1) // page_size
            
            if page > total_pages and total_pages > 0:
                page = total_pages
            
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_lines = filtered_lines[start_idx:end_idx]
            
            return {
                'page': page,
                'page_size': page_size,
                'total_lines': total_lines,
                'total_pages': total_pages,
                'lines': page_lines,
                'time_range': {
                    'start': start_time.isoformat() if start_time else None,
                    'end': end_time.isoformat() if end_time else None
                }
            }
        except Exception as e:
            logger.error(f"Error reading logs by time range: {str(e)}")
            return {
                'error': str(e),
                'page': page,
                'page_size': page_size,
                'lines': [],
                'total_lines': 0,
                'total_pages': 0
            }
    
    def get_log_statistics(self):
        """
        Get statistics about the log file.
        
        Returns:
            Dictionary with log statistics
        """
        if not os.path.exists(self.log_file_path):
            return {'error': 'Log file not found'}
        
        try:
            file_stats = os.stat(self.log_file_path)
            
            # Count log levels
            level_counts = {level: 0 for level in LOG_LEVELS.keys()}
            total_lines = 0
            
            with open(self.log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    total_lines += 1
                    for level in LOG_LEVELS.keys():
                        if f' - {level} - ' in line:
                            level_counts[level] += 1
                            break
            
            # Extract timestamp range
            first_timestamp = None
            last_timestamp = None
            
            with open(self.log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                first_line = f.readline()
                if first_line:
                    first_timestamp = self._extract_timestamp(first_line)
                
                # Read last line
                f.seek(0, 2)
                file_size = f.tell()
                f.seek(max(0, file_size - 1000))
                lines = f.readlines()
                if lines:
                    last_timestamp = self._extract_timestamp(lines[-1])
            
            return {
                'total_lines': total_lines,
                'file_size_mb': round(file_stats.st_size / (1024 * 1024), 2),
                'level_distribution': level_counts,
                'first_log': first_timestamp,
                'last_log': last_timestamp,
                'created': datetime.fromtimestamp(file_stats.st_ctime).isoformat(),
                'modified': datetime.fromtimestamp(file_stats.st_mtime).isoformat()
            }
        except Exception as e:
            logger.error(f"Error getting log statistics: {str(e)}")
            return {'error': str(e)}
    
    def _read_filtered_logs(self, level_filter=None, search_text=None):
        """Read logs with optional filtering."""
        lines = []
        
        try:
            with open(self.log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    # Apply level filter
                    if level_filter and level_filter.upper() in LOG_LEVELS:
                        line_level = self._extract_log_level(line)
                        if line_level and LOG_LEVELS[line_level] < LOG_LEVELS[level_filter.upper()]:
                            continue
                    
                    # Apply text search
                    if search_text and search_text.lower() not in line.lower():
                        continue
                    
                    lines.append(line.rstrip('\n'))
        except Exception as e:
            logger.error(f"Error reading filtered logs: {str(e)}")
        
        return lines
    
    def _read_logs_by_time(self, start_time=None, end_time=None):
        """Read logs within a specific time range."""
        lines = []
        
        try:
            with open(self.log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    log_time = self._extract_timestamp(line)
                    
                    if not log_time:
                        continue
                    
                    # Check if within range
                    if start_time and log_time < start_time:
                        continue
                    if end_time and log_time > end_time:
                        continue
                    
                    lines.append(line.rstrip('\n'))
        except Exception as e:
            logger.error(f"Error reading logs by time range: {str(e)}")
        
        return lines
    
    @staticmethod
    def _extract_log_level(line):
        """Extract log level from a log line."""
        for level in LOG_LEVELS.keys():
            if f' - {level} - ' in line:
                return level
        return None
    
    @staticmethod
    def _extract_timestamp(line):
        """Extract timestamp from a log line."""
        # Match standard log format: YYYY-MM-DD HH:MM:SS,mmm
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        if match:
            try:
                return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
            except:
                return None
        return None


def get_log_streamer(log_file_path, page_size=1000):
    """Factory function to create a log streamer instance."""
    return LogStreamer(log_file_path, page_size)
