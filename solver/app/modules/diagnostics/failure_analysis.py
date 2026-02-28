"""
Failure analysis script for PowerTwin Solver.
Reads the SQLite database after a run and produces a diagnostic report showing
what failed, why, and any patterns (node correlation, batch correlation, etc.).

Usage:
    python -m app.modules.diagnostics.failure_analysis <db_path> [table_name]
"""

import sqlite3
import sys
import os
from collections import Counter


def analyze_failures(db_path, table_name="powertwin"):
    """Analyze failures in a PowerTwin simulation database."""
    if not os.path.exists(db_path):
        print(f"ERROR: Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Overall status summary
    print("=" * 60)
    print("POWERTWIN FAILURE ANALYSIS REPORT")
    print("=" * 60)
    print(f"Database: {db_path}")
    print()

    cursor = conn.execute(f"SELECT status, COUNT(*) as count FROM {table_name} GROUP BY status")
    status_counts = {row['status']: row['count'] for row in cursor}
    total = sum(status_counts.values())

    print("STATUS SUMMARY:")
    for status, count in sorted(status_counts.items()):
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {status:20s}: {count:5d} ({pct:.1f}%)")
    print(f"  {'TOTAL':20s}: {total:5d}")
    print()

    # Check if failure_reason column exists
    columns_cursor = conn.execute(f"PRAGMA table_info({table_name})")
    columns = [row['name'] for row in columns_cursor]
    has_diagnostics = 'failure_reason' in columns

    if not has_diagnostics:
        print("NOTE: No diagnostic columns (failure_reason, node_name, process_id) found.")
        print("      Deploy the instrumented version to capture failure details.")
        conn.close()
        return

    # Failed assets detail
    failed_cursor = conn.execute(
        f"SELECT asset_id, asset_name, batch, failure_reason, node_name, process_id "
        f"FROM {table_name} WHERE status = 'Failed' ORDER BY batch, asset_id"
    )
    failed_assets = [dict(row) for row in failed_cursor]

    if not failed_assets:
        print("No failed assets found.")
        conn.close()
        return

    # Group by failure reason pattern
    print(f"FAILED ASSETS ({len(failed_assets)} total):")
    print("-" * 60)

    reason_groups = Counter()
    for asset in failed_assets:
        reason = asset.get('failure_reason') or 'Unknown (no failure_reason captured)'
        # Truncate for grouping - take first line
        short_reason = reason.split('\n')[0][:100]
        reason_groups[short_reason] += 1

    print("\nFAILURE REASONS (grouped):")
    for reason, count in reason_groups.most_common():
        print(f"  [{count:3d}x] {reason}")

    # Group by node
    node_groups = Counter()
    for asset in failed_assets:
        node = asset.get('node_name') or 'Unknown'
        node_groups[node] += 1

    if any(a.get('node_name') for a in failed_assets):
        print("\nFAILURES BY NODE:")
        for node, count in node_groups.most_common():
            print(f"  {node:20s}: {count:3d} failures")

    # Group by batch
    batch_groups = Counter()
    for asset in failed_assets:
        batch_groups[asset.get('batch', 'Unknown')] += 1

    print("\nFAILURES BY BATCH:")
    for batch, count in sorted(batch_groups.items()):
        print(f"  Batch {str(batch):5s}: {count:3d} failures")

    # Individual failed assets
    print("\nFAILED ASSET DETAILS:")
    print("-" * 60)
    for asset in failed_assets:
        print(f"  Asset {asset['asset_id']} ({asset.get('asset_name', 'N/A')}) "
              f"- Batch {asset.get('batch', '?')} - Node {asset.get('node_name', '?')}")
        if asset.get('failure_reason'):
            # Print first 200 chars of failure reason, indented
            reason = asset['failure_reason'][:200]
            for line in reason.split('\n'):
                print(f"    {line}")
        print()

    conn.close()
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.modules.diagnostics.failure_analysis <db_path> [table_name]")
        sys.exit(1)

    db_path = sys.argv[1]
    table_name = sys.argv[2] if len(sys.argv) > 2 else "powertwin"
    analyze_failures(db_path, table_name)
