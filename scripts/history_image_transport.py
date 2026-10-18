"""Bounded private Yandex image streams; no upload or production mutation."""
from contextlib import contextmanager, suppress
import copy
import gzip
import hashlib
import math
import re
import time
from urllib import request
from urllib.parse import urlsplit

CONTRACT = 'sqlite-history-image-v1'
BUCKET = 'trading-dbd-backups-2026'
CHUNK_BYTES = 1024**2
MAX_BYTES = 25*1024**3
SOCKET_SECONDS = 10
FIELDS = {'contract','run_id','kind','bucket','object_key','codec',
          'database_sha256','database_size_bytes','compressed_sha256','compressed_size_bytes'}


class ImageTransportError(ValueError):
    """Stable public codes never include private transport input."""


def _check(deadline):
    if time.monotonic() >= deadline:
        raise ImageTransportError('IMAGE_TRANSFER_DEADLINE')


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ImageTransportError('IMAGE_REDIRECT_REFUSED')


def _validate(entry, kind):
    if type(entry) is not dict or set(entry) != {'url','receipt'}:
        raise ImageTransportError('INVALID_PRIVATE_IMAGE_INPUT')
    receipt, url = entry['receipt'], entry['url']
    if (type(receipt) is not dict or set(receipt) != FIELDS
            or receipt.get('contract') != CONTRACT or receipt.get('kind') != kind
            or receipt.get('bucket') != BUCKET or receipt.get('codec') != 'gzip'
            or type(receipt.get('run_id')) is not str
            or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',receipt['run_id'])
            or any(type(receipt.get(k)) is not str or not re.fullmatch('[0-9a-f]{64}',receipt[k])
                   for k in ('database_sha256','compressed_sha256'))
            or type(receipt.get('database_size_bytes')) is not int
            or not 4096 <= receipt['database_size_bytes'] <= MAX_BYTES
            or type(receipt.get('compressed_size_bytes')) is not int
            or not 0 < receipt['compressed_size_bytes'] <= MAX_BYTES):
        raise ImageTransportError('INVALID_IMAGE_RECEIPT')
    key = (f'research/sqlite-cutover/v1/{receipt["run_id"]}/{kind}/'
           f'{receipt["database_sha256"]}/{receipt["compressed_sha256"]}.sqlite3.gz')
    if receipt['object_key'] != key or type(url) is not str or len(url) > 16384:
        raise ImageTransportError('INVALID_IMAGE_OBJECT_KEY')
    try:
        parsed = urlsplit(url)
        allowed = (parsed.scheme == 'https' and parsed.netloc == 'storage.yandexcloud.net'
                   and parsed.path == '/'+BUCKET+'/'+key and not parsed.fragment
                   and parsed.query and 'X-Amz-Signature=' in parsed.query
                   and not any(ord(c) <= 32 or ord(c) >= 127 for c in url))
    except ValueError:
        allowed = False
    if not allowed:
        raise ImageTransportError('PRIVATE_IMAGE_ORIGIN_REFUSED')


class _Compressed:
    def __init__(self, response, receipt, deadline):
        self.response, self.receipt, self.deadline = response, receipt, deadline
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size):
        _check(self.deadline)
        if type(size) is not int or not 0 < size <= CHUNK_BYTES:
            raise ImageTransportError('UNBOUNDED_COMPRESSED_READ')
        try:
            data = self.response.read(size)
        except Exception:
            raise ImageTransportError('IMAGE_NETWORK_READ_FAILED') from None
        _check(self.deadline)
        if not isinstance(data, bytes) or len(data) > size:
            raise ImageTransportError('INVALID_IMAGE_NETWORK_CHUNK')
        self.size += len(data)
        if self.size > self.receipt['compressed_size_bytes']:
            raise ImageTransportError('COMPRESSED_IMAGE_SIZE_MISMATCH')
        self.digest.update(data)
        return data


class _Decoded:
    def __init__(self, source, receipt, deadline):
        self.source, self.receipt, self.deadline = source, receipt, deadline
        self.decoded = gzip.GzipFile(fileobj=source,mode='rb')
        self.digest = hashlib.sha256()
        self.size = 0
        self.verified = False

    def read(self, size):
        _check(self.deadline)
        if type(size) is not int or not 0 < size <= CHUNK_BYTES:
            raise ImageTransportError('UNBOUNDED_DECODED_READ')
        if self.verified:
            return b''
        try:
            data = self.decoded.read(size)
        except ImageTransportError:
            raise
        except Exception:
            raise ImageTransportError('INVALID_IMAGE_GZIP_STREAM') from None
        _check(self.deadline)
        self.size += len(data)
        self.digest.update(data)
        if self.size > self.receipt['database_size_bytes']:
            raise ImageTransportError('RAW_IMAGE_SIZE_MISMATCH')
        if not data:
            if (self.source.read(1) or self.size != self.receipt['database_size_bytes']
                    or self.digest.hexdigest() != self.receipt['database_sha256']
                    or self.source.size != self.receipt['compressed_size_bytes']
                    or self.source.digest.hexdigest() != self.receipt['compressed_sha256']):
                raise ImageTransportError('IMAGE_IDENTITY_MISMATCH')
            self.verified = True
        return data


class SignedImageTransport:
    def __init__(self, private_inputs, *, deadline, opener=None):
        if (type(private_inputs) is not dict or not private_inputs
                or set(private_inputs)-{'original','hot'}
                or type(deadline) not in (int,float) or not math.isfinite(deadline)):
            raise ImageTransportError('INVALID_PRIVATE_IMAGE_INPUT')
        self._inputs = copy.deepcopy(private_inputs)
        for kind, entry in self._inputs.items():
            _validate(entry,kind)
        if len({v['receipt']['run_id'] for v in self._inputs.values()}) != 1:
            raise ImageTransportError('MIXED_IMAGE_RUN_IDENTITIES')
        self._deadline = deadline
        self._open = opener or request.build_opener(NoRedirect()).open

    @contextmanager
    def open_image(self, kind):
        _check(self._deadline)
        if kind not in self._inputs:
            raise ImageTransportError('UNKNOWN_IMAGE_KIND')
        entry = self._inputs[kind]
        try:
            response = self._open(request.Request(entry['url']),
                timeout=min(SOCKET_SECONDS,max(.001,self._deadline-time.monotonic())))
        except ImageTransportError:
            raise
        except Exception:
            raise ImageTransportError('IMAGE_OPEN_FAILED') from None
        decoded = None
        try:
            _check(self._deadline)
            try:
                valid = (response.status == 200
                         and response.headers.get('Content-Length') == str(entry['receipt']['compressed_size_bytes'])
                         and not response.headers.get('Content-Encoding'))
            except Exception:
                valid = False
            if not valid:
                raise ImageTransportError('IMAGE_HTTP_CONTRACT_MISMATCH')
            decoded = _Decoded(_Compressed(response,entry['receipt'],self._deadline),
                               entry['receipt'],self._deadline)
            # Do not catch consumer exceptions: a FencingError must retain its
            # type so the transaction can never mistake it for network failure.
            yield decoded
        finally:
            if decoded is not None:
                with suppress(Exception):
                    decoded.decoded.close()
            with suppress(Exception):
                response.close()

    def verify_image(self, kind):
        with self.open_image(kind) as stream:
            while stream.read(CHUNK_BYTES):
                pass
            assert stream.verified
        return dict(self._inputs[kind]['receipt'],full_get_verified=True)
