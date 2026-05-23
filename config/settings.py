
import os
from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.getenv("APP_ENV", "production")
APP_PORT = int(os.getenv("APP_PORT", 8501))

# ==========================================================
# SINGLESTORE
# ==========================================================

SS_USE_BALANCER = os.getenv("SS_USE_BALANCER", "false").lower() == "true"
SS_DIRECT_HOST = os.getenv("SS_DIRECT_HOST", os.getenv("SS_HOST"))
SS_BALANCER_HOST = os.getenv("SS_BALANCER_HOST")

SS_HOST = (
    SS_BALANCER_HOST
    if SS_USE_BALANCER and SS_BALANCER_HOST
    else SS_DIRECT_HOST
)
SS_PORT = int(os.getenv("SS_PORT", 3306))

SS_DATABASE = os.getenv("SS_DATABASE")

SS_USER = os.getenv("SS_USER")
SS_PASSWORD = os.getenv("SS_PASSWORD")

DB_NAME_FILTERS = os.getenv("DB_NAME_FILTERS", "")

# ==========================================================
# HIVE
# ==========================================================

HIVE_HOST = os.getenv("HIVE_HOST")
HIVE_PORT = int(os.getenv("HIVE_PORT", 10000))

HIVE_DATABASE = os.getenv("HIVE_DATABASE", "default")

HIVE_USER = os.getenv("HIVE_USER")
HIVE_PASSWORD = os.getenv("HIVE_PASSWORD")

# ==========================================================
# LDAP / ACTIVE DIRECTORY
# ==========================================================

LDAP_SERVER = os.getenv("LDAP_SERVER")

LDAP_PORT = int(os.getenv("LDAP_PORT", 389))

LDAP_DOMAIN = os.getenv("LDAP_DOMAIN")

LDAP_BIND_USER = os.getenv("LDAP_BIND_USER")
LDAP_BIND_PASSWORD = os.getenv("LDAP_BIND_PASSWORD")

LDAP_USE_SSL = (
    os.getenv("LDAP_USE_SSL", "false").lower() == "true"
)

LDAP_AUTH_METHOD = os.getenv(
    "LDAP_AUTH_METHOD",
    "NTLM"
)

LDAP_BASE_DN = os.getenv("LDAP_BASE_DN")
LDAP_USERS_OU = os.getenv("LDAP_USERS_OU")
LDAP_GROUPS_OU = os.getenv("LDAP_GROUPS_OU")
LDAP_ADMIN_DN = os.getenv("LDAP_ADMIN_DN", LDAP_BIND_USER)
LDAP_ADMIN_PASSWORD = os.getenv("LDAP_ADMIN_PASSWORD", LDAP_BIND_PASSWORD)
LDAP_REQUIRED_GROUP = os.getenv("LDAP_REQUIRED_GROUP", "")

# ==========================================================
# ADMIN LOCAL
# ==========================================================

SYSTEM_ADMIN_USERNAME = os.getenv("SYSTEM_ADMIN_USERNAME", "admin")
SYSTEM_ADMIN_PASSWORD = os.getenv("SYSTEM_ADMIN_PASSWORD")

# ==========================================================
# TABLAS METADATA
# ==========================================================

TBL_PROYECTOS = os.getenv("TBL_PROYECTOS", "proyectos")
TBL_CATALOGOS = os.getenv("TBL_CATALOGOS", "catalogos_config")
TBL_USUARIOS = os.getenv("TBL_USUARIOS", "usuarios")
TBL_PERMISOS = os.getenv("TBL_PERMISOS", "permisos_catalogo")
TBL_LOG_AUDITORIA = os.getenv("TBL_LOG_AUDITORIA", "log_auditoria")

# ==========================================================
# AUDITORÍA
# ==========================================================

AUDIT_STORAGE_PATH = os.getenv(
    "AUDIT_STORAGE_PATH",
    "/app/audit_storage"
)
AUDIT_STORAGE_READONLY_AFTER_WRITE = (
    os.getenv("AUDIT_STORAGE_READONLY_AFTER_WRITE", "true").lower() == "true"
)
AUDIT_STORAGE_DIR_MODE = os.getenv("AUDIT_STORAGE_DIR_MODE", "750")
AUDIT_STORAGE_FILE_MODE = os.getenv("AUDIT_STORAGE_FILE_MODE", "440")

# ==========================================================
# LOGGING
# ==========================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ==========================================================
# ALERTAS
# ==========================================================

ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "false").lower() == "true"
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
ALERT_ON_SUCCESS = os.getenv("ALERT_ON_SUCCESS", "false").lower() == "true"

# ==========================================================
# VALIDACIONES
# ==========================================================

MAX_UPLOAD_SIZE_MB = int(
    os.getenv("MAX_UPLOAD_SIZE_MB", 100)
)
MAX_FILE_SIZE_MB = int(
    os.getenv("MAX_FILE_SIZE_MB", MAX_UPLOAD_SIZE_MB)
)
MAX_ROWS_IN_MEMORY = int(
    os.getenv("MAX_ROWS_IN_MEMORY", 500000)
)
