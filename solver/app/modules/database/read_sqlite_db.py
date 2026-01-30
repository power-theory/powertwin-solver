#!/usr/bin/env python3
"""
SQLite Database Reader for PowerTwin
Reads and displays contents of the powertwin.db SQLite database
"""

import sqlite3
import sys
import os
from pathlib import Path

def read_sqlite_db(db_path, table_name="powertwin", read_timeout=5000):
    """Read and display contents of SQLite database."""
    
    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}")
        return False
    
    try:
        # Connect with read-only mode and timeout for better concurrency
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=read_timeout/1000)
        conn.row_factory = sqlite3.Row
        
        # Set additional pragmas for read operations
        conn.execute("PRAGMA query_only = 1")
        conn.execute(f"PRAGMA busy_timeout = {read_timeout}")
        
        print(f"Reading database: {db_path} (read-only mode)")
        print(f"Table name: {table_name}")
        print("=" * 80)
        
        # Check if table exists
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        if not cursor.fetchone():
            print(f"Table '{table_name}' not found in database")
            
            # Show available tables
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            if tables:
                print("Available tables:")
                for table in tables:
                    print(f"  - {table['name']}")
            else:
                print("No tables found in database")
            return False
        
        # Get table schema
        print(f"\n--- Table Schema for '{table_name}' ---")
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        for col in columns:
            print(f"{col['name']:20} {col['type']:15} {'NOT NULL' if col['notnull'] else 'NULL':8} {'PK' if col['pk'] else ''}")
        
        # Get row count
        cursor = conn.execute(f"SELECT COUNT(*) as count FROM {table_name}")
        total_count = cursor.fetchone()['count']
        print(f"\nTotal records: {total_count}")
        
        if total_count == 0:
            print("No records found in table")
            return True
        
        # Get simulation names
        cursor = conn.execute(f"SELECT DISTINCT simulation_name FROM {table_name}")
        simulations = [row['simulation_name'] for row in cursor.fetchall()]
        print(f"Simulations found: {simulations}")
        
        # Track global timing statistics
        all_total_times = []
        
        # For each simulation, show batch distribution
        for sim_name in simulations:
            print(f"\n--- Simulation: {sim_name} ---")
            
            # Count by batch for all assets
            cursor = conn.execute(f"""
                SELECT 
                    batch,
                    COUNT(*) as count,
                    GROUP_CONCAT(asset_id, ',') as asset_ids
                FROM {table_name} 
                WHERE simulation_name = ?
                GROUP BY batch 
                ORDER BY batch
            """, (sim_name,))
            
            batch_info = cursor.fetchall()
            
            if not batch_info:
                print("No batches found for this simulation")
                continue
                
            print("Batch distribution (All assets):")
            print("Batch | Count | Asset IDs")
            print("-" * 50)
            
            for row in batch_info:
                batch = row['batch'] if row['batch'] is not None else 'NULL'
                count = row['count']
                asset_ids = row['asset_ids'][:30] + '...' if len(row['asset_ids']) > 30 else row['asset_ids']
                print(f"{str(batch):5} | {count:5} | {asset_ids}")
            
            # Count by status
            cursor = conn.execute(f"""
                SELECT 
                    status,
                    COUNT(*) as count
                FROM {table_name} 
                WHERE simulation_name = ?
                GROUP BY status 
                ORDER BY status
            """, (sim_name,))
            
            status_info = cursor.fetchall()
            
            print("\nStatus distribution:")
            print("Status     | Count")
            print("-" * 20)
            for row in status_info:
                status = row['status'] if row['status'] else 'NULL'
                count = row['count']
                print(f"{status:10} | {count:5}")
            
            # Show weather file analysis
            print(f"\nWeather File Analysis:")
            cursor = conn.execute(f"""
                SELECT 
                    weather_file,
                    SUM(CASE WHEN status IN ('failed', 'Failed', 'FAILED', 'error', 'Error', 'ERROR') THEN 1 ELSE 0 END) as failed_count,
                    SUM(CASE WHEN status IN ('finished', 'Finished', 'FINISHED', 'complete', 'Complete', 'COMPLETE') THEN 1 ELSE 0 END) as finished_count,
                    COUNT(*) as total_count
                FROM {table_name} 
                WHERE simulation_name = ?
                GROUP BY weather_file
                ORDER BY failed_count DESC, weather_file
            """, (sim_name,))
            
            weather_records = cursor.fetchall()
            if weather_records:
                print("Weather File               | Failed | Finished | Total")
                print("-" * 60)
                for row in weather_records:
                    weather_file = (row['weather_file']) if row['weather_file'] and len(row['weather_file']) > 25 else (row['weather_file'] or 'NULL')
                    failed_count = row['failed_count']
                    finished_count = row['finished_count']
                    total_count = row['total_count']
                    print(f"{weather_file:26} | {failed_count:6} | {finished_count:8} | {total_count:5}")
            else:
                print("No weather file data found for this simulation")
            
            # Calculate timing statistics for this simulation
            print(f"\n--- Timing Analysis for {sim_name} ---")
            cursor = conn.execute(f"""
                SELECT 
                    COUNT(*) as total_assets,
                    COUNT(total_time) as assets_with_time,
                    AVG(total_time) as avg_total_time,
                    MIN(total_time) as min_total_time,
                    MAX(total_time) as max_total_time,
                    AVG(uorun_time) as avg_uorun_time,
                    AVG(uoprocess_time) as avg_uoprocess_time
                FROM {table_name} 
                WHERE simulation_name = ? AND total_time IS NOT NULL
            """, (sim_name,))
            
            timing_stats = cursor.fetchone()
            
            if timing_stats and timing_stats['assets_with_time'] > 0:
                print("Timing Statistics:")
                print(f"  Total assets with timing data: {timing_stats['assets_with_time']:,}")
                print(f"  Average total time: {timing_stats['avg_total_time']:.2f} seconds")
                print(f"  Minimum total time: {timing_stats['min_total_time']:.2f} seconds")
                print(f"  Maximum total time: {timing_stats['max_total_time']:.2f} seconds")
                if timing_stats['avg_uorun_time']:
                    print(f"  Average UO run time: {timing_stats['avg_uorun_time']:.2f} seconds")
                if timing_stats['avg_uoprocess_time']:
                    print(f"  Average UO process time: {timing_stats['avg_uoprocess_time']:.2f} seconds")
                
                # Collect times for global average
                cursor = conn.execute(f"""
                    SELECT total_time FROM {table_name} 
                    WHERE simulation_name = ? AND total_time IS NOT NULL
                """, (sim_name,))
                sim_times = [row['total_time'] for row in cursor.fetchall()]
                all_total_times.extend(sim_times)
            else:
                print("No timing data available for this simulation")
            
            # Show sample asset data for each status
            # print(f"\n--- Sample Asset Data by Status for {sim_name} ---")
            # cursor.execute(f"""SELECT DISTINCT status FROM {table_name} WHERE simulation_name = ? ORDER BY status""", (sim_name,))
            # statuses = [row['status'] for row in cursor.fetchall()]
            
            # for status in statuses:
            #     cursor.execute(f"""
            #         SELECT * FROM {table_name} 
            #         WHERE simulation_name = ? AND status = ? 
            #         LIMIT 1
            #     """, (sim_name, status))
            #     sample_asset = cursor.fetchone()
                
            #     if sample_asset:
            #         print(f"\nStatus: {status}")
            #         print("-" * 40)
            #         for column_name in sample_asset.keys():
            #             value = sample_asset[column_name]
            #             # Format large values for readability
            #             if isinstance(value, str) and len(str(value)) > 50:
            #                 display_value = str(value)[:50] + "..."
            #             else:
            #                 display_value = value
            #             print(f"  {column_name:20}: {display_value}")
        
        # Global timing analysis across all simulations
        if all_total_times:
            print(f"\n{'=' * 80}")
            print("GLOBAL TIMING ANALYSIS ACROSS ALL SIMULATIONS")
            print(f"{'=' * 80}")
            
            total_assets_with_time = len(all_total_times)
            global_avg_time = sum(all_total_times) / total_assets_with_time
            global_min_time = min(all_total_times)
            global_max_time = max(all_total_times)
            
            print(f"Total assets with timing data across all simulations: {total_assets_with_time:,}")
            print(f"Global average total time: {global_avg_time:.2f} seconds")
            print(f"Global minimum total time: {global_min_time:.2f} seconds")
            print(f"Global maximum total time: {global_max_time:.2f} seconds")
            
            # Calculate percentiles for additional insight
            sorted_times = sorted(all_total_times)
            p25_idx = int(len(sorted_times) * 0.25)
            p50_idx = int(len(sorted_times) * 0.50)
            p75_idx = int(len(sorted_times) * 0.75)
            p95_idx = int(len(sorted_times) * 0.95)
            
            print(f"Timing percentiles:")
            print(f"  25th percentile: {sorted_times[p25_idx]:.2f} seconds")
            print(f"  50th percentile (median): {sorted_times[p50_idx]:.2f} seconds")
            print(f"  75th percentile: {sorted_times[p75_idx]:.2f} seconds")
            print(f"  95th percentile: {sorted_times[p95_idx]:.2f} seconds")
            
            # Performance insights
            fast_assets = len([t for t in all_total_times if t < 60])  # Less than 1 minute
            slow_assets = len([t for t in all_total_times if t > 300])  # More than 5 minutes
            
            print(f"\nPerformance breakdown:")
            print(f"  Fast assets (< 1 min): {fast_assets:,} ({fast_assets/total_assets_with_time*100:.1f}%)")
            print(f"  Slow assets (> 5 min): {slow_assets:,} ({slow_assets/total_assets_with_time*100:.1f}%)")
        else:
            print(f"\n{'=' * 80}")
            print("No timing data found across any simulations")
            print(f"{'=' * 80}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error reading database: {e}")
        return False

def main():
    # Default database path from environment or use provided path
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        # Use same path as PowerTwin system
        hpc_shared_storage = os.environ.get('HPC_SHARED_STORAGE', '/project/cowy-ptheory/test')
        db_path = f"{hpc_shared_storage}/powertwin_data/sqlite/powertwin.db"
    
    # Table name from environment or default
    table_name = os.environ.get('PGDATABASE', 'powertwin')
    
    print("PowerTwin SQLite Database Reader")
    print("=" * 40)
    
    success = read_sqlite_db(db_path, table_name)
    
    if not success:
        print("\nUsage:")
        print(f"  python {sys.argv[0]} [database_path]")
        print(f"  Default path: {db_path}")
        sys.exit(1)

if __name__ == "__main__":
    main()