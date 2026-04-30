"""
utils/db_admin.py
Descubrimiento de esquemas y gestión de catalogos_config para el panel de administración.
"""
from __future__ import annotations
import json
import re
from typing import Any, Dict, List
import streamlit as st
from config.settings import (
    TBL_PROYECTOS, TBL_CATALOGOS, TBL_USUARIOS, TBL_PERMISOS, TBL_LOG_AUDITORIA,
)

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


def _connect():
    from config.settings import SS_HOST, SS_PORT, SS_USER, SS_PASSWORD, SS_DATABASE
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
    return sorted(db for db in rows if db.lower() not in _SYSTEM_DBS)


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
    from config.settings import SS_DATABASE
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


def get_active_catalogs() -> List[Dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    c.catalog_id,
                    c.nombre,
                    c.descripcion,
                    c.base_datos,
                    c.tabla_destino,
                    c.estrategia,
                    c.destino,
                    COALESCE(p.nombre, c.project_id) AS proyecto
                FROM {TBL_CATALOGOS} c
                LEFT JOIN {TBL_PROYECTOS} p ON p.project_id = c.project_id
                WHERE c.activo = 1
                ORDER BY proyecto, c.nombre
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


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

    from config.settings import SS_DATABASE
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


# ------------------------------------------------------------------
# Hive
# ------------------------------------------------------------------
_HIVE_SYSTEM_DBS = {"information_schema", "sys", "default"}


def _connect_hive():
    try:
        from pyhive import hive as pyhive_conn
    except ImportError:
        raise ImportError("Instala pyhive: pip install pyhive thrift thrift-sasl")
    from config.settings import HIVE_HOST, HIVE_PORT, HIVE_USER
    return pyhive_conn.Connection(host=HIVE_HOST, port=HIVE_PORT, username=HIVE_USER)


@st.cache_data(ttl=300, show_spinner=False)
def get_hive_databases() -> List[str]:
    with _connect_hive() as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            rows = [r[0] for r in cur.fetchall()]
    return sorted(db for db in rows if db.lower() not in _HIVE_SYSTEM_DBS)


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
            return [{"Field": r[0], "Type": r[1], "Null": "YES"} for r in cur.fetchall()]
