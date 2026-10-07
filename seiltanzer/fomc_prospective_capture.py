"""Bounded recent official receipts for immutable native FOMC ingestion.

Capture is not local materialization, a first-published HTML vintage, a forecast,
or consensus. Failed newest acquisition rejects the pair rather than falling back.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import threading
import time

from .edge_family_sources import SourceBudget, fetch_public
from .macro_fomc_deterministic_bootstrap import (
    FOMCStatementSpec, INDEX_TEMPLATE, extract_statement_text, parse_fomc_index,
    parse_release_timestamp,
)
from .runtime_git_identity import runtime_git_sha

CONTRACT = 'fomc-prospective-offhost-capture-v1'
MAX_BODY_BYTES = 200_000
MAX_CAPTURE_BYTES = 950_000
MAX_AGE_SEC = 3600.
PUBLICATION_FIELDS = {'publication_contract_version', 'published_for_sha', 'publication_run_id'}


def capture_digest(capture):
    value = {k: v for k, v in capture.items() if k not in PUBLICATION_FIELDS | {'capture_sha256'}}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('FOMC_CAPTURE_CLOCK_INVALID')
    return float(value)


def _sha(value):
    if not isinstance(value, str) or len(value) != 40 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('FOMC_CAPTURE_CODE_SHA_INVALID')
    return value


def build_capture(*, expected_sha, fetch=fetch_public, clock=time.time, budget=None):
    expected_sha = _sha(expected_sha)
    budget = budget or SourceBudget(seconds=60)
    initial_requests, initial_bytes = budget.requests, budget.body_bytes
    schedules, specs = [], {}

    def receive(address):
        if budget.requests - initial_requests >= 4:
            raise ValueError('FOMC_CAPTURE_REQUEST_BOUND')
        budget.reserve('www.federalreserve.gov')
        body = fetch(address, timeout=min(12., budget.remaining()),
                     max_bytes=MAX_BODY_BYTES, budget=budget)
        if fetch is not fetch_public:
            budget.accept_body(len(body))
        receipt = _number(clock())
        if budget.remaining() <= 0:
            raise ValueError('FOMC_CAPTURE_DEADLINE')
        if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY_BYTES:
            raise ValueError('FOMC_CAPTURE_BODY_BOUND')
        return {'html': body.decode('utf-8'), 'fetched_at': receipt,
                'body_sha256': hashlib.sha256(body).hexdigest()}

    year = datetime.fromtimestamp(_number(clock()), timezone.utc).year
    for candidate in (year, year-1):
        row = receive(INDEX_TEMPLATE.format(year=candidate))
        row.update(year=candidate, source_url=INDEX_TEMPLATE.format(year=candidate))
        schedules.append(row)
        specs.update({s.date_code: s for s in parse_fomc_index(row['html'])})
        if len(specs) >= 2:
            break
    if len(specs) < 2:
        raise ValueError('FOMC_CAPTURE_EXACT_PAIR_MISSING')
    records = []
    previous = None
    for spec in sorted(specs.values(), key=lambda s: s.date_code)[-2:]:
        row = receive(spec.source_url)
        row.update(spec=asdict(spec), previous_source_url=previous,
                   published_at=parse_release_timestamp(row['html'], date_code=spec.date_code))
        extract_statement_text(row['html'])
        records.append(row)
        previous = spec.source_url
    captured = _number(clock())
    capture = dict(contract_version=CONTRACT, code_sha=expected_sha, captured_ts=captured,
        production_authority=False, source_vintage_guarantee='OFFICIAL_DATED_PAGE_NOT_VERSIONED',
        schedules=schedules, records=records,
        collection_limits={'requests': budget.requests-initial_requests,
            'body_bytes_received': budget.body_bytes-initial_bytes,
            'max_requests': 4, 'max_body_bytes': MAX_BODY_BYTES, 'deadline_sec': 60})
    capture['capture_sha256'] = capture_digest(capture)
    return validate_capture(capture, expected_sha=expected_sha, now=captured)


def validate_capture(capture, *, expected_sha, now):
    expected_sha = _sha(expected_sha)
    if (not isinstance(capture, dict) or capture.get('contract_version') != CONTRACT
            or capture.get('production_authority') is not False or capture.get('code_sha') != expected_sha):
        raise ValueError('FOMC_CAPTURE_CONTRACT_OR_SHA_MISMATCH')
    if len(json.dumps(capture, ensure_ascii=False).encode()) > MAX_CAPTURE_BYTES:
        raise ValueError('FOMC_CAPTURE_BYTE_BOUND')
    captured = _number(capture.get('captured_ts'))
    if not 0 <= _number(now) - captured <= MAX_AGE_SEC:
        raise ValueError('FOMC_CAPTURE_STALE_OR_AFTER_NOW')
    if capture.get('capture_sha256') != capture_digest(capture):
        raise ValueError('FOMC_CAPTURE_DIGEST_MISMATCH')
    schedules, records = capture.get('schedules'), capture.get('records')
    if not isinstance(schedules, list) or not 1 <= len(schedules) <= 2 or not isinstance(records, list) or len(records) != 2:
        raise ValueError('FOMC_CAPTURE_RECORD_BOUND')
    specs, total_bytes = {}, 0
    for row in schedules + records:
        html = row.get('html')
        if not isinstance(html, str) or not 0 < len(html.encode()) <= MAX_BODY_BYTES:
            raise ValueError('FOMC_CAPTURE_BODY_BOUND')
        total_bytes += len(html.encode())
        if hashlib.sha256(html.encode()).hexdigest() != row.get('body_sha256'):
            raise ValueError('FOMC_CAPTURE_BODY_HASH_MISMATCH')
        if _number(row.get('fetched_at')) > captured:
            raise ValueError('FOMC_CAPTURE_RECEIPT_AFTER_CAPTURE')
    year = datetime.fromtimestamp(captured, timezone.utc).year
    years = [row.get('year') for row in schedules]
    if years not in ([year], [year, year-1]):
        raise ValueError('FOMC_CAPTURE_INDEX_YEAR_INVALID')
    for row in schedules:
        if row.get('source_url') != INDEX_TEMPLATE.format(year=row['year']):
            raise ValueError('FOMC_CAPTURE_INDEX_URL_INVALID')
        specs.update({s.date_code: s for s in parse_fomc_index(row['html'])})
    selected = sorted(specs.values(), key=lambda s: s.date_code)[-2:]
    if len(selected) != 2:
        raise ValueError('FOMC_CAPTURE_EXACT_PAIR_MISSING')
    prior = None
    for row, spec in zip(records, selected):
        if row.get('spec') != asdict(spec) or row.get('previous_source_url') != prior:
            raise ValueError('FOMC_CAPTURE_PREDECESSOR_OR_INDEX_MISMATCH')
        published = parse_release_timestamp(row['html'], date_code=spec.date_code)
        if published != _number(row.get('published_at')) or published > row['fetched_at']:
            raise ValueError('FOMC_CAPTURE_PUBLICATION_INVALID')
        extract_statement_text(row['html'])
        prior = spec.source_url
    limits = capture.get('collection_limits') or {}
    if limits.get('requests') != len(schedules)+2 or limits.get('body_bytes_received') != total_bytes:
        raise ValueError('FOMC_CAPTURE_ACCOUNTING_INVALID')
    return capture


def ingest_capture(store, capture, *, expected_sha, now):
    validate_capture(capture, expected_sha=expected_sha, now=now)
    stored, skipped = 0, 0
    # All transport data is checked before any native write. Local created_ts is
    # assigned by the existing immutable store, never taken from runner time.
    for row in capture['records']:
        result = store.ingest(FOMCStatementSpec(**row['spec']), html=row['html'],
            previous_source_url=row['previous_source_url'], fetched_at=row['fetched_at'])
        if result['status'] == 'CACHED':
            skipped += 1
        else:
            stored += 1
    return {'status': 'OK', 'stored': stored, 'skipped': skipped,
            'capture_sha256': capture['capture_sha256'], 'network_calls': False,
            'production_authority': False}


def prospective_admission_reason(store, cutoff):
    """Constant-time request guard; never reads a file, acquires DB, or fetches."""
    state = getattr(store, 'prospective_capture_state', None)
    return _state_admission_reason(state, cutoff)


def _state_admission_reason(state, cutoff):
    if state is None:
        return None  # Existing offline readers without prospective configuration.
    if state.get('status') != 'OK':
        return state.get('reason', 'FOMC_PROSPECTIVE_CAPTURE_UNAVAILABLE')
    if not state['materialized_at'] <= cutoff <= state['captured_ts'] + MAX_AGE_SEC:
        return 'FOMC_PROSPECTIVE_CAPTURE_STALE_OR_AFTER_SNAPSHOT'
    return None


class ProspectiveFOMCRuntime:
    """Minute local-file reader; official acquisition stays on GitHub runners."""
    def __init__(self, store, path, *, code_sha=runtime_git_sha, clock=time.time):
        self.store, self.path, self.code_sha, self.clock = store, Path(path), code_sha, clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self.store.prospective_capture_state = {'status': 'UNAVAILABLE',
            'reason': 'FOMC_PROSPECTIVE_CAPTURE_NOT_LOADED'}

    def refresh(self):
        if not self._lock.acquire(blocking=False):
            return {'status': 'IN_PROGRESS'}
        self.store.prospective_capture_state = {'status': 'IN_PROGRESS',
            'reason': 'FOMC_PROSPECTIVE_CAPTURE_MATERIALIZING'}
        try:
            with self.path.open('rb') as handle:
                raw = handle.read(MAX_CAPTURE_BYTES+1)
            if not raw or len(raw) > MAX_CAPTURE_BYTES:
                raise ValueError('FOMC_CAPTURE_BYTE_BOUND')
            capture = json.loads(raw)
            sha = self.code_sha()
            if (not isinstance(capture, dict)
                    or capture.get('publication_contract_version') != 'active-edge-exact-sha-publication-v1'
                    or capture.get('published_for_sha') != sha):
                raise ValueError('FOMC_CAPTURE_PUBLICATION_SHA_MISMATCH')
            result = ingest_capture(self.store, capture, expected_sha=sha, now=self.clock())
            result.update(captured_ts=capture['captured_ts'], materialized_at=self.clock(),
                          published_for_sha=capture['published_for_sha'])
            self.store.prospective_capture_state = result
            return result
        except Exception as exc:
            result = {'status': 'UNAVAILABLE', 'reason': 'FOMC_PROSPECTIVE_CAPTURE_REJECTED',
                'detail': type(exc).__name__ + ':' + str(exc)[:160], 'network_calls': False}
            self.store.prospective_capture_state = result
            return result
        finally:
            self._lock.release()

    def status(self):
        """Read one in-memory sample; ingestion success is not current admission."""
        state = dict(self.store.prospective_capture_state)
        cutoff = self.clock()
        reason = _state_admission_reason(state, cutoff)
        return {**state, 'checked_at': cutoff,
                'admissible_for_new_snapshot': reason is None,
                'admission_reason': reason, 'sqlite_access': False,
                'network_calls': False}

    def _run(self):
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(60)

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name='fomc-prospective-local')
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
