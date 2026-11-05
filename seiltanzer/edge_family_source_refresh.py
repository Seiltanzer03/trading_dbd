"""Bounded refresh of existing public facts; no models, orders or request I/O."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace

from .config import ALL_INSTRUMENTS
from .edge_family_source_runtime import CONTRACT,POLICY,PUBLICATION,MAX_BYTES
from .runtime_git_identity import runtime_git_sha

INTERVAL_SEC=300


def _stamp(value):
    return (float(value) if isinstance(value,(int,float)) and not isinstance(value,bool)
            and math.isfinite(value) else None)


def _valid(payload,sha,now):
    if not isinstance(payload,dict):return False
    capture=_stamp(payload.get('captured_ts'))
    return (payload.get('contract_version')==CONTRACT and payload.get('edge_policy')==POLICY
        and payload.get('production_authority') is False and type(payload.get('models_produced')) is int
        and payload['models_produced']==0 and capture is not None and 0<capture<=now
        and set(payload.get('instruments') or {})==set(ALL_INSTRUMENTS)
        and payload.get('published_for_sha',sha)==sha)


class FamilySourceRefresher:
    def __init__(self,data_dir,*,expected_sha,clock=time.time,sha_reader=runtime_git_sha,enabled=True):
        self.root=Path(data_dir)/'research';self.expected_sha=expected_sha
        self.clock=clock;self.sha_reader=sha_reader;self._lock=threading.Lock()
        self.enabled=enabled
        self._attempt=None;self._status={'state':'PENDING' if enabled else 'DISABLED','runtime_fitting':False}

    def status(self):return dict(self._status)

    def _document(self,name,now):
        path=self.root/name
        try:
            stat=path.stat()
            if not 0<stat.st_size<=MAX_BYTES or max(stat.st_mtime,stat.st_ctime)>now:return None
            with path.open('rb') as handle:raw=handle.read(MAX_BYTES+1)
            if not 0<len(raw)<=MAX_BYTES:return None
            payload=json.loads(raw)
            if (not _valid(payload,self.expected_sha,now)
                    or payload.get('published_for_sha')!=self.expected_sha
                    or payload.get('publication_contract_version')!=PUBLICATION):return None
            return payload
        except (OSError,ValueError,TypeError):return None

    def refresh(self):
        if not self.enabled:return
        if not self._lock.acquire(blocking=False):return
        temporary=None
        try:
            now=_stamp(self.clock())
            sha=self.expected_sha
            if (now is None or not isinstance(sha,str) or len(sha)!=40
                    or any(c not in '0123456789abcdef' for c in sha) or self.sha_reader()!=sha):
                raise ValueError('SOURCE_RUNTIME_GENERATION_INVALID')
            documents=[doc for name in ('edge_family_sources_latest.json','edge_family_sources_runtime.json')
                       if (doc:=self._document(name,now)) is not None]
            if any(0<=now-doc['captured_ts']<INTERVAL_SEC for doc in documents):
                self._status={'state':'FRESH_PUBLICATION','network_calls':False,'runtime_fitting':False};return
            if self._attempt is not None and now-self._attempt<INTERVAL_SEC:return
            self._attempt=now
            from .edge_family_sources import build_bundle,SourceBudget
            previous=max(documents,key=lambda doc:doc['captured_ts']) if documents else {}
            # This is a new observation window, not an ever-growing archive.
            # The independently admitted off-host document retains baseline facts.
            payload=build_bundle(clock=self.clock,previous=previous,
                                 budget=SourceBudget(seconds=45.))
            finished=_stamp(self.clock())
            if (finished is None or not _valid(payload,sha,finished)
                    or not any(isinstance(row,dict) and row.get('fetch_status')=='FETCHED'
                               for row in (payload.get('raw_sources') or {}).values())):
                raise ValueError('SOURCE_RUNTIME_RESULT_INVALID')
            payload.update(publication_contract_version=PUBLICATION,published_for_sha=sha,
                           publication_run_id='runtime-'+uuid.uuid4().hex)
            raw=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            if not 0<len(raw)<=MAX_BYTES:raise ValueError('SOURCE_RUNTIME_BYTE_BOUND')
            self.root.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.root,prefix='.family-source-',suffix='.tmp',delete=False) as handle:
                temporary=Path(handle.name);handle.write(raw);handle.flush();os.fsync(handle.fileno())
            # HTTP200/parsed empty bodies are not a usable replacement. Apply
            # the actual consumer's unchanged source/clock/selected-byte gates.
            from .edge_family_source_runtime import _load_one
            cutoff=_stamp(self.clock())
            owner=SimpleNamespace(settings=SimpleNamespace(data_dir=self.root.parent))
            if cutoff is None or not any(
                    _load_one(owner,{'captured_ts':cutoff,'strategy':{'instrument':code}},
                              sha,temporary.name)['edge_family_source_bundle_audit'].get('source_record_n',0)>0
                    for code in ALL_INSTRUMENTS):
                raise ValueError('SOURCE_RUNTIME_NO_ADMISSIBLE_FACTS')
            if self.sha_reader()!=sha:raise ValueError('SOURCE_RUNTIME_GENERATION_CHANGED')
            os.replace(temporary,self.root/'edge_family_sources_runtime.json');temporary=None
            self._status={'state':'PUBLISHED','captured_ts':payload['captured_ts'],'bytes':len(raw),
                          'network_calls':True,'runtime_fitting':False,'last_attempt_ts':now}
        except Exception as exc:
            self._status={'state':'FAILED','reason':type(exc).__name__,'last_attempt_ts':self._attempt,
                          'runtime_fitting':False}
        finally:
            if temporary is not None:
                try:temporary.unlink()
                except OSError:pass
            self._lock.release()
