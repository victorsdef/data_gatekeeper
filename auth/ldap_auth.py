"""
auth/ldap_auth.py
Autenticacion contra Active Directory usando el binario del sistema `ldapsearch`.
"""
import os
import shutil
import subprocess
from typing import Optional, Dict, Any

from config import settings
from utils.logging_utils import get_logger


def _setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, os.getenv(name, default))


LDAP_SERVER = _setting("LDAP_SERVER")
LDAP_BASE_DN = _setting("LDAP_BASE_DN")
LDAP_DOMAIN = _setting("LDAP_DOMAIN")
SYSTEM_ADMIN_USERNAME = _setting("SYSTEM_ADMIN_USERNAME", "admin")
SYSTEM_ADMIN_PASSWORD = _setting("SYSTEM_ADMIN_PASSWORD")
LDAP_REQUIRED_GROUP = _setting("LDAP_REQUIRED_GROUP", "")
LDAP_SEARCH_ATTRIBUTE = _setting("LDAP_SEARCH_ATTRIBUTE", "sAMAccountName")
LDAPSEARCH_BIN = _setting("LDAPSEARCH_BIN", "ldapsearch")
logger = get_logger(__name__)


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    if not username or not password:
        logger.info("Intento de autenticacion rechazado por credenciales vacias.")
        return None

    if username.lower() == SYSTEM_ADMIN_USERNAME.lower():
        result = _system_admin_authenticate(password)
        logger.info(
            "Autenticacion de admin local usuario=%s success=%s",
            username.lower(),
            bool(result),
        )
        return result

    result = _ldapsearch_authenticate(username, password)
    logger.info(
        "Autenticacion LDAP usuario=%s metodo=LDAPSEARCH success=%s",
        username.lower(),
        bool(result),
    )
    return result


def _system_admin_authenticate(password: str) -> Optional[Dict[str, Any]]:
    if not SYSTEM_ADMIN_PASSWORD or password != SYSTEM_ADMIN_PASSWORD:
        return None
    return {
        "username": SYSTEM_ADMIN_USERNAME.lower(),
        "nombre": "Administrador",
        "email": f"{SYSTEM_ADMIN_USERNAME.lower()}@baustro.fin.ec",
        "rol": "Admin",
    }


def _ldapsearch_authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    server_uri = _ldap_server_uri()
    if not server_uri or not LDAP_BASE_DN:
        logger.warning("LDAPSEARCH no configurado: faltan LDAP_SERVER o LDAP_BASE_DN.")
        return None

    bind_user = _ldap_bind_user(username)
    command = [
        shutil.which(LDAPSEARCH_BIN) or LDAPSEARCH_BIN,
        "-x",
        "-LLL",
        "-H",
        server_uri,
        "-D",
        bind_user,
        "-w",
        password,
        "-b",
        LDAP_BASE_DN,
        f"({LDAP_SEARCH_ATTRIBUTE}={username})",
        "cn",
        "mail",
        "memberOf",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        logger.exception("No se encontro el binario ldapsearch para autenticacion.")
        return None
    except Exception:
        logger.exception("Fallo inesperado ejecutando ldapsearch para usuario '%s'.", username)
        return None

    if result.returncode != 0:
        logger.warning(
            "ldapsearch fallo para usuario=%s codigo=%s stderr=%s",
            username.lower(),
            result.returncode,
            (result.stderr or "").strip(),
        )
        return None

    attributes = _parse_ldapsearch_output(result.stdout)
    if not attributes:
        logger.warning(
            "ldapsearch autentico pero no devolvio atributos para usuario '%s'.",
            username,
        )
        return None

    nombre = _first_attr(attributes, "cn") or username
    email = _first_attr(attributes, "mail") or _default_email(username)
    member_of = attributes.get("memberOf", [])
    member_of_str = " ".join(member_of)
    rol = _resolve_role_from_memberof(member_of_str) if member_of else "Publicador"

    if LDAP_REQUIRED_GROUP and LDAP_REQUIRED_GROUP.upper() not in member_of_str.upper():
        logger.info("Usuario '%s' autenticado pero sin grupo requerido.", username.lower())
        return None

    return {
        "username": username.lower(),
        "nombre": nombre,
        "email": email,
        "rol": rol,
    }


def _resolve_role_from_memberof(member_of: Any) -> str:
    groups_str = str(member_of).upper()
    if "GATEKEEPER_ADMIN" in groups_str:
        return "Admin"
    return "Publicador"


def _ldap_server_uri() -> str:
    return str(LDAP_SERVER or "").strip()


def _ldap_bind_user(username: str) -> str:
    domain = str(LDAP_DOMAIN or "").strip()
    if not domain or "@" in username or "\\" in username:
        return username
    return f"{username}@{domain}"


def _default_email(username: str) -> str:
    domain = str(LDAP_DOMAIN or "").strip() or "baustro.fin.ec"
    return f"{username}@{domain}"


def _parse_ldapsearch_output(output: str) -> Dict[str, list]:
    data: Dict[str, list] = {}
    current_key: Optional[str] = None
    current_value = ""

    def flush() -> None:
        nonlocal current_key, current_value
        if current_key is None:
            return
        data.setdefault(current_key, []).append(current_value)
        current_key = None
        current_value = ""

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            flush()
            continue
        if line.startswith("#"):
            continue
        if line.startswith(" "):
            current_value += line[1:]
            continue
        flush()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        current_value = value.strip()
    flush()
    return data


def _first_attr(attributes: Dict[str, list], name: str) -> str:
    values = attributes.get(name, [])
    return str(values[0]).strip() if values else ""
