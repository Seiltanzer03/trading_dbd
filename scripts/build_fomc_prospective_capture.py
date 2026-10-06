#!/usr/bin/env python3
"""Build a bounded recent official FOMC capture off-host; no production writes."""
from __future__ import annotations

import argparse
from pathlib import Path
import time

from seiltanzer.fomc_prospective_capture import build_capture
from seiltanzer.macro_offhost_bundle import write_bundle
from seiltanzer.runtime_git_identity import runtime_git_sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if runtime_git_sha() != args.expected_sha:
        parser.error('FOMC_CAPTURE_BUILDER_SHA_MISMATCH')
    try:
        capture = build_capture(expected_sha=args.expected_sha)
    except Exception as exc:
        # Publish refusal as well as success: retaining yesterday's accepted
        # file after a known acquisition failure must not keep its live vote.
        write_bundle(args.output, {'contract_version': 'fomc-prospective-offhost-capture-v1',
            'code_sha': args.expected_sha, 'captured_ts': time.time(),
            'production_authority': False, 'status': 'UNAVAILABLE',
            'reason': type(exc).__name__ + ':' + str(exc)[:160]})
        raise SystemExit('FOMC_CAPTURE_ACQUISITION_FAILED') from exc
    write_bundle(args.output, capture)
    print('FOMC_CAPTURE_BUILT', capture['capture_sha256'],
          'requests=' + str(capture['collection_limits']['requests']))


if __name__ == '__main__':
    main()
