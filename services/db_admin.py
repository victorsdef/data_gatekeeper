"""
services/db_admin.py
Descubrimiento de esquemas y gestión de catalogos_config para el panel de administración.
"""
from __future__ import annotations
import json
import os
import re
from typing import Any, Dict, List
import streamlit as st
from config import settings


def _setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, os.getenv(name, default))


SS_HOST = _setting("SS_HOST")
SS_PORT = int(_setting("SS_PORT", 3306))
SS_USER = _setting("SS_USER")
SS_PASSWORD = _setting("SS_PASSWORD")
SS_DATABASE = _setting("SS_DATABASE")
HIVE_HOST = _setting("HIVE_HOST")
HIVE_PORT = int(_setting("HIVE_PORT", 10000))
HIVE_USER = _setting("HIVE_USER", "hive")
HIVE_ENABLED = str(_setting("HIVE_ENABLED", "true")).lower() == "true"
DB_NAME_FILTERS = _setting("DB_NAME_FILTERS", "")

TBL_PROYECTOS = _setting("TBL_PROYECTOS", "proyectos")
TBL_CATALOGOS = _setting("TBL_CATALOGOS", "catalogos_config")
TBL_USUARIOS = _setting("TBL_USUARIOS", "usuarios")
TBL_PERMISOS = _setting("TBL_PERMISOS", "permisos_catalogo")
TBL_LOG_AUDITORIA = _setting("TBL_LOG_AUDITORIA", "log_auditoria")

_SYSTEM_DBS = {
    "information_schema", "memsql", "cluster", "mysql",
    "performance_schema", "gatekeeper_meta",
}

_TYPE_MAP = {
    "tinyint": "int",  "smallint": "int", "mediumint": "int",
    "int": "int",      "bigint": "int",   "year": "int",
    "float": "float",  "double": "float", "decimal": "float",
    "numeric": "float","real": "float",
    "varchar": "str",  "char": "str",     "text": "str",
    "mediumtext": "str","longtext": "str", "tinytext": "str",
    "json": "str",     "blob": "str",
    "date": "str",     "datetime": "str", "timestamp": "str", "time": "str",
    "bit": "bool",     "bool": "bool",    "boolean": "bool",
}


def _db_name_filters() -> List[str]:
    return [
        item.strip().lower()
        for item in str(DB_NAME_FILTERS or "").split(",")
        if item.strip()
    ]


def _is_allowed_database(database: str, system_databases: set[str]) -> bool:
    db_name = database.lower()
    if db_name in system_databases:
        return False

    filters = _db_name_filters()
    if not filters:
        return True

    return any(pattern in db_name for pattern in filters)


def _connect():
    import singlestoredb as s2
    return s2.connect(
        host=SS_HOST, port=SS_PORT,
        user=SS_USER, password=SS_PASSWORD,
        database=SS_DATABASE,
    )


def _base_type(ss_type: str) -> str:
    base = re.split(r"[\s(]", ss_type.lower().strip())[0]
    return _TYPE_MAP.get(base, "str")


@st.cache_data(ttl=300, show_spinner=False)
def get_all_databases() -> List[str]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            rows = [r[0] for r in cur.fetchall()]
    return sorted(db for db in rows if _is_allowed_database(db, _SYSTEM_DBS))


@st.cache_data(ttl=300, show_spinner=False)
def get_tables_from_db(database: str) -> List[str]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SHOW TABLES FROM `{database}`")
            return sorted(r[0] for r in cur.fetchall())


def describe_table(database: str, table: str) -> List[Dict[str, str]]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DESCRIBE `{database}`.`{table}`")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def build_schema_json(describe_rows: List[Dict]) -> Dict:
    columnas = []
    for r in describe_rows:
        columnas.append({
            "nombre":   r["Field"],
            "tipo":     _base_type(str(r.get("Type", "varchar"))),
            "nullable": str(r.get("Null", "YES")).upper() == "YES",
            "reglas":   [],
        })
    return {"columnas": columnas}


def get_mapped_tables() -> set:
    """Retorna un set de (base_datos, tabla_destino) ya registrados en catalogos_config."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT base_datos, tabla_destino FROM {TBL_CATALOGOS} WHERE activo = 1")
            return {(r[0], r[1]) for r in cur.fetchall()}


def ensure_project_exists(project_id: str, nombre: str) -> None:
    """Crea el proyecto si no existe (usa la BD como proyecto)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT IGNORE INTO `{SS_DATABASE}`.{TBL_PROYECTOS} (project_id, nombre, activo) VALUES (%s, %s, 1)",
                (project_id, nombre),
            )
        conn.commit()


def get_all_projects() -> List[Dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT project_id, nombre FROM {TBL_PROYECTOS} WHERE activo = 1 ORDER BY nombre")
            return [{"id": r[0], "nombre": r[1]} for r in cur.fetchall()]


def get_active_catalogs(include_inactive: bool = False) -> List[Dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            where_clause = "" if include_inactive else "WHERE c.activo = 1"
            cur.execute(f"""
                SELECT
                    c.catalog_id,
                    c.project_id,
                    c.nombre,
                    c.descripcion,
                    c.base_datos,
                    c.tabla_destino,
                    c.estrategia,
                    c.destino,
                    c.activo,
                    COALESCE(p.activo, 1) AS proyecto_activo,
                    COALESCE(p.nombre, c.project_id) AS proyecto
                FROM {TBL_CATALOGOS} c
                LEFT JOIN {TBL_PROYECTOS} p ON p.project_id = c.project_id
                {where_clause}
                ORDER BY proyecto, c.nombre
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            if not HIVE_ENABLED:
                rows = [r for r in rows if str(r.get("destino", "")).lower() != "hive"]
            return rows


def get_catalog_schema(catalog_id: str) -> Dict:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT schema_json FROM {TBL_CATALOGOS} WHERE catalog_id = %s", (catalog_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return {"columnas": []}
            data = row[0]
            if isinstance(data, (dict, list)):
                return data if isinstance(data, dict) else {"columnas": data}
            return json.loads(data)


def update_catalog_config(
    catalog_id: str,
    nombre: str,
    descripcion: str,
    estrategia: str,
    destino: str,
    schema_json: Dict | None = None,
) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            if schema_json is not None:
                cur.execute(
                    f"UPDATE {TBL_CATALOGOS} SET nombre=%s, descripcion=%s, estrategia=%s, destino=%s, schema_json=%s WHERE catalog_id=%s",
                    (nombre, descripcion, estrategia, destino, json.dumps(schema_json, ensure_ascii=False), catalog_id),
                )
            else:
                cur.execute(
                    f"UPDATE {TBL_CATALOGOS} SET nombre=%s, descripcion=%s, estrategia=%s, destino=%s WHERE catalog_id=%s",
                    (nombre, descripcion, estrategia, destino, catalog_id),
                )
        conn.commit()


def catalog_exists(catalog_id: str) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {TBL_CATALOGOS} WHERE catalog_id = %s LIMIT 1",
                (catalog_id,),
            )
            return cur.fetchone() is not None


def get_catalog_id_by_table(base_datos: str, tabla_destino: str) -> str | None:
    """Retorna el catalog_id activo asociado a una tabla física."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT catalog_id
                FROM {TBL_CATALOGOS}
                WHERE base_datos = %s AND tabla_destino = %s AND activo = 1
                ORDER BY catalog_id
                LIMIT 1
                """,
                (base_datos, tabla_destino),
            )
            row = cur.fetchone()
            return row[0] if row else None


def save_catalog_config(
    catalog_id: str,
    project_id: str,
    nombre: str,
    descripcion: str,
    base_datos: str,
    tabla_destino: str,
    destino: str,
    estrategia: str,
    schema_json: Dict,
) -> bool:
    """
    Registra el catálogo solo si no existe (INSERT IGNORE).
    Retorna True si se insertó, False si ya existía.
    """
    if catalog_exists(catalog_id):
        return False

    sql = f"""
        INSERT IGNORE INTO `{SS_DATABASE}`.{TBL_CATALOGOS}
            (catalog_id, project_id, nombre, descripcion, base_datos,
             tabla_destino, destino, estrategia, schema_json, activo)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                catalog_id, project_id, nombre, descripcion,
                base_datos, tabla_destino, destino, estrategia,
                json.dumps(schema_json, ensure_ascii=False),
            ))
        conn.commit()
    return True


def deactivate_catalog(catalog_id: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {TBL_CATALOGOS} SET activo = 0 WHERE catalog_id = %s",
                (catalog_id,),
            )
        conn.commit()


def activate_catalog(catalog_id: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {TBL_CATALOGOS} SET activo = 1 WHERE catalog_id = %s",
                (catalog_id,),
            )
        conn.commit()


def deactivate_project(project_id: str) -> None:
    """Desactiva un proyecto y todos sus catalogos asociados."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {TBL_PROYECTOS} SET activo = 0 WHERE project_id = %s",
                (project_id,),
            )
            cur.execute(
                f"UPDATE {TBL_CATALOGOS} SET activo = 0 WHERE project_id = %s",
                (project_id,),
            )
        conn.commit()


def activate_project(project_id: str) -> None:
    """Activa un proyecto y todos sus catalogos asociados."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {TBL_PROYECTOS} SET activo = 1 WHERE project_id = %s",
                (project_id,),
            )
            cur.execute(
                f"UPDATE {TBL_CATALOGOS} SET activo = 1 WHERE project_id = %s",
                (project_id,),
            )
        conn.commit()


def get_catalog_permissions(catalog_id: str) -> List[Dict]:
    """Retorna los permisos configurados para un catálogo."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT tipo, valor FROM {TBL_PERMISOS} WHERE catalog_id = %s ORDER BY tipo, valor",
                (catalog_id,),
            )
            return [{"tipo": r[0], "valor": r[1]} for r in cur.fetchall()]


def save_permissions(catalog_id: str, permisos: List[Dict]) -> None:
    """Reemplaza todos los permisos de un catálogo (DELETE + INSERT)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {TBL_PERMISOS} WHERE catalog_id = %s", (catalog_id,))
            for p in permisos:
                cur.execute(
                    f"INSERT IGNORE INTO {TBL_PERMISOS} (catalog_id, tipo, valor) VALUES (%s, %s, %s)",
                    (catalog_id, p["tipo"], p["valor"]),
                )
        conn.commit()


def get_all_usuarios_activos() -> List[str]:
    """Retorna usernames activos de la tabla usuarios."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT username FROM {TBL_USUARIOS} WHERE activo = 1 ORDER BY username")
            return [r[0] for r in cur.fetchall()]


def export_catalogs_bundle() -> Dict[str, Any]:
    """Exporta proyectos, catálogos y permisos activos a un bundle JSON."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT project_id, nombre
                FROM {TBL_PROYECTOS}
                WHERE activo = 1
                ORDER BY nombre
                """
            )
            proyectos = [{"project_id": r[0], "nombre": r[1]} for r in cur.fetchall()]

            cur.execute(
                f"""
                SELECT
                    catalog_id, project_id, nombre, descripcion,
                    base_datos, tabla_destino, destino, estrategia,
                    schema_json, activo
                FROM {TBL_CATALOGOS}
                WHERE activo = 1
                ORDER BY project_id, nombre
                """
            )
            catalogs = []
            for row in cur.fetchall():
                schema_value = row[8]
                if isinstance(schema_value, str):
                    schema_json = json.loads(schema_value)
                elif isinstance(schema_value, (bytes, bytearray)):
                    schema_json = json.loads(schema_value.decode("utf-8", errors="ignore"))
                else:
                    schema_json = schema_value or {"columnas": []}
                catalogs.append(
                    {
                        "catalog_id": row[0],
                        "project_id": row[1],
                        "nombre": row[2],
                        "descripcion": row[3],
                        "base_datos": row[4],
                        "tabla_destino": row[5],
                        "destino": row[6],
                        "estrategia": row[7],
                        "schema_json": schema_json,
                        "activo": bool(row[9]),
                    }
                )

            cur.execute(
                f"""
                SELECT catalog_id, tipo, valor
                FROM {TBL_PERMISOS}
                ORDER BY catalog_id, tipo, valor
                """
            )
            permissions = [{"catalog_id": r[0], "tipo": r[1], "valor": r[2]} for r in cur.fetchall()]

    return {
        "version": 1,
        "proyectos": proyectos,
        "catalogos": catalogs,
        "permisos": permissions,
    }


def import_catalogs_bundle(bundle: Dict[str, Any], overwrite_existing: bool = False) -> Dict[str, int]:
    """
    Importa un bundle exportado previamente.
    Si overwrite_existing=True, actualiza config/permisos de catálogos existentes.
    """
    if not isinstance(bundle, dict):
        raise ValueError("El backup de catálogos debe ser un objeto JSON válido.")

    proyectos = bundle.get("proyectos", []) or []
    catalogos = bundle.get("catalogos", []) or []
    permisos = bundle.get("permisos", []) or []
    if not isinstance(proyectos, list) or not isinstance(catalogos, list) or not isinstance(permisos, list):
        raise ValueError("El backup de catálogos debe contener listas válidas de proyectos, catálogos y permisos.")

    permisos_by_catalog: Dict[str, List[Dict[str, str]]] = {}
    for perm in permisos:
        if not isinstance(perm, dict):
            raise ValueError("Cada permiso del backup debe ser un objeto.")
        if not str(perm.get("catalog_id", "")).strip() or not str(perm.get("tipo", "")).strip() or not str(perm.get("valor", "")).strip():
            raise ValueError("Cada permiso del backup debe incluir catalog_id, tipo y valor.")
        permisos_by_catalog.setdefault(str(perm["catalog_id"]), []).append(
            {"tipo": str(perm["tipo"]), "valor": str(perm["valor"])}
        )

    created = 0
    updated = 0
    skipped = 0

    for proyecto in proyectos:
        if not isinstance(proyecto, dict):
            raise ValueError("Cada proyecto del backup debe ser un objeto.")
        if not str(proyecto.get("project_id", "")).strip():
            raise ValueError("Cada proyecto del backup debe tener project_id.")
        if not str(proyecto.get("nombre", "")).strip():
            raise ValueError("Cada proyecto del backup debe tener nombre.")
        ensure_project_exists(str(proyecto["project_id"]), str(proyecto["nombre"]))

    with _connect() as conn:
        with conn.cursor() as cur:
            for cat in catalogos:
                if not isinstance(cat, dict):
                    raise ValueError("Cada catálogo del backup debe ser un objeto.")
                catalog_id = str(cat.get("catalog_id", "")).strip()
                required_fields = ["catalog_id", "project_id", "nombre", "base_datos", "tabla_destino", "destino", "estrategia"]
                missing = [field for field in required_fields if not str(cat.get(field, "")).strip()]
                if missing:
                    raise ValueError(
                        f"El catálogo `{catalog_id or 'sin_id'}` no tiene estos campos obligatorios: {', '.join(missing)}."
                    )
                schema_json = cat.get("schema_json") or {"columnas": []}
                if not isinstance(schema_json, dict) or not (schema_json.get("columnas") or []):
                    raise ValueError(f"El catálogo `{catalog_id}` debe incluir un schema_json con columnas.")
                if catalog_exists(catalog_id):
                    if overwrite_existing:
                        cur.execute(
                            f"""
                            UPDATE {TBL_CATALOGOS}
                            SET project_id=%s, nombre=%s, descripcion=%s, base_datos=%s,
                                tabla_destino=%s, destino=%s, estrategia=%s, schema_json=%s, activo=1
                            WHERE catalog_id=%s
                            """,
                            (
                                str(cat["project_id"]),
                                str(cat["nombre"]),
                                str(cat.get("descripcion") or ""),
                                str(cat["base_datos"]),
                                str(cat["tabla_destino"]),
                                str(cat["destino"]),
                                str(cat["estrategia"]),
                                json.dumps(schema_json, ensure_ascii=False),
                                catalog_id,
                            ),
                        )
                        updated += 1
                    else:
                        skipped += 1
                        continue
                else:
                    cur.execute(
                        f"""
                        INSERT INTO {TBL_CATALOGOS}
                            (catalog_id, project_id, nombre, descripcion, base_datos,
                             tabla_destino, destino, estrategia, schema_json, activo)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                        """,
                        (
                            catalog_id,
                            str(cat["project_id"]),
                            str(cat["nombre"]),
                            str(cat.get("descripcion") or ""),
                            str(cat["base_datos"]),
                            str(cat["tabla_destino"]),
                            str(cat["destino"]),
                            str(cat["estrategia"]),
                            json.dumps(schema_json, ensure_ascii=False),
                        ),
                    )
                    created += 1

                cur.execute(f"DELETE FROM {TBL_PERMISOS} WHERE catalog_id = %s", (catalog_id,))
                for perm in permisos_by_catalog.get(catalog_id, []):
                    cur.execute(
                        f"INSERT IGNORE INTO {TBL_PERMISOS} (catalog_id, tipo, valor) VALUES (%s, %s, %s)",
                        (catalog_id, perm["tipo"], perm["valor"]),
                    )
        conn.commit()

    return {"created": created, "updated": updated, "skipped": skipped}


# ------------------------------------------------------------------
# Hive
# ------------------------------------------------------------------
_HIVE_SYSTEM_DBS = {"information_schema", "sys", "default"}


def _connect_hive():
    if not HIVE_ENABLED:
        raise ValueError("Hive está deshabilitado en la configuración.")
    try:
        from pyhive import hive as pyhive_conn
    except ImportError:
        raise ImportError("Instala pyhive: pip install pyhive thrift thrift-sasl")
    return pyhive_conn.Connection(host=HIVE_HOST, port=HIVE_PORT, username=HIVE_USER)


@st.cache_data(ttl=300, show_spinner=False)
def get_hive_databases() -> List[str]:
    with _connect_hive() as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            rows = [r[0] for r in cur.fetchall()]
    return sorted(db for db in rows if _is_allowed_database(db, _HIVE_SYSTEM_DBS))


@st.cache_data(ttl=300, show_spinner=False)
def get_hive_tables(database: str) -> List[str]:
    with _connect_hive() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SHOW TABLES IN {database}")
            return sorted(r[0] for r in cur.fetchall())


def describe_hive_table(database: str, table: str) -> List[Dict[str, str]]:
    with _connect_hive() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DESCRIBE {database}.{table}")
            raw = cur.fetchall()

    seen: set = set()
    result = []
    for r in raw:
        col_name = (r[0] or "").strip()
        # Fila vacía o header de partición (#) → fin de columnas reales
        if not col_name or col_name.startswith("#"):
            break
        if col_name not in seen:
            seen.add(col_name)
            result.append({"Field": col_name, "Type": r[1] or "string", "Null": "YES"})
    return result
