"""
auth/ldap_auth.py
Autenticación contra Active Directory (NTLM) u OpenLDAP (SIMPLE).
Si DEMO_MODE=true, valida contra las credenciales hardcodeadas en settings.
"""
from typing import Optional, Dict, Any
from config.settings import (
    DEMO_MODE, DEMO_USERS,
    LDAP_SERVER, LDAP_PORT, LDAP_BASE_DN,
    LDAP_DOMAIN, LDAP_USE_SSL, LDAP_AUTH_METHOD,
    LDAP_USERS_OU, LDAP_GROUPS_OU,
    LDAP_ADMIN_DN, LDAP_ADMIN_PASSWORD,
    SYSTEM_ADMIN_USERNAME, SYSTEM_ADMIN_PASSWORD,
)


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    if not username or not password:
        return None
    # Admin del sistema: siempre funciona, independiente de LDAP o DEMO_MODE
    if username.lower() == SYSTEM_ADMIN_USERNAME.lower():
        return _system_admin_authenticate(password)
    if DEMO_MODE:
        return _demo_authenticate(username, password)
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


def _demo_authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    user_data = DEMO_USERS.get(username.lower())
    if user_data and user_data["password"] == password:
        return {
            "username": username.lower(),
            "nombre":   user_data["nombre"],
            "email":    user_data["email"],
            "rol":      user_data["rol"],
        }
    return None


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
        rol    = _resolve_role_from_memberof(entry.memberOf) if entry.memberOf else "Publicador"
        conn.unbind()
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
