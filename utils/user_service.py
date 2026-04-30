"""
utils/user_service.py
Registro automático de usuarios en SingleStore al iniciar sesión.
"""
from __future__ import annotations
from typing import Dict, Any


def _connect():
    from config.settings import SS_HOST, SS_PORT, SS_USER, SS_PASSWORD, SS_DATABASE
    import singlestoredb as s2
    return s2.connect(
        host=SS_HOST, port=SS_PORT,
        user=SS_USER, password=SS_PASSWORD,
        database=SS_DATABASE,
    )


def get_all_usuarios() -> list:
    from config.settings import TBL_USUARIOS
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT username, nombre, email, rol, activo, ultimo_acceso "
                f"FROM {TBL_USUARIOS} ORDER BY rol DESC, username"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def update_user_rol(username: str, rol: str) -> None:
    from config.settings import TBL_USUARIOS
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {TBL_USUARIOS} SET rol = %s WHERE username = %s",
                (rol, username),
            )
        conn.commit()


def toggle_user_activo(username: str, activo: bool) -> None:
    from config.settings import TBL_USUARIOS
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
    from config.settings import TBL_USUARIOS
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
