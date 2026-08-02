import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.getenv('SECRET_KEY', 'default-insecure-key')

DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 't')
print(f"DEBUG: {DEBUG}")
print(f"MEDIA_URL: {os.getenv('MEDIA_URL')}")
print(f"MEDIA_ROOT: {os.getenv('MEDIA_ROOT')}")

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

# nginx terminates TLS in front of this app, so Django sees plain http and
# request.build_absolute_uri() hands out http:// media URLs. On the https website
# those are blocked as mixed content before nginx's redirect can apply, so player
# photos and team logos silently fail to load. Trusting X-Forwarded-Proto makes
# Django build https:// URLs instead.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    'daphne',  # MUST be first — overrides runserver to use ASGI (enables WebSockets)
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third party
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'channels',
    # Local
    'auction',
    'teams',
    'players',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'ezone.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

ASGI_APPLICATION = 'ezone.asgi.application'
WSGI_APPLICATION = 'ezone.wsgi.application'

# In-memory channel layer for development (no Redis required)
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    }
}

# Database - SQLite for dev
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_USER_MODEL = 'auction.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS
cors_origins = os.getenv('CORS_ALLOWED_ORIGINS')
if cors_origins:
    CORS_ALLOWED_ORIGINS = cors_origins.split(',')
    CORS_ALLOW_ALL_ORIGINS = False
else:
    CORS_ALLOW_ALL_ORIGINS = True

CORS_ALLOW_CREDENTIALS = True

# CSRF Security for HTTPS Proxies
CSRF_TRUSTED_ORIGINS = [
    'https://ezone.siyad.tech',
    'https://ezone-football.vercel.app'
]

# DRF
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
}

# JWT
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=60),
    'AUTH_HEADER_TYPES': ('Bearer',),
}

MEDIA_URL = os.getenv('MEDIA_URL', '/media/')
MEDIA_ROOT = os.path.join(BASE_DIR, os.getenv('MEDIA_ROOT', 'media'))

# ── Media storage ────────────────────────────────────────────────────────────
# Off by default so local development keeps writing to MEDIA_ROOT on disk. Set
# USE_R2=True on the server to serve uploads from Cloudflare R2 instead.
#
# Serving media from R2 behind a custom domain also removes two problems that the
# nginx-served /media/ had: the CORS headers the browser needs to rasterise a player
# card, and the http:// URLs Django emitted from behind the TLS proxy.
USE_R2 = os.getenv('USE_R2', 'False').lower() in ('true', '1', 't')

if USE_R2:
    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.s3.S3Storage',
            'OPTIONS': {
                'bucket_name': os.getenv('R2_BUCKET', 'ezone'),
                'endpoint_url': os.getenv('R2_ENDPOINT'),
                'access_key': os.getenv('R2_ACCESS_KEY_ID'),
                'secret_key': os.getenv('R2_SECRET_ACCESS_KEY'),
                # R2 ignores regions but boto3 insists on one.
                'region_name': 'auto',
                # R2 has no ACL support; public access is granted on the bucket.
                'default_acl': None,
                # Serve plain public URLs rather than expiring signed ones.
                'querystring_auth': False,
                'signature_version': 's3v4',
                # The public hostname bound to the bucket, e.g.
                # media.ezone-football.online. Without it URLs point at the private
                # S3 API endpoint, which browsers cannot read.
                'custom_domain': os.getenv('R2_PUBLIC_DOMAIN'),
                # Keep both files if two uploads collide instead of overwriting.
                'file_overwrite': False,
            },
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }
    # Absolute R2 URLs come from the storage backend, so MEDIA_URL is unused for
    # uploads — kept only for any code that still concatenates it.
    MEDIA_URL = f"https://{os.getenv('R2_PUBLIC_DOMAIN', '')}/"
