import gzip
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ede_ledger_archive import prepare, upload, restore, compress_closed, retire_plain, retire_installer, retire_closed_archive, require_capacity


class ObjectStore:
    def __init__(self):
        self.objects = {}
        self.corrupt = False

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.objects[key] = (Path(path).read_bytes(), ExtraArgs['Metadata'])

    def head_object(self, **kw):
        data, metadata = self.objects[kw['Key']]
        return {'ContentLength': len(data), 'Metadata': metadata}

    def get_object(self, **kw):
        data = self.objects[kw['Key']][0]
        if self.corrupt:
            data = data[:-1] + bytes([data[-1] ^ 1])
        return {'Body': io.BytesIO(data)}

    def put_object(self, **kw):
        self.objects[kw['Key']] = (kw['Body'], {})


class LedgerArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'ede_v13_candidate_registry.jsonl'
        self.original = (json.dumps({'event': 'EVALUATION_RECORDED', 'legacy': True}) + '\n').encode() * 3000
        self.source.write_bytes(self.original)
        self.archive = self.root / 'registry.gz'
        self.store = ObjectStore()

    def prepared(self):
        return prepare(self.source, self.archive, expected_sha='a' * 40)

    def test_exact_bytes_round_trip_and_original_hash_preserved(self):
        manifest = upload(self.store, self.archive, self.prepared())
        out = self.root / 'restored.jsonl'
        restore(self.store, manifest, out)
        self.assertEqual(out.read_bytes(), self.original)
        self.assertEqual(manifest['source_sha256'], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(manifest['production_authority'])

    def test_full_read_corruption_refuses_receipt(self):
        manifest = self.prepared()
        self.store.corrupt = True
        with self.assertRaises((ValueError, OSError, EOFError)):
            upload(self.store, self.archive, manifest)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_bad_manifest_refuses_before_touching_destination(self):
        manifest = upload(self.store, self.archive, self.prepared())
        out = self.root / 'existing'
        out.write_bytes(b'keep')
        for field, value in [('bucket', 'other'), ('object_key', 'backups/v1/daily-slot-1/snapshot.sqlite3.gz'), ('source_size_bytes', 0), ('verified_full_get', False)]:
            invalid = {**manifest, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                restore(self.store, invalid, out)
            self.assertEqual(out.read_bytes(), b'keep')

    def test_truncated_restore_does_not_replace_prior_destination(self):
        manifest = upload(self.store, self.archive, self.prepared())
        out = self.root / 'existing'
        out.write_bytes(b'keep')
        self.store.corrupt = True
        with self.assertRaises((ValueError, OSError, EOFError)):
            restore(self.store, manifest, out)
        self.assertEqual(out.read_bytes(), b'keep')

    def test_raw_or_compressed_hash_mismatch_rejects(self):
        manifest = upload(self.store, self.archive, self.prepared())
        for field in ('source_sha256', 'compressed_sha256'):
            bad = {**manifest, field: '0' * 64}
            with self.subTest(field=field), self.assertRaises(ValueError):
                restore(self.store, bad, self.root / 'out')

    def test_symlink_source_refused(self):
        link = self.root / 'link'
        link.symlink_to(self.source)
        with self.assertRaises(ValueError):
            prepare(link, self.archive, expected_sha='a' * 40)

    def test_closed_log_is_losslessly_compressed_and_open_log_preserved(self):
        source = self.root / 'syslog.1'
        source.write_bytes(self.original)
        result = compress_closed(source, is_closed=lambda _: True)
        self.assertGreater(result['bytes_released'], 0)
        self.assertEqual(gzip.decompress(source.with_name('syslog.1.gz').read_bytes()), self.original)
        self.assertFalse(source.exists())
        source.write_bytes(b'active')
        with self.assertRaises(ValueError):
            compress_closed(source, is_closed=lambda _: False)
        self.assertEqual(source.read_bytes(), b'active')

    def test_changed_or_existing_log_archive_never_unlinks_source(self):
        source = self.root / 'auth.log.1'
        source.write_bytes(self.original)
        source.with_name('auth.log.1.gz').write_bytes(b'previous')
        with self.assertRaises(ValueError):
            compress_closed(source, is_closed=lambda _: True)
        self.assertEqual(source.read_bytes(), self.original)


    def test_compression_rechecks_closed_state_before_releasing_original(self):
        source = self.root / 'btmp.1'
        source.write_bytes(self.original)
        states = iter((True, False))
        with self.assertRaises(ValueError):
            compress_closed(source, is_closed=lambda _: next(states))
        self.assertEqual(source.read_bytes(), self.original)

    def test_research_retirement_preserves_every_old_event_in_cloud_and_gzip(self):
        new_source = self.root / 'new' / self.source.name
        new_source.parent.mkdir()
        new_source.write_bytes(self.original + b'{"new":true}\n')
        packed = self.source.with_name(self.source.name + '.gz')
        manifest = upload(self.store, packed, prepare(new_source, packed, expected_sha='a' * 40))
        result = retire_plain(self.source, packed, manifest)
        self.assertEqual(result['retired_bytes'], len(self.original))
        self.assertEqual(gzip.decompress(packed.read_bytes()), new_source.read_bytes())

    def test_research_retirement_rejects_missing_legacy_prefix(self):
        wrong = self.root / 'new' / self.source.name
        wrong.parent.mkdir()
        wrong.write_bytes(b'{"different":true}\n')
        packed = self.source.with_name(self.source.name + '.gz')
        manifest = upload(self.store, packed, prepare(wrong, packed, expected_sha='a' * 40))
        with self.assertRaises(ValueError):
            retire_plain(self.source, packed, manifest)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_only_exact_verified_retired_installer_may_be_released(self):
        installer = self.root / 'actions-runner-linux-x64-2.322.0.tar.gz'
        installer.write_bytes(b'original installer')
        packed = self.root / 'installer.gz'
        manifest = upload(self.store, packed, prepare(installer, packed, expected_sha='a' * 40))
        invalid = {**manifest, 'verified_full_get': False}
        with self.assertRaises(ValueError):
            retire_installer(installer, invalid)
        installer.write_bytes(b'changed installer')
        with self.assertRaises(ValueError):
            retire_installer(installer, manifest)
        installer.write_bytes(b'original installer')
        self.assertGreater(retire_installer(installer, manifest)['retired_bytes'], 0)

    def test_staging_reserve_refuses_without_writing(self):
        with patch('scripts.ede_ledger_archive.shutil.disk_usage', return_value=SimpleNamespace(free=1024**3+10)):
            with self.assertRaises(ValueError):
                require_capacity(self.root, 20)

    def test_closed_archive_requires_cloud_proof_and_rechecks_open_state(self):
        source = self.root / 'btmp.1'
        source.write_bytes(self.original)
        packed = self.root / 'logs.gz'
        manifest = upload(self.store, packed, prepare(source, packed, expected_sha='a'*40))
        with self.assertRaises(ValueError):
            retire_closed_archive(source, manifest, is_closed=lambda _: False)
        self.assertTrue(source.exists())
        self.assertGreater(retire_closed_archive(source, manifest, is_closed=lambda _: True)['retired_bytes'], 0)


class LedgerTransportTests(unittest.TestCase):
    def test_newer_live_generation_cannot_be_hidden_by_stale_archive(self):
        from scripts import production_ede_offload as offload
        with tempfile.TemporaryDirectory() as temporary:
            raw = Path(temporary) / 'old'
            raw.write_bytes(b'old event\n')
            current = b'old event\nnew event\n'
            with patch.object(offload, '_remote_ledger_identity', return_value={'size':len(current),'sha256':hashlib.sha256(current).hexdigest()}):
                with self.assertRaises(ValueError):
                    offload._assert_remote_ledger_prefix(None, raw)

    def test_serialization_covers_snapshot_fetch_audit_and_publish(self):
        import yaml
        workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / '.github/workflows/production-ede-v12-audit.yml').read_text())
        self.assertEqual(workflow['concurrency']['group'], 'ede-v13-heavy-research')
        self.assertFalse(workflow['concurrency']['cancel-in-progress'])
        self.assertNotIn('concurrency', workflow['jobs']['audit'])

    def test_fetch_uses_verified_gzip_and_preserves_legacy_plaintext(self):
        from scripts import production_ede_offload as offload
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / 'remote'
            remote.mkdir()
            (remote / 'ede_frozen_evidence.jsonl').write_bytes(b'{"legacy": true}\n')
            raw = b'{"event":"EVALUATION_RECORDED","old_clock":123}\n'
            source = root / 'ede_v13_candidate_registry.jsonl'
            source.write_bytes(raw)
            archive = remote / (source.name + '.gz')
            manifest = prepare(source, archive, expected_sha='a' * 40)
            manifest['verified_full_get'] = True
            (remote / (source.name + '.archive.json')).write_text(json.dumps(manifest))
            class SFTP:
                def stat(self, path): return (remote / Path(path).name).stat()
                def get(self, path, output): Path(output).write_bytes((remote / Path(path).name).read_bytes())
                def close(self): pass
            client = SimpleNamespace(open_sftp=lambda: SFTP(), close=lambda: None)
            out = root / 'out'
            args = SimpleNamespace(password='unused', expected_sha='a' * 40, output_dir=str(out))
            with patch.object(offload, '_connect', return_value=client), patch.object(offload, '_verify_sha'), patch.object(offload, '_remote_ledger_identity', return_value={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}):
                offload.fetch_ledgers(args)
            self.assertEqual((out / source.name).read_bytes(), raw)
            self.assertEqual((out / 'ede_frozen_evidence.jsonl').read_bytes(), b'{"legacy": true}\n')

            # A disconnected publisher may leave a new cache with the old receipt.
            # The authoritative receipt's cloud object still restores the old history.
            store = ObjectStore()
            upload(store, archive, {**manifest, 'verified_full_get': False})
            archive.write_bytes(b'interrupted cache')
            with patch.object(offload, '_connect', return_value=client), patch.object(offload, '_verify_sha'), patch.object(offload, '_remote_ledger_identity', return_value={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}), patch('scripts.ede_ledger_archive.s3_client', return_value=store):
                offload.fetch_ledgers(args)
            self.assertEqual((out / source.name).read_bytes(), raw)

    def test_cloud_failure_cannot_publish_or_retire_production_registry(self):
        from scripts import production_ede_offload as offload
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in offload.OUTPUT_NAMES:
                (root / name).write_bytes(b'{}\n')
            args = SimpleNamespace(password='unused', expected_sha='a' * 40, output_dir=str(root), run_id='123')
            with patch('scripts.ede_ledger_archive.s3_client', side_effect=RuntimeError('storage unavailable')), patch.object(offload, '_connect') as connect:
                with self.assertRaises(Exception):
                    offload.publish(args)
                connect.assert_not_called()

if __name__ == '__main__':
    unittest.main()
