"""
config/settings.py
Configuración central del proyecto Data Gatekeeper.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------
# Modo demo: True = no necesita LDAP ni BD reales, útil para desarrollo
# DEMO_MODE       controla el LOGIN (True = usuarios hardcodeados, sin AD)
# REAL_CATALOGS   controla los CATÁLOGOS (True = lee de SingleStore, False = mock)
#                 Si no se define, sigue el valor contrario de DEMO_MODE
# ------------------------------------------------------------------
DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() == "true"
REAL_CATALOGS: bool = os.getenv("REAL_CATALOGS", str(not DEMO_MODE)).lower() == "true"

# ------------------------------------------------------------------
# LDAP / Active Directory
# ------------------------------------------------------------------
LDAP_SERVER: str   = os.getenv("LDAP_SERVER",   "ldap://ad.baustro.fin.ec")
LDAP_PORT:   int   = int(os.getenv("LDAP_PORT", "389"))
LDAP_BASE_DN: str  = os.getenv("LDAP_BASE_DN",  "DC=baustro,DC=fin,DC=ec")
LDAP_DOMAIN: str   = os.getenv("LDAP_DOMAIN",   "BAUSTRO")
LDAP_USE_SSL: bool = os.getenv("LDAP_USE_SSL", "false").lower() == "true"

# ------------------------------------------------------------------
# SingleStore
# ------------------------------------------------------------------
SS_HOST:     str = os.getenv("SS_HOST",     "localhost")
SS_PORT:     int = int(os.getenv("SS_PORT", "3306"))
SS_USER:     str = os.getenv("SS_USER",     "admin")
SS_PASSWORD: str = os.getenv("SS_PASSWORD", "")
SS_DATABASE: str = os.getenv("SS_DATABASE", "gatekeeper_meta")

# ------------------------------------------------------------------
# Auditoría / Almacenamiento
# ------------------------------------------------------------------
AUDIT_STORAGE_PATH: str = os.getenv("AUDIT_STORAGE_PATH", "./audit_storage")
MAX_FILE_SIZE_MB:   int = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
MAX_ROWS_IN_MEMORY: int = int(os.getenv("MAX_ROWS_IN_MEMORY", "500000"))

# ------------------------------------------------------------------
# Credenciales demo (solo cuando DEMO_MODE=true)
# ------------------------------------------------------------------
DEMO_USERS = {
    "vcastro": {
        "password": "demo123",
        "nombre":   "Víctor Castro",
        "email":    "vcastro@baustro.fin.ec",
        "rol":      "Publicador",
    },
    "admin": {
        "password": "admin123",
        "nombre":   "Administrador",
        "email":    "admin@baustro.fin.ec",
        "rol":      "Admin",
    },
}
