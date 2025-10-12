from pathlib import Path
from datetime import timedelta
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# === أساسيات ===
SECRET_KEY = "change-me"
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").split(",") if not DEBUG else ["*"]


MEDIA_URL = "/media/"
MEDIA_ROOT = os.getenv("MEDIA_ROOT", os.path.join(BASE_DIR, "media"))


# === التطبيقات ===
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",

    # طرف ثالث
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "corsheaders",

    # محلي
    "api",
]
INSTALLED_APPS += ["cloudinary", "cloudinary_storage"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",  # يجب أن تكون قبل CommonMiddleware
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "zakati.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.debug",
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}]

WSGI_APPLICATION = "zakati.wsgi.application"

# === قاعدة البيانات (SQLite للتطوير) ===
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

import dj_database_url
if os.getenv("DATABASE_URL"):
    DATABASES["default"] = dj_database_url.parse(
        os.environ["DATABASE_URL"], conn_max_age=600, ssl_require=True
    )

# === DRF + JWT + Swagger ===
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Zakati API",
    "DESCRIPTION": "واجهة برمجية لتطبيق زكاتي (عربي، UI-First، Snapshot Sync).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# === اللغة والوقت ===
LANGUAGE_CODE = "ar"
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Kuala_Lumpur")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
    "default": {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# === CORS للتطوير ===
CORS_ALLOW_ALL_ORIGINS = True

# ===== Zakat Test Mode (للتطوير فقط) =====
# الإنتاج: اتركها False
ZAKAT_TEST_MODE = False          # إذا True: الحَول = عدد أيام اختبارية
ZAKAT_TEST_CYCLE_DAYS = 1        # مدة "الحَول" في الاختبار (مثلاً: 1 يوم)
ZAKAT_TEST_REMINDERS_HOURS = [6, 1, 0, -6]  
# نقاط التذكير في الاختبار (بالساعات): قبل 6 ساعات، قبل ساعة، وقت الاستحقاق، بعد 6 ساعات

# ===== قيود حجم/ذاكرة افتراضية معقولة =====
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024    # 5MB للـ JSON
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024


# === عام ===
RATES_HTTP_TIMEOUT = int(os.getenv("RATES_HTTP_TIMEOUT", "8"))  # ثواني
RATES_HTTP_RETRIES = int(os.getenv("RATES_HTTP_RETRIES", "2"))

# === تفعيل/تعطيل مزوّدين (افتراضيًا معطّل) ===
ENABLE_FX_PROVIDER = os.getenv("ENABLE_FX_PROVIDER", "0") == "1"
ENABLE_METALS_PROVIDER = os.getenv("ENABLE_METALS_PROVIDER", "0") == "1"

# === FX Provider (مثال: exchangerate.host - مجاني) ===
FX_PROVIDER_NAME = os.getenv("FX_PROVIDER_NAME", "exchangerate_host")
FX_BASE_CURRENCY = os.getenv("FX_BASE_CURRENCY", "USD")
# أزواج مهمة مسبقًا (يمكن توسيعها)
FX_TARGETS = os.getenv("FX_TARGETS", "SYP,MYR,USD").split(",")

# === Metals Provider (مثال: GoldAPI.io أو metals-api.com) ===
METALS_PROVIDER_NAME = os.getenv("METALS_PROVIDER_NAME", "none")
# مثال GoldAPI:
GOLDAPI_API_KEY = os.getenv("GOLDAPI_API_KEY", "")
# أو metals-api.com:
METALSAPI_ACCESS_KEY = os.getenv("METALSAPI_ACCESS_KEY", "")
METALSAPI_BASE = os.getenv("METALSAPI_BASE", "USD")


