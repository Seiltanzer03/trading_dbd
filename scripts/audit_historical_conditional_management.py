#!/usr/bin/env python3
"""Print the read-only six-hypothesis management replay from an existing DB."""
import argparse
import json
from seiltanzer.historical_conditional_audit import audit_database

parser = argparse.ArgumentParser()
parser.add_argument("--database", required=True)
args = parser.parse_args()
print(json.dumps(audit_database(args.database), ensure_ascii=False, sort_keys=True))
