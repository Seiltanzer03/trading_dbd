"""Production plan pins and physical reserve are prerequisites, never authority."""
import copy
import hashlib
import importlib
import json
import os
from types import SimpleNamespace

import pytest

from test_sqlite_history_transaction import setup_transaction
from test_history_image_transport import image


def production_plan(module,setup):
    _,old,_,_,original,hot = setup
    manifest = dict(old['hot_manifest'])
    manifest.update(module.CATALOG_PINS)
    receipts = {kind:dict(image(raw,kind)[0]['receipt'],full_get_verified=True)
                for kind,raw in (('original',original),('hot',hot))}
    return dict(contract=module.CONTRACT,mode='FENCED_PRODUCTION_ONLY',run_id='run-001',
        application_sha='a'*40,original=old['original'],hot=old['hot'],hot_manifest=manifest,
        artifact_receipts=receipts)


def test_plan_freezes_public_pins_without_private_urls(setup_transaction):
    module = importlib.import_module('scripts.history_production_plan')
    plan = production_plan(module,setup_transaction)
    raw = module.validate_plan(plan,run_id='run-001',application_sha='a'*40,
                               frozen_identity=setup_transaction[1]['original'])
    frozen = json.loads(raw)
    plan['hot']['database_sha256'] = 'f'*64
    assert frozen['hot']['database_sha256'] == hashlib.sha256(setup_transaction[-1]).hexdigest()
    assert b'PRIVATE-' not in raw


@pytest.mark.parametrize('hazard',['source','application','run','catalog','manifest_hot','false_receipt',
                                  'object_key','mixed_raw','private_url','extra','disposable_mode'])
def test_invalid_production_epoch_or_image_pin_is_refused(setup_transaction,hazard):
    module = importlib.import_module('scripts.history_production_plan')
    plan = production_plan(module,setup_transaction)
    if hazard == 'source':plan['original']['database_sha256'] = 'f'*64
    elif hazard == 'application':plan['application_sha'] = 'b'*40
    elif hazard == 'run':plan['run_id'] = 'run-002'
    elif hazard == 'catalog':plan['hot_manifest']['cold_history_catalog_sha256'] = 'f'*64
    elif hazard == 'manifest_hot':plan['hot_manifest']['database_sha256'] = 'f'*64
    elif hazard == 'false_receipt':plan['artifact_receipts']['original']['full_get_verified'] = 1
    elif hazard == 'object_key':plan['artifact_receipts']['original']['object_key'] = 'backups/v1/daily-slot-6/snapshot.sqlite3.gz'
    elif hazard == 'mixed_raw':plan['artifact_receipts']['original']['database_sha256'] = 'f'*64
    elif hazard == 'private_url':plan['artifact_receipts']['original']['url'] = 'https://secret.invalid/?PRIVATE'
    elif hazard == 'disposable_mode':plan['mode'] = 'DISPOSABLE_ONLY'
    else:plan['extra'] = True
    with pytest.raises(ValueError):
        module.validate_plan(plan,run_id='run-001',application_sha='a'*40,
                             frozen_identity=setup_transaction[1]['original'])


def test_sparse_original_cannot_manufacture_reclaimable_physical_bytes(tmp_path,monkeypatch):
    module = importlib.import_module('scripts.history_production_plan')
    source = tmp_path/'sparse.sqlite3'
    with source.open('wb') as stream:
        stream.write(b'original');stream.truncate(64*1024**2)
    free = module.MIN_FREE_BYTES-1
    monkeypatch.setattr(module.os,'statvfs',lambda _:SimpleNamespace(f_bavail=free,f_frsize=1))
    result = module.capacity_before_transfer(source,32*1024**2)
    assert result['admitted'] is False
    assert result['source_reclaimable_bytes'] == min(source.stat().st_size,source.stat().st_blocks*512)
    assert result['source_reclaimable_bytes'] < source.stat().st_size


@pytest.mark.parametrize('delta,admitted',[(-1,False),(0,True),(1,True)])
def test_actual_post_transfer_reserve_matches_existing_disk_gate(tmp_path,monkeypatch,delta,admitted):
    module = importlib.import_module('scripts.history_production_plan')
    from seiltanzer.background_disk_admission import MIN_FREE_BYTES
    assert module.MIN_FREE_BYTES == MIN_FREE_BYTES
    monkeypatch.setattr(module.os,'statvfs',lambda _:SimpleNamespace(f_bavail=MIN_FREE_BYTES+delta,f_frsize=1))
    result = module.capacity_after_transfer(tmp_path/'trades.db')
    assert result['admitted'] is admitted
    assert result['minimum_free_bytes'] == MIN_FREE_BYTES
