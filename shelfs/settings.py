"""Local demo defaults; PostgreSQL and SMTP are configured through .env."""
import os
import secrets
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
# A small .env reader: existing process environment takes precedence.
if (BASE_DIR / '.env').exists():
    for line in (BASE_DIR / '.env').read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            name, value = line.split('=', 1)
            os.environ.setdefault(name.strip(), value.strip().strip('\"\''))

DEBUG = os.environ.get('DEBUG', '1') == '1'
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('Set a private SECRET_KEY before deployment.')
    secret_file = DATA_DIR / 'dev-secret.key'
    try:
        with secret_file.open('x', encoding='utf-8') as stream:
            stream.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    SECRET_KEY = secret_file.read_text(encoding='utf-8')

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'library',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'shelfs.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
WSGI_APPLICATION = 'shelfs.wsgi.application'
DB_ENGINE = os.environ.get('DB_ENGINE', 'sqlite')
if DB_ENGINE == 'postgresql':
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'shelfs'),
        'USER': os.environ.get('DB_USER', 'shelfs'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        'OPTIONS': {'connect_timeout': 5},
    }}
elif DB_ENGINE == 'sqlite':
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'db.sqlite3',
        'OPTIONS': {'timeout': 20},
    }}
else:
    raise ImproperlyConfigured('DB_ENGINE must be sqlite or postgresql.')

AUTH_USER_MODEL = 'library.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'uk'
TIME_ZONE = 'Europe/Kyiv'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'account'
LOGOUT_REDIRECT_URL = 'catalog'
SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.filebased.EmailBackend')
EMAIL_FILE_PATH = DATA_DIR / 'mail'
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', '1') == '1'
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', '0') == '1'
EMAIL_TIMEOUT = 15
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured('Choose either EMAIL_USE_TLS or EMAIL_USE_SSL.')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'library@example.com')
CONFIRMATION_MAX_AGE = 3600
RESERVATION_HOURS = int(os.environ.get('RESERVATION_HOURS', '48'))
LOAN_DAYS = int(os.environ.get('LOAN_DAYS', '14'))
REMINDER_DAYS = int(os.environ.get('REMINDER_DAYS', '2'))
OVERDUE_REPEAT_DAYS = int(os.environ.get('OVERDUE_REPEAT_DAYS', '3'))
if min(RESERVATION_HOURS, LOAN_DAYS, OVERDUE_REPEAT_DAYS) <= 0 or REMINDER_DAYS < 0:
    raise ImproperlyConfigured('Library time intervals must be positive (reminder may be zero).')
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

CSRF_TRUSTED_ORIGINS = [value for value in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if value]
# Enable only behind a trusted proxy that overwrites incoming forwarded headers.
if os.environ.get('TRUST_PROXY', '0') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
