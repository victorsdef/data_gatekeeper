"""
config/settings.py
Configuración central del proyecto Data Gatekeeper.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------
# LDAP / Active Directory
# ------------------------------------------------------------------
LDAP_SERVER: str   = os.getenv("LDAP_SERVER",   "ldap://ad.baustro.fin.ec")
LDAP_PORT:   int   = int(os.getenv("LDAP_PORT", "389"))
LDAP_BASE_DN: str  = os.getenv("LDAP_BASE_DN",  "DC=baustro,DC=fin,DC=ec")
LDAP_DOMAIN: str   = os.getenv("LDAP_DOMAIN",   "BAUSTRO")
LDAP_USE_SSL: bool = os.getenv("LDAP_USE_SSL", "false").lower() == "true"
# NTLM = Active Directory real | SIMPLE = OpenLDAP / servidor de pruebas
# ------------------------------------------------------------------
# Admin del sistema (independiente de LDAP, siempre funciona)
# ------------------------------------------------------------------
SYSTEM_ADMIN_USERNAME: str = os.getenv("SYSTEM_ADMIN_USERNAME", "admin")
SYSTEM_ADMIN_PASSWORD: str = os.getenv("SYSTEM_ADMIN_PASSWORD", "")

# Grupo LDAP requerido para acceder a la app (vacío = sin restricción de grupo)
LDAP_REQUIRED_GROUP: str = os.getenv("LDAP_REQUIRED_GROUP", "")

LDAP_AUTH_METHOD: str    = os.getenv("LDAP_AUTH_METHOD",    "NTLM")
LDAP_USERS_OU: str       = os.getenv("LDAP_USERS_OU",       "ou=users,DC=baustro,DC=fin,DC=ec")
LDAP_GROUPS_OU: str      = os.getenv("LDAP_GROUPS_OU",      "ou=groups,DC=baustro,DC=fin,DC=ec")
LDAP_ADMIN_DN: str       = os.getenv("LDAP_ADMIN_DN",       "cn=admin,DC=baustro,DC=fin,DC=ec")
LDAP_ADMIN_PASSWORD: str = os.getenv("LDAP_ADMIN_PASSWORD", "")

# ------------------------------------------------------------------
# SingleStore
# ------------------------------------------------------------------
SS_HOST:     str = os.getenv("SS_HOST",     "localhost")
SS_PORT:     int = int(os.getenv("SS_PORT", "3306"))
SS_USER:     str = os.getenv("SS_USER",     "admin")
SS_PASSWORD: str = os.getenv("SS_PASSWORD", "")
SS_DATABASE: str = os.getenv("SS_DATABASE", "gatekeeper_meta")

# ------------------------------------------------------------------
# Hive
# ------------------------------------------------------------------
HIVE_HOST:     str = os.getenv("HIVE_HOST",     "localhost")
HIVE_PORT:     int = int(os.getenv("HIVE_PORT", "10000"))
HIVE_USER:     str = os.getenv("HIVE_USER",     "hive")
HIVE_DATABASE: str = os.getenv("HIVE_DATABASE", "default")

# ------------------------------------------------------------------
# Tablas de metadatos (gatekeeper_meta)
# ------------------------------------------------------------------
TBL_PROYECTOS:       str = "proyectos"
TBL_CATALOGOS:       str = "catalogos_config"
TBL_USUARIOS:        str = "usuarios"
TBL_PERMISOS:        str = "permisos_catalogo"
TBL_LOG_AUDITORIA:   str = "log_auditoria"

# ------------------------------------------------------------------
# Auditoría / Almacenamiento
# ------------------------------------------------------------------
AUDIT_STORAGE_PATH: str = os.getenv("AUDIT_STORAGE_PATH", "./audit_storage")
MAX_FILE_SIZE_MB:   int = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
MAX_ROWS_IN_MEMORY: int = int(os.getenv("MAX_ROWS_IN_MEMORY", "500000"))
