from django.core.exceptions import ValidationError

MAX_PHOTO_SIZE_BYTES = 1 * 1024 * 1024


def validate_photo_size(value):
    if value.size > MAX_PHOTO_SIZE_BYTES:
        raise ValidationError('Photo must be smaller than 1MB.')
