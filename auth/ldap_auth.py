"""
auth/ldap_auth.py
Autenticación contra Active Directory (NTLM) u OpenLDAP (SIMPLE).
"""
import os
from typing import Optional, Dict, Any

from config import settings


def _setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, os.getenv(name, default))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


LDAP_SERVER = _setting("LDAP_SERVER")
LDAP_PORT = int(_setting("LDAP_PORT", 389))
LDAP_BASE_DN = _setting("LDAP_BASE_DN")
LDAP_DOMAIN = _setting("LDAP_DOMAIN")
LDAP_USE_SSL = _as_bool(_setting("LDAP_USE_SSL", False))
LDAP_AUTH_METHOD = str(_setting("LDAP_AUTH_METHOD", "NTLM")).upper()
LDAP_USERS_OU = _setting("LDAP_USERS_OU")
LDAP_GROUPS_OU = _setting("LDAP_GROUPS_OU")
LDAP_ADMIN_DN = _setting("LDAP_ADMIN_DN", _setting("LDAP_BIND_USER"))
LDAP_ADMIN_PASSWORD = _setting("LDAP_ADMIN_PASSWORD", _setting("LDAP_BIND_PASSWORD"))
SYSTEM_ADMIN_USERNAME = _setting("SYSTEM_ADMIN_USERNAME", "admin")
SYSTEM_ADMIN_PASSWORD = _setting("SYSTEM_ADMIN_PASSWORD")
LDAP_REQUIRED_GROUP = _setting("LDAP_REQUIRED_GROUP", "")


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    if not username or not password:
        return None
    # Admin del sistema: siempre funciona, independiente de LDAP
    if username.lower() == SYSTEM_ADMIN_USERNAME.lower():
        return _system_admin_authenticate(password)
    if LDAP_AUTH_METHOD == "SIMPLE":
        return _simple_authenticate(username, password)
    return _ntlm_authenticate(username, password)


def _system_admin_authenticate(password: str) -> Optional[Dict[str, Any]]:
    if not SYSTEM_ADMIN_PASSWORD or password != SYSTEM_ADMIN_PASSWORD:
        return None
    return {
        "username": SYSTEM_ADMIN_USERNAME.lower(),
        "nombre":   "Administrador",
        "email":    f"{SYSTEM_ADMIN_USERNAME.lower()}@baustro.fin.ec",
        "rol":      "Admin",
    }


def _ntlm_authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Autenticación real contra Active Directory con NTLM."""
    try:
        from ldap3 import Server, Connection, ALL, NTLM, SUBTREE
        server   = Server(LDAP_SERVER, port=LDAP_PORT, use_ssl=LDAP_USE_SSL, get_info=ALL)
        conn     = Connection(server, user=f"{LDAP_DOMAIN}\\{username}", password=password, authentication=NTLM)

        if not conn.bind():
            return None

        conn.search(
            search_base=LDAP_BASE_DN,
            search_filter=f"(sAMAccountName={username})",
            attributes=["cn", "mail", "memberOf"],
        )
        if not conn.entries:
            conn.unbind()
            return None

        entry  = conn.entries[0]
        nombre = str(entry.cn)   if entry.cn   else username
        email  = str(entry.mail) if entry.mail else f"{username}@baustro.fin.ec"
        member_of_str = str(entry.memberOf) if entry.memberOf else ""
        rol    = _resolve_role_from_memberof(entry.memberOf) if entry.memberOf else "Publicador"
        conn.unbind()

        if LDAP_REQUIRED_GROUP and LDAP_REQUIRED_GROUP.upper() not in member_of_str.upper():
            return None

        return {"username": username.lower(), "nombre": nombre, "email": email, "rol": rol}

    except Exception as exc:
        print(f"[LDAP NTLM ERROR] {exc}")
        return None


def _simple_authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Autenticación contra OpenLDAP local. Admin busca el usuario, luego se verifica la contraseña."""
    try:
        from ldap3 import Server, Connection, ALL, SIMPLE, SUBTREE
        server  = Server(LDAP_SERVER, port=LDAP_PORT, use_ssl=LDAP_USE_SSL, get_info=ALL)
        user_dn = f"uid={username},{LDAP_USERS_OU}"

        # 1. Bind como admin para buscar atributos y grupos
        admin_conn = Connection(server, user=LDAP_ADMIN_DN, password=LDAP_ADMIN_PASSWORD, authentication=SIMPLE)
        if not admin_conn.bind():
            print(f"[LDAP SIMPLE ERROR] Admin bind falló: {admin_conn.last_error}")
            return None

        admin_conn.search(
            search_base=LDAP_USERS_OU,
            search_filter=f"(uid={username})",
            attributes=["cn", "mail"],
        )
        if not admin_conn.entries:
            admin_conn.unbind()
            return None

        entry  = admin_conn.entries[0]
        nombre = str(entry.cn)   if entry.cn   else username
        email  = str(entry.mail) if entry.mail else f"{username}@baustro.fin.ec"

        admin_conn.search(
            search_base=LDAP_GROUPS_OU,
            search_filter=f"(&(objectClass=groupOfNames)(member={user_dn}))",
            attributes=["cn"],
        )
        groups = [str(e.cn) for e in admin_conn.entries]
        rol    = _resolve_role_from_groups(groups)
        admin_conn.unbind()

        if LDAP_REQUIRED_GROUP and LDAP_REQUIRED_GROUP.upper() not in [g.upper() for g in groups]:
            return None

        # 2. Verificar contraseña del usuario con su propio bind
        user_conn = Connection(server, user=user_dn, password=password, authentication=SIMPLE)
        if not user_conn.bind():
            return None
        user_conn.unbind()

        return {"username": username.lower(), "nombre": nombre, "email": email, "rol": rol}

    except Exception as exc:
        print(f"[LDAP SIMPLE ERROR] {exc}")
        return None


def _resolve_role_from_memberof(member_of: Any) -> str:
    groups_str = str(member_of).upper()
    if "GATEKEEPER_ADMIN" in groups_str:
        return "Admin"
    return "Publicador"


def _resolve_role_from_groups(groups: list) -> str:
    groups_upper = [g.upper() for g in groups]
    if "GATEKEEPER_ADMIN" in groups_upper:
        return "Admin"
    return "Publicador"
