#!/usr/bin/env python3
"""Audit report of all configured instruments and 8 families using frozen outputs."""
import argparse
import json
import sys
from pathlib import Path

from seiltanzer.edge_family_adapters import FAMILIES


def main():
    parser = argparse.ArgumentParser(description="Audit Readiness Matrix")
    parser.add_argument("--input", type=Path, default=Path("edge_family_sources_latest.json"),
                        help="Path to the frozen JSON bundle")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: Bundle file '{args.input}' not found.", file=sys.stderr)
        print("Please run 'python -m scripts.build_edge_family_sources --output edge_family_sources_latest.json' first.", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        bundle = json.load(f)

    instruments = bundle.get("instruments", {})
    captured_ts = bundle.get("captured_ts")
    
    print(f"=== Unified Edge Readiness Matrix ===")
    print(f"Captured TS: {captured_ts}")
    print("=" * 120)
    print(f"{'Instrument':<12} | {'Family':<15} | {'Ready':<7} | {'Eligible':<8} | {'Missing Fields / Reason'}")
    print("-" * 120)

    for code in sorted(instruments.keys()):
        data = instruments[code]
        readiness = data.get("readiness", {})
        for family in FAMILIES:
            fam_data = readiness.get(family, {})
            needs_data = fam_data.get("needs_data", [])
            forecast_avail = fam_data.get("forecast_available", False)
            
            ready = len(needs_data) == 0
            reason = ", ".join(needs_data) if needs_data else "All features present"
            
            print(f"{code:<12} | {family:<15} | {str(ready):<7} | {str(forecast_avail):<8} | {reason}")

if __name__ == "__main__":
    main()
