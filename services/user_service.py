"""
services/user_service.py
Registro automático de usuarios en SingleStore al iniciar sesión.
"""
from __future__ import annotations
import os
from typing import Dict, Any, List

from config import settings


def _setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, os.getenv(name, default))


SS_HOST = _setting("SS_HOST")
SS_PORT = int(_setting("SS_PORT", 3306))
SS_USER = _setting("SS_USER")
SS_PASSWORD = _setting("SS_PASSWORD")
SS_DATABASE = _setting("SS_DATABASE")
TBL_USUARIOS = _setting("TBL_USUARIOS", "usuarios")


def _connect():
    import singlestoredb as s2
    return s2.connect(
        host=SS_HOST, port=SS_PORT,
        user=SS_USER, password=SS_PASSWORD,
        database=SS_DATABASE,
    )


def get_all_usuarios() -> list:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT username, nombre, email, rol, activo, ultimo_acceso "
                f"FROM {TBL_USUARIOS} ORDER BY rol DESC, username"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def export_users_bundle() -> Dict[str, Any]:
    users = get_all_usuarios()
    normalized: List[Dict[str, Any]] = []
    for user in users:
        normalized.append(
            {
                "username": str(user["username"]),
                "nombre": user.get("nombre") or "",
                "email": user.get("email") or "",
                "rol": user.get("rol") or "Publicador",
                "activo": bool(user.get("activo")),
            }
        )
    return {"version": 1, "usuarios": normalized}


def import_users_bundle(bundle: Dict[str, Any], overwrite_existing: bool = False) -> Dict[str, int]:
    if not isinstance(bundle, dict):
        raise ValueError("El backup de usuarios debe ser un objeto JSON válido.")

    usuarios = bundle.get("usuarios", []) or []
    if not isinstance(usuarios, list):
        raise ValueError("El backup de usuarios debe contener una lista válida de usuarios.")

    created = 0
    updated = 0
    skipped = 0

    with _connect() as conn:
        with conn.cursor() as cur:
            for user in usuarios:
                if not isinstance(user, dict):
                    raise ValueError("Cada usuario del backup debe ser un objeto.")
                username = str(user.get("username", "")).strip().lower()
                if not username:
                    raise ValueError("Cada usuario del backup debe tener username.")
                if not str(user.get("rol", "")).strip():
                    raise ValueError(f"El usuario `{username}` debe tener rol.")
                cur.execute(f"SELECT 1 FROM {TBL_USUARIOS} WHERE username = %s", (username,))
                exists = cur.fetchone() is not None
                if exists:
                    if overwrite_existing:
                        cur.execute(
                            f"""
                            UPDATE {TBL_USUARIOS}
                            SET nombre=%s, email=%s, rol=%s, activo=%s
                            WHERE username=%s
                            """,
                            (
                                str(user.get("nombre") or ""),
                                str(user.get("email") or ""),
                                str(user.get("rol") or "Publicador"),
                                1 if bool(user.get("activo", True)) else 0,
                                username,
                            ),
                        )
                        updated += 1
                    else:
                        skipped += 1
                else:
                    cur.execute(
                        f"""
                        INSERT INTO {TBL_USUARIOS} (username, nombre, email, rol, activo, ultimo_acceso)
                        VALUES (%s, %s, %s, %s, %s, NULL)
                        """,
                        (
                            username,
                            str(user.get("nombre") or ""),
                            str(user.get("email") or ""),
                            str(user.get("rol") or "Publicador"),
                            1 if bool(user.get("activo", True)) else 0,
                        ),
                    )
                    created += 1
        conn.commit()

    return {"created": created, "updated": updated, "skipped": skipped}


def update_user_rol(username: str, rol: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {TBL_USUARIOS} SET rol = %s WHERE username = %s",
                (rol, username),
            )
        conn.commit()


def toggle_user_activo(username: str, activo: bool) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {TBL_USUARIOS} SET activo = %s WHERE username = %s",
                (1 if activo else 0, username),
            )
        conn.commit()


def get_or_register_user(
    username: str,
    nombre: str,
    email: str,
    rol_inicial: str = "Publicador",
) -> Dict[str, Any]:
    """
    Si el usuario existe en DB → retorna su rol actual y actualiza ultimo_acceso.
    Si no existe → lo registra con el rol autenticado y retorna sus datos.
    """
    rol_inicial = rol_inicial if rol_inicial in {"Admin", "Publicador"} else "Publicador"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT rol, activo FROM {TBL_USUARIOS} WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()

        if row:
            rol, activo = row
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {TBL_USUARIOS} SET ultimo_acceso = NOW() WHERE username = %s",
                    (username,),
                )
            conn.commit()
            return {
                "username": username,
                "nombre": nombre,
                "email": email,
                "rol": rol,
                "activo": bool(activo),
            }

        # Primer login: respeta el rol resuelto por LDAP/admin local.
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {TBL_USUARIOS} (username, nombre, email, rol, activo, ultimo_acceso) "
                f"VALUES (%s, %s, %s, %s, 1, NOW())",
                (username, nombre, email, rol_inicial),
            )
        conn.commit()
        return {
            "username": username,
            "nombre": nombre,
            "email": email,
            "rol": rol_inicial,
            "activo": True,
        }
