#!/usr/bin/env python3
"""Consolidate cleaned sensor report CSVs into a single master CSV."""

import argparse
import os
import shutil
from multiprocessing import Pool, cpu_count
from pathlib import Path
import pandas as pd


def process_worker(args):
    """Process a slice of sensor directories and write to a temp file."""
    worker_id, sensor_dirs, collection_id, resample, types, temp_dir = args

    temp_path = os.path.join(temp_dir, f'worker_{worker_id}.csv')
    header_written = False
    total_rows = 0

    with open(temp_path, 'w') as out_f:
        for sensor_dir in sensor_dirs:
            for csv_path in sensor_dir.glob('*.csv'):
                if types and not any(csv_path.stem.endswith(t) for t in types):
                    continue
                try:
                    df = pd.read_csv(csv_path, parse_dates=['ts'])
                except Exception:
                    continue

                if 'value' not in df.columns:
                    continue

                sensor_id = df['id'].iloc[0] if 'id' in df.columns else sensor_dir.name

                if df['ts'].dt.tz is not None:
                    df['ts'] = df['ts'].dt.tz_convert(None)

                # EnergyPlus uses end-of-period timestamps (hour ending at 01:00,
                # month ending Feb 1 for January). Shift back 1 second to convert
                # to start-of-period so grouping never leaks across boundaries,
                # then floor to the native resolution for clean timestamps.
                if len(df) >= 2:
                    native_freq = df['ts'].diff().median()
                else:
                    native_freq = pd.Timedelta(hours=1)
                df['ts'] = df['ts'] - pd.Timedelta(seconds=1)
                if native_freq >= pd.Timedelta(days=27):
                    df['ts'] = df['ts'].dt.to_period('M').dt.to_timestamp()
                else:
                    df['ts'] = df['ts'].dt.floor(native_freq)

                if resample:
                    df = (df.groupby(df['ts'].dt.to_period(resample))['value']
                          .sum()
                          .reset_index())
                    if resample == 'M':
                        df['ts'] = df['ts'].dt.to_timestamp().dt.normalize() + pd.Timedelta(hours=12)
                    else:
                        df['ts'] = df['ts'].dt.to_timestamp()

                df['sensor_id'] = sensor_id
                df['collection_id'] = collection_id
                df['metadata'] = '{}'
                df = df[['sensor_id', 'collection_id', 'ts', 'value', 'metadata']]
                df = df.drop_duplicates(subset=['sensor_id', 'collection_id', 'ts'], keep='first')
                df.to_csv(out_f, index=False, header=not header_written)
                header_written = True
                total_rows += len(df)

    return worker_id, total_rows


def main():
    parser = argparse.ArgumentParser(description='Consolidate sensor logs into master CSV')
    parser.add_argument('--input-dir', required=True, help='Path to cleaned_reports directory')
    parser.add_argument('--output', required=True, help='Output CSV path')
    parser.add_argument('--collection-id', type=int, default=1, help='Collection ID (default: 1)')
    parser.add_argument('--resample', default='', help='Resample period (e.g. M for monthly, H for hourly). Default: no resampling')
    parser.add_argument('--workers', type=int, default=0, help='Number of workers (default: cpu_count)')
    parser.add_argument('--types', default='', help='Comma-separated sensor type suffixes to include (e.g. electricity,natural_gas). Default: all')
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f'ERROR: {input_dir} is not a directory')
        return

    sensor_dirs = sorted([d for d in input_dir.iterdir() if d.is_dir()])
    print(f'Found {len(sensor_dirs)} sensor directories')

    workers = args.workers if args.workers > 0 else cpu_count()
    print(f'Using {workers} workers')

    resample = args.resample.strip() if args.resample else ''
    if resample:
        print(f'Resampling: {resample}')
    else:
        print('No resampling (raw passthrough)')

    types = [t.strip() for t in args.types.split(',') if t.strip()] if args.types else []
    if types:
        print(f'Filtering to types: {types}')

    temp_dir = os.path.join(os.path.dirname(args.output), f'consolidation_tmp_{os.getpid()}')
    os.makedirs(temp_dir, exist_ok=True)

    chunks = [[] for _ in range(workers)]
    for i, d in enumerate(sensor_dirs):
        chunks[i % workers].append(d)

    work_items = [(i, chunk, args.collection_id, resample, types, temp_dir)
                  for i, chunk in enumerate(chunks) if chunk]

    try:
        total_rows = 0
        with Pool(len(work_items)) as pool:
            for worker_id, rows in pool.imap_unordered(process_worker, work_items):
                total_rows += rows
                print(f'Worker {worker_id} finished ({rows} rows)')

        print(f'All workers done. Total rows: {total_rows}')
        print(f'Concatenating worker files...')

        with open(args.output, 'wb') as out_f:
            for i in range(len(work_items)):
                temp_path = os.path.join(temp_dir, f'worker_{i}.csv')
                if not os.path.exists(temp_path):
                    continue
                with open(temp_path, 'rb') as in_f:
                    if i == 0:
                        shutil.copyfileobj(in_f, out_f)
                    else:
                        in_f.readline()
                        shutil.copyfileobj(in_f, out_f)

        print(f'Written to {args.output}')
        print(f'Final size: {os.path.getsize(args.output) / (1024**3):.2f} GB')

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f'Cleaned up temp directory')


if __name__ == '__main__':
    main()
