"""Custom storage backend for Cloudflare R2.

django-storages' S3Boto3Storage defaults to ``application/octet-stream`` for
every upload because it has no way to know the MIME type of a raw file handle.
This thin subclass overrides ``_save`` to detect the type from the filename
before calling boto3, so ``.webp``, ``.jpg``, ``.png`` etc. are stored with
the correct ``Content-Type`` header automatically.

No configuration change is needed — just point the STORAGES backend at this
class instead of ``storages.backends.s3.S3Storage``.
"""

import mimetypes

from storages.backends.s3 import S3Storage


class R2Storage(S3Storage):
    """S3-compatible storage for Cloudflare R2 that preserves MIME types."""

    def _save(self, name: str, content) -> str:
        # Detect content-type from the filename if not already set on the file
        # object (InMemoryUploadedFile carries content_type from the browser, but
        # a plain open() handle does not).
        if not getattr(content, 'content_type', None):
            mime, _ = mimetypes.guess_type(name)
            if mime:
                content.content_type = mime  # picked up by S3Boto3Storage._save

        return super()._save(name, content)
