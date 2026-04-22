"""
auth/ldap_auth.py
Autenticación contra Active Directory usando ldap3.
Si DEMO_MODE=true, valida contra las credenciales hardcodeadas en settings.
"""
from typing import Optional, Dict, Any
from config.settings import (
    DEMO_MODE, DEMO_USERS,
    LDAP_SERVER, LDAP_PORT, LDAP_BASE_DN,
    LDAP_DOMAIN, LDAP_USE_SSL,
)


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Autentica usuario contra AD.
    Retorna dict con info del usuario si es válido, None si falla.
    """
    if not username or not password:
        return None

    if DEMO_MODE:
        return _demo_authenticate(username, password)
    else:
        return _ldap_authenticate(username, password)


def _demo_authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Autenticación mock para desarrollo."""
    user_data = DEMO_USERS.get(username.lower())
    if user_data and user_data["password"] == password:
        return {
            "username": username.lower(),
            "nombre":   user_data["nombre"],
            "email":    user_data["email"],
            "rol":      user_data["rol"],
        }
    return None


def _ldap_authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Autenticación real contra Active Directory."""
    try:
        from ldap3 import Server, Connection, ALL, NTLM, SUBTREE
        from ldap3.core.exceptions import LDAPException

        server = Server(LDAP_SERVER, port=LDAP_PORT, use_ssl=LDAP_USE_SSL, get_info=ALL)
        bind_user = f"{LDAP_DOMAIN}\\{username}"

        conn = Connection(server, user=bind_user, password=password, authentication=NTLM)

        if not conn.bind():
            return None

        # Buscar atributos del usuario en el AD
        search_filter = f"(sAMAccountName={username})"
        conn.search(
            search_base=LDAP_BASE_DN,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["cn", "mail", "memberOf"],
        )

        if not conn.entries:
            conn.unbind()
            return None

        entry = conn.entries[0]
        nombre = str(entry.cn) if entry.cn else username
        email  = str(entry.mail) if entry.mail else f"{username}@baustro.fin.ec"
        rol    = _resolve_role(entry.memberOf) if entry.memberOf else "Publicador"

        conn.unbind()
        return {
            "username": username.lower(),
            "nombre":   nombre,
            "email":    email,
            "rol":      rol,
        }

    except Exception as exc:
        # En producción, loggear el error
        print(f"[LDAP ERROR] {exc}")
        return None


def _resolve_role(member_of: Any) -> str:
    """
    Mapea grupos del AD a roles de la aplicación.
    Ajustar los nombres de los grupos según el AD del banco.
    """
    groups_str = str(member_of).upper()
    if "GATEKEEPER_ADMIN" in groups_str:
        return "Admin"
    if "GATEKEEPER_PUBLICADOR" in groups_str:
        return "Publicador"
    return "Publicador"
