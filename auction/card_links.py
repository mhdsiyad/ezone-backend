"""Normalising participant-card image links pasted into the import sheet.

Google Drive share URLs are not image URLs — pointing an <img> at one returns an
HTML viewer page. Drive does expose a direct-image endpoint keyed on the file id,
so anything we can pull an id out of is rewritten to that. Links from a normal
image host (Cloudinary, ImgBB, ...) are already direct and pass through untouched.
"""

import re
from urllib.parse import urlparse, parse_qs

# Drive file ids are long opaque strings; the surrounding URL shape varies a lot.
_DRIVE_HOSTS = {'drive.google.com', 'docs.google.com'}
_FILE_PATH_RE = re.compile(r'/file/d/([A-Za-z0-9_-]+)')
_SHORT_PATH_RE = re.compile(r'^/d/([A-Za-z0-9_-]+)')

# Must be the lh3 CDN host, NOT drive.google.com/thumbnail: the latter answers a
# cross-origin <img> with a 302 that Chrome refuses to follow, so the card renders
# blank. lh3 serves the bytes directly. =w1000 asks for a 1000px-wide render —
# big enough to project, small enough to load fast.
DRIVE_IMAGE_TEMPLATE = 'https://lh3.googleusercontent.com/d/{file_id}=w1000'


class CardLinkError(ValueError):
    """A link that cannot ever resolve to an image, with a reason for the manager."""


def drive_file_id(url):
    """Pull the file id out of any Drive URL shape, or None if there isn't one."""
    parsed = urlparse(url)
    if parsed.netloc not in _DRIVE_HOSTS:
        return None

    for pattern in (_FILE_PATH_RE, _SHORT_PATH_RE):
        match = pattern.search(parsed.path)
        if match:
            return match.group(1)

    # /open?id=, /uc?id=, /thumbnail?id=
    ids = parse_qs(parsed.query).get('id')
    return ids[0] if ids else None


def normalise_card_link(url):
    """Return a direct image URL, or '' for blank input.

    Raises CardLinkError when the link is a Drive folder — a folder holds many
    files and names none of them, so there is no image to show and silently
    storing it would leave a blank card at auction time.
    """
    url = (url or '').strip()
    if not url:
        return ''

    parsed = urlparse(url)
    if parsed.netloc in _DRIVE_HOSTS:
        if '/folders/' in parsed.path:
            raise CardLinkError(
                'this is a Drive *folder* link, which has no single image in it — '
                'open the card image itself, Share > Anyone with the link, and paste that'
            )
        file_id = drive_file_id(url)
        if not file_id:
            raise CardLinkError('could not find a Drive file id in this link')
        return DRIVE_IMAGE_TEMPLATE.format(file_id=file_id)

    if parsed.scheme not in ('http', 'https'):
        raise CardLinkError('not a valid http(s) link')

    return url
