"""
utils/db_admin.py
Descubrimiento de esquemas y gestión de catalogos_config para el panel de administración.
"""
from __future__ import annotations
import json
import re
from typing import Any, Dict, List
import streamlit as st

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
            cur.execute("SELECT base_datos, tabla_destino FROM catalogos_config WHERE activo = 1")
            return {(r[0], r[1]) for r in cur.fetchall()}


def ensure_project_exists(project_id: str, nombre: str) -> None:
    """Crea el proyecto si no existe (usa la BD como proyecto)."""
    from config.settings import SS_DATABASE
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT IGNORE INTO `{SS_DATABASE}`.proyectos (project_id, nombre, activo) VALUES (%s, %s, 1)",
                (project_id, nombre),
            )
        conn.commit()


def get_all_projects() -> List[Dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT project_id, nombre FROM proyectos WHERE activo = 1 ORDER BY nombre")
            return [{"id": r[0], "nombre": r[1]} for r in cur.fetchall()]


def get_active_catalogs() -> List[Dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    c.catalog_id,
                    c.nombre,
                    c.base_datos,
                    c.tabla_destino,
                    c.estrategia,
                    c.destino,
                    COALESCE(p.nombre, c.project_id) AS proyecto
                FROM catalogos_config c
                LEFT JOIN proyectos p ON p.project_id = c.project_id
                WHERE c.activo = 1
                ORDER BY proyecto, c.nombre
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def catalog_exists(catalog_id: str) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM catalogos_config WHERE catalog_id = %s LIMIT 1",
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
        INSERT IGNORE INTO `{SS_DATABASE}`.catalogos_config
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
                "UPDATE catalogos_config SET activo = 0 WHERE catalog_id = %s",
                (catalog_id,),
            )
        conn.commit()
