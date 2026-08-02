"""Copy everything under MEDIA_ROOT into the configured R2 bucket.

Run this once on the server, with USE_R2 already switched on, so that files
uploaded before the cutover keep resolving. Existing DB rows store *relative*
paths (e.g. ``player_photos/foo.webp``), so preserving the relative layout is what
makes them keep working — no data migration is needed.

    python manage.py migrate_media_to_r2 --dry-run
    python manage.py migrate_media_to_r2

Safe to re-run: objects already present are skipped unless --overwrite is passed.
"""

import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


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

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        overwrite = options['overwrite']
        media_root = options['media_root'] or settings.MEDIA_ROOT

        if not getattr(settings, 'USE_R2', False):
            raise CommandError(
                "USE_R2 is not enabled, so the default storage is still the local "
                "filesystem and this would copy files onto themselves. Set USE_R2=True "
                "first."
            )

        if not os.path.isdir(media_root):
            raise CommandError(f"MEDIA_ROOT does not exist: {media_root}")

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
                if dry_run:
                    self.stdout.write(f"  would upload   {key}  ({size / 1024:.0f} KB)")
                    uploaded += 1
                    total_bytes += size
                    continue

                try:
                    with open(absolute, 'rb') as fh:
                        # save() may suffix the name on collision; with the exists()
                        # check above that only happens when --overwrite is set.
                        default_storage.save(key, fh)
                except Exception as exc:  # noqa: BLE001 - report and keep going
                    self.stderr.write(self.style.ERROR(f"  FAILED         {key}: {exc}"))
                    failed += 1
                    continue

                self.stdout.write(self.style.SUCCESS(f"  uploaded       {key}  ({size / 1024:.0f} KB)"))
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
