"""Copy everything under MEDIA_ROOT into the configured R2 bucket.

Run this once on the server, with USE_R2 already switched on, so that files
uploaded before the cutover keep resolving. Existing DB rows store *relative*
paths (e.g. ``player_photos/foo.webp``), so preserving the relative layout is what
makes them keep working — no data migration is needed.

    python manage.py migrate_media_to_r2 --dry-run
    python manage.py migrate_media_to_r2

Safe to re-run: objects already present are skipped unless --overwrite is passed.

To patch the content-type on already-uploaded files without re-transferring data:

    python manage.py migrate_media_to_r2 --fix-content-type --dry-run
    python manage.py migrate_media_to_r2 --fix-content-type

This performs a server-side S3 copy_object with MetadataDirective=REPLACE so
no bytes are transferred between the server and R2.
"""

import mimetypes
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


def _guess_content_type(filename: str) -> str:
    """Return the MIME type for *filename*, defaulting to application/octet-stream."""
    mime, _ = mimetypes.guess_type(filename)
    return mime or 'application/octet-stream'


class Command(BaseCommand):
    help = "Upload local MEDIA_ROOT files to the configured remote storage (R2)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="List what would be uploaded without transferring anything.",
        )
        parser.add_argument(
            '--overwrite', action='store_true',
            help="Re-upload files that already exist in the bucket.",
        )
        parser.add_argument(
            '--media-root', default=None,
            help="Source directory. Defaults to settings.MEDIA_ROOT.",
        )
        parser.add_argument(
            '--fix-content-type', action='store_true',
            help=(
                "Patch the Content-Type on objects already in R2 using a server-side "
                "copy_object (no bytes transferred). Skips the normal upload logic."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        overwrite = options['overwrite']
        fix_content_type = options['fix_content_type']
        media_root = options['media_root'] or settings.MEDIA_ROOT

        if not getattr(settings, 'USE_R2', False):
            raise CommandError(
                "USE_R2 is not enabled, so the default storage is still the local "
                "filesystem and this would copy files onto themselves. Set USE_R2=True "
                "first."
            )

        if not os.path.isdir(media_root):
            raise CommandError(f"MEDIA_ROOT does not exist: {media_root}")

        if fix_content_type:
            self._fix_content_types(media_root, dry_run)
            return

        uploaded = skipped = failed = 0
        total_bytes = 0

        for dirpath, _dirnames, filenames in os.walk(media_root):
            for filename in sorted(filenames):
                # Editor leftovers and macOS metadata shouldn't be published.
                if filename in {'.DS_Store'} or filename.endswith('~'):
                    continue

                absolute = os.path.join(dirpath, filename)
                # Relative path == the value stored in the DB's FileField.
                key = os.path.relpath(absolute, media_root).replace(os.sep, '/')

                if not overwrite and default_storage.exists(key):
                    self.stdout.write(f"  skip (exists)  {key}")
                    skipped += 1
                    continue

                size = os.path.getsize(absolute)
                content_type = _guess_content_type(filename)

                if dry_run:
                    self.stdout.write(
                        f"  would upload   {key}  ({size / 1024:.0f} KB)  [{content_type}]"
                    )
                    uploaded += 1
                    total_bytes += size
                    continue

                try:
                    with open(absolute, 'rb') as fh:
                        data = fh.read()
                    # ContentFile lets django-storages pick up content_type so that
                    # boto3 passes it as ContentType to S3/R2 instead of defaulting
                    # to application/octet-stream.
                    cf = ContentFile(data, name=filename)
                    cf.content_type = content_type  # read by S3Boto3Storage
                    # delete first when overwriting so the storage doesn't suffix the key
                    if overwrite and default_storage.exists(key):
                        default_storage.delete(key)
                    default_storage.save(key, cf)
                except Exception as exc:  # noqa: BLE001 - report and keep going
                    self.stderr.write(self.style.ERROR(f"  FAILED         {key}: {exc}"))
                    failed += 1
                    continue

                self.stdout.write(self.style.SUCCESS(
                    f"  uploaded       {key}  ({size / 1024:.0f} KB)  [{content_type}]"
                ))
                uploaded += 1
                total_bytes += size

        verb = 'would upload' if dry_run else 'uploaded'
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{verb}: {uploaded} file(s), {total_bytes / 1024 / 1024:.1f} MB  |  "
            f"skipped: {skipped}  |  failed: {failed}"
        ))
        if failed:
            raise CommandError(f"{failed} file(s) failed to upload.")

    def _fix_content_types(self, media_root: str, dry_run: bool) -> None:
        """Patch content-type on already-uploaded R2 objects using a server-side copy.

        S3's copy_object with MetadataDirective=REPLACE rewrites the object's
        metadata (including Content-Type) in-place without transferring the body
        across the network — so this is near-instant regardless of file size.
        """
        storage = default_storage
        # django-storages S3Boto3Storage exposes the underlying boto3 client via
        # connection.meta.client or directly as storage.connection.
        try:
            s3 = storage.connection.meta.client  # type: ignore[attr-defined]
        except AttributeError:
            try:
                s3 = storage.connection  # type: ignore[attr-defined]
            except AttributeError:
                raise CommandError(
                    "Could not obtain a boto3 S3 client from default_storage. "
                    "Make sure USE_R2=True and the storage backend is S3Boto3Storage."
                )

        bucket = storage.bucket_name  # type: ignore[attr-defined]
        fixed = skipped = failed = 0

        for dirpath, _dirnames, filenames in os.walk(media_root):
            for filename in sorted(filenames):
                if filename in {'.DS_Store'} or filename.endswith('~'):
                    continue

                absolute = os.path.join(dirpath, filename)
                key = os.path.relpath(absolute, media_root).replace(os.sep, '/')
                content_type = _guess_content_type(filename)

                if content_type == 'application/octet-stream':
                    self.stdout.write(f"  skip (unknown type)  {key}")
                    skipped += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        f"  would patch    {key}  →  {content_type}"
                    )
                    fixed += 1
                    continue

                try:
                    s3.copy_object(
                        Bucket=bucket,
                        CopySource={'Bucket': bucket, 'Key': key},
                        Key=key,
                        ContentType=content_type,
                        MetadataDirective='REPLACE',
                    )
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(self.style.ERROR(f"  FAILED         {key}: {exc}"))
                    failed += 1
                    continue

                self.stdout.write(self.style.SUCCESS(f"  patched        {key}  →  {content_type}"))
                fixed += 1

        verb = 'would patch' if dry_run else 'patched'
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{verb}: {fixed} file(s)  |  skipped: {skipped}  |  failed: {failed}"
        ))
        if failed:
            raise CommandError(f"{failed} file(s) failed to patch.")
