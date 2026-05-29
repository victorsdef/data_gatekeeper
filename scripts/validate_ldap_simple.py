"""
scripts/validate_ldap_simple.py

Validador manual para ambientes LDAP con autenticacion SIMPLE.

Este script esta pensado para OpenLDAP o ambientes de prueba donde primero se
necesita una cuenta tecnica para buscar usuarios y grupos.

Por que existe este script:

- porque en modo SIMPLE el flujo no es igual al de Active Directory NTLM;
- porque primero debe probarse la cuenta tecnica;
- porque ayuda a identificar si el problema esta en conectividad, bind tecnico
  o autenticacion final del usuario.

Que valida:

- `server`: conectividad al host y puerto LDAP;
- `admin-bind`: autenticacion de la cuenta tecnica;
- `user-auth`: autenticacion de un usuario final despues de consultar el arbol.

Uso:
  python scripts/validate_ldap_simple.py --show-config
  python scripts/validate_ldap_simple.py --check server
  python scripts/validate_ldap_simple.py --check admin-bind
  python scripts/validate_ldap_simple.py --check user-auth --username tu_usuario --password tu_clave
"""
from __future__ import annotations

import argparse
import sys
from typing import Any


# ==========================================================
# CONFIGURACION  PARA SIMPLE
# ==========================================================

LDAP_SERVER = "ldap://ldap"
LDAP_PORT = 389
LDAP_USE_SSL = False
LDAP_USERS_OU = "ou=users,dc=baustro,dc=fin,dc=ec"
LDAP_GROUPS_OU = "ou=groups,dc=baustro,dc=fin,dc=ec"
LDAP_ADMIN_DN = "cn=admin,dc=baustro,dc=fin,dc=ec"
LDAP_ADMIN_PASSWORD = "admin"
LDAP_BIND_USER = "cn=admin,dc=baustro,dc=fin,dc=ec"
LDAP_BIND_PASSWORD = "admin"
LDAP_REQUIRED_GROUP = ""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "<vacio>"
    return "<definido>"


def _mask_dn(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "<vacio>"
    if len(text) <= 12:
        return "<definido>"
    return text[:6] + "..." + text[-6:]


def _build_server():
    from ldap3 import ALL, Server

    ldap_server = LDAP_SERVER
    if "://" in ldap_server:
        ldap_server = ldap_server.split("://", 1)[1]

    return Server(
        ldap_server,
        port=int(LDAP_PORT),
        use_ssl=_as_bool(LDAP_USE_SSL),
        get_info=ALL,
    )


def _print_config_summary() -> None:
    print("Configuracion SIMPLE del script:")
    print(f"  LDAP_SERVER={LDAP_SERVER}")
    print(f"  LDAP_PORT={LDAP_PORT}")
    print(f"  LDAP_USE_SSL={LDAP_USE_SSL}")
    print(f"  LDAP_USERS_OU={LDAP_USERS_OU}")
    print(f"  LDAP_GROUPS_OU={LDAP_GROUPS_OU}")
    print(f"  LDAP_REQUIRED_GROUP={LDAP_REQUIRED_GROUP}")
    print(f"  LDAP_ADMIN_DN={_mask_dn(LDAP_ADMIN_DN)}")
    print(f"  LDAP_BIND_USER={_mask_dn(LDAP_BIND_USER)}")
    print(f"  LDAP_ADMIN_PASSWORD={_mask_secret(LDAP_ADMIN_PASSWORD)}")
    print(f"  LDAP_BIND_PASSWORD={_mask_secret(LDAP_BIND_PASSWORD)}")


def _validate_required_vars() -> int:
    required = {
        "LDAP_SERVER": LDAP_SERVER,
        "LDAP_PORT": LDAP_PORT,
        "LDAP_USERS_OU": LDAP_USERS_OU,
        "LDAP_GROUPS_OU": LDAP_GROUPS_OU,
        "LDAP_ADMIN_DN": LDAP_ADMIN_DN,
        "LDAP_ADMIN_PASSWORD": LDAP_ADMIN_PASSWORD,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        print("ERROR: faltan variables requeridas en el script:")
        for item in missing:
            print(f"  - {item}")
        return 1
    return 0


def check_server() -> int:
    from ldap3 import Connection

    print("Probando conectividad LDAP SIMPLE...")
    server = _build_server()
    conn = Connection(server)
    if conn.open():
        print("OK: se pudo abrir conexion con el servidor LDAP.")
        conn.unbind()
        return 0
    print("ERROR: no se pudo abrir conexion con el servidor LDAP.")
    return 1


def check_admin_bind() -> int:
    from ldap3 import Connection, SIMPLE

    admin_dn = LDAP_ADMIN_DN or LDAP_BIND_USER
    admin_password = LDAP_ADMIN_PASSWORD or LDAP_BIND_PASSWORD

    print("Probando bind tecnico LDAP...")
    server = _build_server()
    conn = Connection(server, user=admin_dn, password=admin_password, authentication=SIMPLE)
    if conn.bind():
        print("OK: bind tecnico exitoso.")
        conn.unbind()
        return 0
    print(f"ERROR: bind tecnico fallido. Detalle: {conn.last_error}")
    return 1


def _resolve_role_from_groups(groups: list[str]) -> str:
    groups_upper = [g.upper() for g in groups]
    if "GATEKEEPER_ADMIN" in groups_upper:
        return "Admin"
    return "Publicador"


def check_user_auth(username: str, password: str) -> int:
    from ldap3 import Connection, SIMPLE

    if not username or not password:
        print("ERROR: debes enviar --username y --password.")
        return 1

    print(f"Probando autenticacion SIMPLE de usuario '{username}'...")
    server = _build_server()
    user_dn = f"uid={username},{LDAP_USERS_OU}"

    admin_conn = Connection(
        server,
        user=LDAP_ADMIN_DN,
        password=LDAP_ADMIN_PASSWORD,
        authentication=SIMPLE,
    )
    if not admin_conn.bind():
        print(f"ERROR: bind tecnico fallido. Detalle: {admin_conn.last_error}")
        return 1

    admin_conn.search(
        search_base=LDAP_USERS_OU,
        search_filter=f"(uid={username})",
        attributes=["cn", "mail"],
    )
    if not admin_conn.entries:
        admin_conn.unbind()
        print("ERROR: no se encontro el usuario en LDAP.")
        return 1

    entry = admin_conn.entries[0]
    nombre = str(entry.cn) if entry.cn else username
    email = str(entry.mail) if entry.mail else f"{username}@local"

    admin_conn.search(
        search_base=LDAP_GROUPS_OU,
        search_filter=f"(&(objectClass=groupOfNames)(member={user_dn}))",
        attributes=["cn"],
    )
    groups = [str(e.cn) for e in admin_conn.entries]
    rol = _resolve_role_from_groups(groups)
    admin_conn.unbind()

    if LDAP_REQUIRED_GROUP and LDAP_REQUIRED_GROUP.upper() not in [g.upper() for g in groups]:
        print("ERROR: el usuario existe, pero no pertenece al grupo requerido.")
        return 1

    user_conn = Connection(server, user=user_dn, password=password, authentication=SIMPLE)
    if not user_conn.bind():
        print("ERROR: autenticacion final del usuario fallida.")
        print(f"Detalle LDAP: {user_conn.last_error}")
        return 1
    user_conn.unbind()

    print("OK: autenticacion exitosa.")
    print(f"  username={username.lower()}")
    print(f"  nombre={nombre}")
    print(f"  email={email}")
    print(f"  rol={rol}")
    print(f"  grupos={groups if groups else []}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida LDAP en modo SIMPLE con variables quemadas.")
    parser.add_argument("--check", choices=["server", "admin-bind", "user-auth"], default="server")
    parser.add_argument("--username", help="Usuario para prueba de autenticacion.")
    parser.add_argument("--password", help="Password para prueba de autenticacion.")
    parser.add_argument("--show-config", action="store_true", help="Muestra la configuracion quemada del script.")
    args = parser.parse_args()

    try:
        validation_result = _validate_required_vars()
        if validation_result != 0:
            return validation_result

        if args.show_config:
            _print_config_summary()

        if args.check == "server":
            return check_server()
        if args.check == "admin-bind":
            return check_admin_bind()
        return check_user_auth(args.username or "", args.password or "")
    except Exception as exc:
        print(f"ERROR: fallo inesperado validando LDAP SIMPLE: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
