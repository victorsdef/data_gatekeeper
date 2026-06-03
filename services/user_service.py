"""
services/user_service.py
Registro automático de usuarios en SingleStore al iniciar sesión.
"""
from __future__ import annotations
import json
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
TBL_LOG_USUARIOS = _setting("TBL_LOG_USUARIOS", "log_usuarios")


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
            try:
                cur.execute(
                    f"""
                    SELECT
                        u.username,
                        u.nombre,
                        u.email,
                        u.rol,
                        u.activo,
                        MAX(
                            CASE
                                WHEN l.accion IN ('LOGIN_OK', 'LOGIN_DENIED_INACTIVE')
                                THEN l.timestamp_evento
                                ELSE NULL
                            END
                        ) AS ultimo_acceso
                    FROM {TBL_USUARIOS} u
                    LEFT JOIN {TBL_LOG_USUARIOS} l
                        ON l.username = u.username
                    GROUP BY u.username, u.nombre, u.email, u.rol, u.activo
                    ORDER BY u.rol DESC, u.username
                    """
                )
            except Exception:
                cur.execute(
                    f"""
                    SELECT username, nombre, email, rol, activo, NULL AS ultimo_acceso
                    FROM {TBL_USUARIOS}
                    ORDER BY rol DESC, username
                    """
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
                            SET nombre=%s, email=%s, rol=%s, activo=%s, fecha_actualizacion=NOW()
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
                        INSERT INTO {TBL_USUARIOS} (username, nombre, email, rol, activo)
                        VALUES (%s, %s, %s, %s, %s)
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


def _log_user_event(
    cur,
    username: str,
    accion: str,
    estado: str = "OK",
    rol: str = "",
    actor_username: str = "",
    detalle: Dict[str, Any] | None = None,
) -> None:
    try:
        cur.execute(
            f"""
            INSERT INTO {TBL_LOG_USUARIOS}
                (username, actor_username, accion, estado, rol, detalle_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                username,
                actor_username or None,
                accion,
                estado,
                rol or None,
                json.dumps(detalle or {}, ensure_ascii=False),
            ),
        )
    except Exception:
        # El log no debe impedir un login si el ambiente aun no fue migrado.
        pass


def create_or_promote_user(
    username: str,
    rol: str = "Admin",
    actor_username: str = "",
) -> Dict[str, Any]:
    """
    Crea un usuario manualmente o actualiza su rol si ya existe.

    Sirve para preautorizar usuarios antes de su primer login LDAP.
    Cuando el usuario inicie sesion, get_or_register_user respetara el rol
    guardado en esta tabla.
    """
    username = str(username or "").strip().lower()
    if not username:
        raise ValueError("Debes ingresar el usuario.")

    rol = rol if rol in {"Admin", "Publicador"} else "Publicador"

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT rol FROM {TBL_USUARIOS} WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()

            if row:
                cur.execute(
                    f"""
                    UPDATE {TBL_USUARIOS}
                    SET rol = %s, activo = 1, fecha_actualizacion = NOW()
                    WHERE username = %s
                    """,
                    (rol, username),
                )
                created = False
            else:
                cur.execute(
                    f"""
                    INSERT INTO {TBL_USUARIOS} (username, nombre, email, rol, activo)
                    VALUES (%s, NULL, NULL, %s, 1)
                    """,
                    (username, rol),
                )
                created = True
            _log_user_event(
                cur,
                username=username,
                actor_username=actor_username,
                accion="USER_PREAUTHORIZED",
                estado="OK",
                rol=rol,
                detalle={"created": created},
            )
        conn.commit()

    return {
        "username": username,
        "nombre": "",
        "email": "",
        "rol": rol,
        "activo": True,
        "created": created,
    }


def update_user_rol(username: str, rol: str, actor_username: str = "") -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {TBL_USUARIOS} SET rol = %s, fecha_actualizacion = NOW() WHERE username = %s",
                (rol, username),
            )
            _log_user_event(
                cur,
                username=username,
                actor_username=actor_username,
                accion="ROLE_UPDATED",
                estado="OK",
                rol=rol,
            )
        conn.commit()


def toggle_user_activo(username: str, activo: bool, actor_username: str = "") -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {TBL_USUARIOS} SET activo = %s, fecha_actualizacion = NOW() WHERE username = %s",
                (1 if activo else 0, username),
            )
            _log_user_event(
                cur,
                username=username,
                actor_username=actor_username,
                accion="USER_ACTIVATED" if activo else "USER_DEACTIVATED",
                estado="OK",
            )
        conn.commit()


def get_or_register_user(
    username: str,
    nombre: str,
    email: str,
    rol_inicial: str = "Publicador",
) -> Dict[str, Any]:
    """
    Si el usuario existe en DB, retorna su rol actual y actualiza datos LDAP.
    Si no existe → lo registra con el rol autenticado y retorna sus datos.
    """
    username = str(username or "").strip().lower()
    nombre = str(nombre or "").strip()
    email = str(email or "").strip()
    rol_inicial = rol_inicial if rol_inicial in {"Admin", "Publicador"} else "Publicador"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT nombre, email, rol, activo FROM {TBL_USUARIOS} WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()

        if row:
            current_nombre, current_email, rol, activo = row
            saved_nombre = nombre or current_nombre or username
            saved_email = email or current_email or ""
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {TBL_USUARIOS}
                    SET nombre = %s, email = %s, fecha_actualizacion = NOW()
                    WHERE username = %s
                    """,
                    (saved_nombre, saved_email, username),
                )
                _log_user_event(
                    cur,
                    username=username,
                    accion="LOGIN_OK" if bool(activo) else "LOGIN_DENIED_INACTIVE",
                    estado="OK" if bool(activo) else "DENIED",
                    rol=rol,
                )
            conn.commit()
            return {
                "username": username,
                "nombre": saved_nombre,
                "email": saved_email,
                "rol": rol,
                "activo": bool(activo),
            }

        # Primer login: respeta el rol resuelto por LDAP/admin local.
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {TBL_USUARIOS} (username, nombre, email, rol, activo) "
                f"VALUES (%s, %s, %s, %s, 1)",
                (username, nombre, email, rol_inicial),
            )
            _log_user_event(
                cur,
                username=username,
                accion="USER_CREATED_LOGIN",
                estado="OK",
                rol=rol_inicial,
            )
            _log_user_event(
                cur,
                username=username,
                accion="LOGIN_OK",
                estado="OK",
                rol=rol_inicial,
            )
        conn.commit()
        return {
            "username": username,
            "nombre": nombre or username,
            "email": email,
            "rol": rol_inicial,
            "activo": True,
        }
