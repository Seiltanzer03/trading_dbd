"""Strict initial production pins and physical reserve; grants no ownership."""
import copy
import os
from pathlib import Path
import re

from scripts import history_image_transport as images
from scripts import sqlite_history_transaction as core
from scripts.history_storage_loader import history_state
from scripts.sqlite_history_partition import _identity

CONTRACT = 'sqlite-history-production-plan-v1'
MIN_FREE_BYTES = 1024**3+32*1024**2
CATALOG_SHA256 = '7b78d25d13f249f1ab967828675419a6656fe6b69787e5b78644076a54411a63'
CATALOG_SOURCE = 'd6e8c423f21585f68cd28b0d837eb08c944d2b273bbba59ad963927e33434535'
CATALOG_PINS = dict(cold_history_catalog_sha256=CATALOG_SHA256,
    cold_history_catalog_bucket=images.BUCKET,
    cold_history_catalog_object_key=history_state.catalog_key(CATALOG_SOURCE,CATALOG_SHA256))


def validate_plan(plan, *, run_id, application_sha, frozen_identity):
    """Validate public pins only; owner must independently verify actual GETs."""
    plan = copy.deepcopy(plan)
    if (type(plan) is not dict or set(plan) != {'contract','mode','run_id','application_sha',
            'original','hot','hot_manifest','artifact_receipts'}
            or plan['contract'] != CONTRACT or plan['mode'] != 'FENCED_PRODUCTION_ONLY'
            or type(run_id) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',run_id)
            or type(application_sha) is not str or not re.fullmatch('[0-9a-f]{40}',application_sha)
            or plan['run_id'] != run_id or plan['application_sha'] != application_sha):
        raise core.CutoverError('INVALID_PRODUCTION_PLAN_EPOCH')
    core._valid_identity(frozen_identity)
    for kind in ('original','hot'):
        core._valid_identity(plan[kind])
    if plan['original'] != frozen_identity:
        raise core.CutoverError('FROZEN_SOURCE_IDENTITY_MISMATCH')
    if plan['hot']['database_size_bytes'] >= plan['original']['database_size_bytes']:
        raise core.CutoverError('HOT_IMAGE_NOT_SMALLER')
    manifest = plan['hot_manifest']
    if (type(manifest) is not dict or set(manifest) != core._IDENTITY_FIELDS.union(history_state.PIN_FIELDS)
            or any(manifest.get(key) != value for key,value in plan['hot'].items())
            or any(manifest.get(key) != value for key,value in CATALOG_PINS.items())
            or type(manifest.get('sqlite_history_state_sha256')) is not str
            or not re.fullmatch('[0-9a-f]{64}',manifest['sqlite_history_state_sha256'])):
        raise core.CutoverError('INVALID_PRODUCTION_HOT_MANIFEST')
    receipts = plan['artifact_receipts']
    if type(receipts) is not dict or set(receipts) != {'original','hot'}:
        raise core.CutoverError('MISSING_PRODUCTION_IMAGE_RECEIPTS')
    for kind,receipt in receipts.items():
        if (type(receipt) is not dict or set(receipt) != images.FIELDS.union({'full_get_verified'})
                or receipt.get('full_get_verified') is not True
                or receipt.get('contract') != images.CONTRACT or receipt.get('kind') != kind
                or receipt.get('run_id') != run_id or receipt.get('bucket') != images.BUCKET
                or receipt.get('codec') != 'gzip'
                or any(receipt.get(key) != value for key,value in plan[kind].items())
                or type(receipt.get('compressed_sha256')) is not str
                or not re.fullmatch('[0-9a-f]{64}',receipt['compressed_sha256'])
                or type(receipt.get('compressed_size_bytes')) is not int
                or not 0 < receipt['compressed_size_bytes'] <= images.MAX_BYTES):
            raise core.CutoverError('INVALID_PRODUCTION_IMAGE_RECEIPT')
        key = (f'research/sqlite-cutover/v1/{run_id}/{kind}/{receipt["database_sha256"]}/'
               f'{receipt["compressed_sha256"]}.sqlite3.gz')
        if receipt['object_key'] != key:
            raise core.CutoverError('PRODUCTION_IMAGE_OBJECT_KEY_MISMATCH')
    return core._canonical(plan)


def _filesystem(database):
    vfs = os.statvfs(Path(database).parent)
    if (type(vfs.f_bavail) is not int or vfs.f_bavail < 0
            or type(vfs.f_frsize) is not int or not 0 < vfs.f_frsize <= 1024**2):
        raise core.CutoverError('INVALID_FILESYSTEM_CAPACITY')
    return vfs.f_bavail*vfs.f_frsize, vfs.f_frsize


def capacity_before_transfer(database, hot_size_bytes):
    """Conservative estimate only; actual post-transfer reserve is mandatory."""
    database = Path(database)
    _identity(database)
    info = database.stat()
    if type(hot_size_bytes) is not int or not 4096 <= hot_size_bytes < info.st_size:
        raise core.CutoverError('INVALID_HOT_PHYSICAL_SIZE')
    free, block = _filesystem(database)
    reclaimable = min(info.st_size,info.st_blocks*512)
    hot_rounded = ((hot_size_bytes+block-1)//block)*block
    predicted = free+reclaimable-hot_rounded
    return dict(admitted=predicted >= MIN_FREE_BYTES,free_bytes=free,
                minimum_free_bytes=MIN_FREE_BYTES,source_reclaimable_bytes=reclaimable,
                hot_rounded_bytes=hot_rounded,predicted_free_bytes=predicted)


def capacity_after_transfer(database):
    free,_ = _filesystem(database)
    return dict(admitted=free >= MIN_FREE_BYTES,free_bytes=free,minimum_free_bytes=MIN_FREE_BYTES)
