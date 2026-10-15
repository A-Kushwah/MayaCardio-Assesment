#!/usr/bin/env python3
"""Standalone script to run pipeline in online mode for 1 hour and collect metrics.

Usage:
    python tools/run_metrics.py [--duration SEC] [--output DIR]

Collects CPU, memory, disk, and network metrics every second and exports to CSV/JSON.
"""

import sys
import time
import argparse
import os
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

try:
    from metrics import MetricsCollector
except ImportError as e:
    print(f"Error: Could not import MetricsCollector: {e}", file=sys.stderr)
    print("Make sure psutil is installed: pip install psutil", file=sys.stderr)
    sys.exit(1)


def run_metrics_collection(duration_sec: int = 3600, output_dir: Path = None) -> None:
    """Run pipeline in online mode and collect metrics for specified duration.

    Args:
        duration_sec: Collection duration in seconds (default 3600 = 1 hour)
        output_dir: Output directory for CSV and JSON (default: current directory)
    """
    if output_dir is None:
        output_dir = Path.cwd()
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Starting metrics collection for {duration_sec} seconds ({duration_sec/3600:.1f} hours)...")
    print(f"Output directory: {output_dir.absolute()}")

    collector = MetricsCollector(interval_sec=1.0)
    collector.start()

    try:
        start_time = time.time()
        sample_count = 0

        while time.time() - start_time < duration_sec:
            # Collect one sample
            sample = collector.sample()
            sample_count += 1

            # Print progress every 60 samples (every minute)
            if sample_count % 60 == 0:
                elapsed = time.time() - start_time
                remaining = duration_sec - elapsed
                print(f"  [{sample_count} samples, {elapsed:.1f}s elapsed, {remaining:.1f}s remaining]")

            # Sleep for interval (minus time spent sampling)
            sleep_time = collector.interval_sec - (time.time() - sample['timestamp'])
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\nCollection interrupted by user")
    finally:
        collector.stop()

    # Export results
    timestamp = int(time.time())
    csv_file = output_dir / f"metrics_{timestamp}.csv"
    json_file = output_dir / f"metrics_{timestamp}.json"

    print(f"\nExporting metrics...")
    collector.export_csv(csv_file)
    collector.export_json(json_file)

    print(f"  CSV: {csv_file}")
    print(f"  JSON: {json_file}")

    # Print summary
    collector.print_summary()


def main():
    parser = argparse.ArgumentParser(
        description="Collect system metrics during pipeline online mode execution.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tools/run_metrics.py
    (Collect for 1 hour, save to current directory)

  python tools/run_metrics.py --duration 300 --output ./results
    (Collect for 5 minutes, save to ./results directory)
        """,
    )

    parser.add_argument(
        '--duration',
        type=int,
        default=3600,
        help='Collection duration in seconds (default: 3600 = 1 hour)',
    )
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output directory for CSV and JSON files (default: current directory)',
    )

    args = parser.parse_args()

    if args.duration <= 0:
        print("Error: --duration must be positive", file=sys.stderr)
        sys.exit(1)

    try:
        run_metrics_collection(duration_sec=args.duration, output_dir=args.output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
