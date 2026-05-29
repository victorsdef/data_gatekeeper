"""
scripts/validate_ldap_ntlm.py

Validador manual para ambientes LDAP con autenticacion NTLM.

Este script esta pensado para Active Directory real, donde el usuario final se
autentica con el formato DOMINIO\\usuario.

Por que existe este script:

- porque en Active Directory el flujo real de autenticacion es diferente al de
  OpenLDAP;
- porque normalmente no se necesita una cuenta tecnica para autenticar al
  usuario final;
- porque permite probar conectividad y autenticacion real tal como lo hace la
  aplicacion en modo NTLM.

Que valida:

- `server`: conectividad al host y puerto LDAP;
- `user-auth`: autenticacion real de un usuario del dominio.

Uso:
  python scripts/validate_ldap_ntlm.py --show-config
  python scripts/validate_ldap_ntlm.py --check server
  python scripts/validate_ldap_ntlm.py --check user-auth --username tu_usuario --password tu_clave
"""
from __future__ import annotations

import argparse
import sys
from typing import Any


# ==========================================================
# CONFIGURACION PARA NTLM
# ==========================================================

LDAP_SERVER = "ldap://10.1.76.2"
LDAP_PORT = 389
LDAP_USE_SSL = False
LDAP_DOMAIN = "baustro.fin.ec"
LDAP_BASE_DN = "DC=baustro,DC=fin,DC=ec"
LDAP_REQUIRED_GROUP = ""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


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
    print("Configuracion NTLM del script:")
    print(f"  LDAP_SERVER={LDAP_SERVER}")
    print(f"  LDAP_PORT={LDAP_PORT}")
    print(f"  LDAP_USE_SSL={LDAP_USE_SSL}")
    print(f"  LDAP_DOMAIN={LDAP_DOMAIN}")
    print(f"  LDAP_BASE_DN={LDAP_BASE_DN}")
    print(f"  LDAP_REQUIRED_GROUP={LDAP_REQUIRED_GROUP}")


def _validate_required_vars() -> int:
    required = {
        "LDAP_SERVER": LDAP_SERVER,
        "LDAP_PORT": LDAP_PORT,
        "LDAP_DOMAIN": LDAP_DOMAIN,
        "LDAP_BASE_DN": LDAP_BASE_DN,
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

    print("Probando conectividad LDAP NTLM...")
    server = _build_server()
    conn = Connection(server)
    if conn.open():
        print("OK: se pudo abrir conexion con el servidor LDAP.")
        conn.unbind()
        return 0
    print("ERROR: no se pudo abrir conexion con el servidor LDAP.")
    return 1


def _resolve_role_from_memberof(member_of: Any) -> str:
    groups_str = str(member_of).upper()
    if "GATEKEEPER_ADMIN" in groups_str:
        return "Admin"
    return "Publicador"


def check_user_auth(username: str, password: str) -> int:
    from ldap3 import Connection, NTLM

    if not username or not password:
        print("ERROR: debes enviar --username y --password.")
        return 1

    print(f"Probando autenticacion NTLM de usuario '{username}'...")
    server = _build_server()
    conn = Connection(
        server,
        user=f"{LDAP_DOMAIN}\\{username}",
        password=password,
        authentication=NTLM,
    )

    if not conn.bind():
        print("ERROR: autenticacion fallida.")
        print(f"Detalle LDAP: {conn.last_error}")
        return 1

    conn.search(
        search_base=LDAP_BASE_DN,
        search_filter=f"(sAMAccountName={username})",
        attributes=["cn", "mail", "memberOf"],
    )
    if not conn.entries:
        conn.unbind()
        print("ERROR: autenticacion exitosa, pero no se encontro la entrada del usuario.")
        return 1

    entry = conn.entries[0]
    nombre = str(entry.cn) if entry.cn else username
    email = str(entry.mail) if entry.mail else f"{username}@baustro.fin.ec"
    member_of_str = str(entry.memberOf) if entry.memberOf else ""
    rol = _resolve_role_from_memberof(entry.memberOf) if entry.memberOf else "Publicador"
    conn.unbind()

    if LDAP_REQUIRED_GROUP and LDAP_REQUIRED_GROUP.upper() not in member_of_str.upper():
        print("ERROR: el usuario autentico, pero no pertenece al grupo requerido.")
        return 1

    print("OK: autenticacion exitosa.")
    print(f"  username={username.lower()}")
    print(f"  nombre={nombre}")
    print(f"  email={email}")
    print(f"  rol={rol}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida LDAP en modo NTLM con variables quemadas.")
    parser.add_argument("--check", choices=["server", "user-auth"], default="server")
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
        return check_user_auth(args.username or "", args.password or "")
    except Exception as exc:
        print(f"ERROR: fallo inesperado validando LDAP NTLM: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
