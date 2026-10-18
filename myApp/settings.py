from pathlib import Path
import os
import dj_database_url
#from dotenv import load_dotenv
import cloudinary

BASE_DIR = Path(__file__).resolve().parent.parent
#load_dotenv(BASE_DIR / '.env')

SECRET_KEY ='django-insecure-+_hifhep#jpt5&%*i%nnog_8ws@($^*^8zjg5kf&=jf3%gr8vj'
DEBUG = False  
USE_CLOUDINARY = True

# Allowed hosts configuration
ALLOWED_HOSTS = [
    '127.0.0.1',
    'localhost',
    'topmarktutorial.onrender.com',

    
]




CSRF_TRUSTED_ORIGINS = [
    'https://topmarktutorial.onrender.com',
]


MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')


INSTALLED_APPS = [
    'daphne', 
    'channels',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'school',  
    'django.contrib.humanize',
    'pwa',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'myApp.urls'

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
                'school.context_processors.user_post',
                'school.context_processors.user_notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'myApp.wsgi.application'
ASGI_APPLICATION = 'myApp.asgi.application'


if DEBUG:
    
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        }
    }
else:
    
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [os.environ.get("REDIS_URL", "redis://localhost:6379")],
            },
        },
    }

# Database
if USE_CLOUDINARY:
    DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv("DATABASE_URL"),  
        conn_max_age=600,
        conn_health_checks=True,
        ssl_require=True
    )
    }
else:
    DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv("DATABASE"),  
        conn_max_age=600,
        env='DATABASE_URL'
    )
    }



AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Lagos'  
USE_I18N = True
USE_TZ = True


STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
_static_dir = os.path.join(BASE_DIR, 'static')
STATICFILES_DIRS = [_static_dir] if os.path.isdir(_static_dir) else []

# Media files configuration
if USE_CLOUDINARY:
    # Cloudinary storage for media files
    INSTALLED_APPS += [
        'cloudinary',
        'cloudinary_storage',
    ]
    
    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': os.getenv('CLOUDINARY_CLOUD_NAME'),
        'API_KEY': os.getenv('CLOUDINARY_API_KEY'),
        'API_SECRET': os.getenv('CLOUDINARY_API_SECRET'),
        'SECURE': True,
    }
    
    DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
    
else:
    # Local file storage for development
    DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'

# Static files storage (WhiteNoise for production)
if not DEBUG:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# PWA Settings
PWA_APP_NAME = 'TopMark'
PWA_APP_SHORT_NAME = 'TopMark'
PWA_APP_DESCRIPTION = "Install TopMark on Your Phone"
PWA_APP_THEME_COLOR = '#0095f6'
PWA_APP_BACKGROUND_COLOR = '#ffffff'
PWA_APP_DISPLAY = 'standalone'
PWA_APP_SCOPE = '/'
PWA_APP_ORIENTATION = 'natural'
PWA_APP_START_URL = '/'
PWA_APP_STATUS_BAR_COLOR = 'default'

PWA_APP_ICONS = [
    {'src': '/static/images/logo.png', 'sizes': '192x192', 'type': 'image/png'},
    {'src': '/static/images/logo.png', 'sizes': '512x512', 'type': 'image/png'},
    {
        'src': '/static/images/logo.png',
        'sizes': '512x512',
        'type': 'image/png',
        'purpose': 'maskable'
    },
]

# Point django-pwa at your custom service worker
PWA_SERVICE_WORKER_PATH = BASE_DIR / 'school' / 'static' / 'js' / 'serviceworker.js'

# The offline fallback URL — must match urls.py
PWA_APP_FETCH_URL = '/'


if not DEBUG:
    # HTTPS Security
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True
    
    # Cookie Security
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    
    # Browser Security Headers
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'
    
    # HSTS (HTTP Strict Transport Security)
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Logging Configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose' if DEBUG else 'simple',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': os.path.join(BASE_DIR, 'django.log'),
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO' if DEBUG else 'WARNING',
        },
        'channels': {
            'handlers': ['console'],
            'level': 'INFO' if DEBUG else 'WARNING',
        },
        'school': {  # Your app's logger
            'handlers': ['console', 'file'] if DEBUG else ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
        },
    },
}

# Email Configuration (for production)
if not DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Cache Configuration (optional, for better performance)
if not DEBUG:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': os.getenv('REDIS_URL', 'redis://localhost:6379/1'),
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }

# Session Configuration
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 1209600  # 2 weeks
SESSION_SAVE_EVERY_REQUEST = True

# CSRF Settings
CSRF_COOKIE_AGE = 31449600  # 1 year
CSRF_USE_SESSIONS = False

# File Upload Settings
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000
DATA_UPLOAD_MAX_NUMBER_FILES = 100
DATA_UPLOAD_MAX_MEMORY_SIZE = 26214400  # 25 MB

# Custom Settings for Live Video
MAX_LIVE_PARTICIPANTS = 50
VIDEO_UPLOAD_MAX_SIZE = 104857600  # 100 MB
SCREEN_SHARE_ENABLED = True
CHAT_MESSAGE_HISTORY_LIMIT = 100

# WebSocket Settings
WEBSOCKET_CONNECTION_TIMEOUT = 3600  # 1 hour
WEBSOCKET_PING_INTERVAL = 20  # seconds
WEBSOCKET_PING_TIMEOUT = 10  # seconds

# STUN/TURN Servers for WebRTC (configure for production)
WEBRTC_ICE_SERVERS = [
    {'urls': 'stun:stun.l.google.com:19302'},
    {'urls': 'stun:stun1.l.google.com:19302'},
    {'urls': 'stun:stun2.l.google.com:19302'},
]

# For production, add TURN servers:
if not DEBUG:
    WEBRTC_ICE_SERVERS.extend([
        {
            'urls': 'turn:turn.yourserver.com:3478',
            'username': os.getenv('TURN_USERNAME'),
            'credential': os.getenv('TURN_PASSWORD'),
        },
    ])

print(f"Running in {'DEBUG' if DEBUG else 'PRODUCTION'} mode")
if USE_CLOUDINARY:
    print("Cloudinary storage enabled")
print(f"Channel layer: {'InMemory' if DEBUG else 'Redis'}")