"""
utils/user_service.py
Registro automático de usuarios en SingleStore al iniciar sesión.
"""
from __future__ import annotations
import os
from typing import Dict, Any

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


def get_or_register_user(username: str, nombre: str, email: str) -> Dict[str, Any]:
    """
    Si el usuario existe en DB → retorna su rol actual y actualiza ultimo_acceso.
    Si no existe → lo registra como Publicador y retorna sus datos.
    """
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
            return {"username": username, "nombre": nombre, "email": email, "rol": rol, "activo": bool(activo)}

        # Primer login — registrar como Publicador
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {TBL_USUARIOS} (username, nombre, email, rol, activo, ultimo_acceso) "
                f"VALUES (%s, %s, %s, 'Publicador', 1, NOW())",
                (username, nombre, email),
            )
        conn.commit()
        return {"username": username, "nombre": nombre, "email": email, "rol": "Publicador", "activo": True}
