"""Bounded managed transfer into the unchanged accepted restore verifier."""
from __future__ import annotations

from functools import partial
import os
from pathlib import Path
import shutil
import sys
import tempfile


class ManagedRestoreClient:
    def __init__(self, client, *, temp_dir):
        self.client = client
        self.temp_dir = Path(temp_dir)
        self.heads = {}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def head_object(self, **kwargs):
        result = self.client.head_object(**kwargs)
        self.heads[(kwargs['Bucket'], kwargs['Key'])] = result
        return result

    def get_object(self, **kwargs):
        head = self.heads.get((kwargs['Bucket'], kwargs['Key']))
        if head is None:
            return self.client.get_object(**kwargs)
        # The canonical verifier has already checked this HEAD against its
        # manifest. Reserve both compressed staging and the restored database.
        metadata = {str(k).lower(): str(v) for k,v in head['Metadata'].items()}
        required = (int(head['ContentLength']) +
                    int(metadata['source-size']) + 2 * 1024**3)
        if shutil.disk_usage(self.temp_dir).free < required:
            raise RuntimeError('managed download worker staging lacks headroom')
        from boto3.s3.transfer import TransferConfig
        config = TransferConfig(multipart_threshold=64*1024**2,
            multipart_chunksize=64*1024**2, max_concurrency=2,
            num_download_attempts=5, max_io_queue=16,
            preferred_transfer_client='classic')
        staged = tempfile.TemporaryFile(dir=self.temp_dir)
        try:
            self.client.download_fileobj(kwargs['Bucket'], kwargs['Key'], staged,
                                         Config=config)
            staged.seek(0)
            return {'Body': staged}
        except BaseException:
            staged.close()
            raise


def main():
    # The workflow checks out the accepted SHA into cwd; this transport lives
    # in a separate sparse checkout and supplies no replacement verification.
    sys.path.insert(0, str(Path.cwd() / 'scripts'))
    import restore_verified_snapshot as verifier
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url=verifier.ENDPOINT,
        region_name='ru-central1', config=Config(connect_timeout=10,
            read_timeout=120, retries={'max_attempts': 3}))
    verifier.restore = partial(verifier.restore,
        client=ManagedRestoreClient(client,
            temp_dir=os.environ.get('RUNNER_TEMP', tempfile.gettempdir())))
    return verifier.main()


if __name__ == '__main__':
    raise SystemExit(main())
