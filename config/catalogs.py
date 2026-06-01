"""
config/catalogs.py

Proveedor de catálogos.

- Lee proyectos/catálogos desde SingleStore (SS_DATABASE).

La UI consume estas funciones:
  - get_proyectos_list() -> [{"id": "...", "nombre": "..."}]
  - get_catalogs_by_project(project_id) -> lista de catálogos con schema
  - get_catalog_by_id(project_id, catalog_id) -> dict catálogo
"""

from __future__ import annotations

from typing import Any, Dict, List

import json
import os

from config import settings


def _setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, os.getenv(name, default))


SS_HOST = _setting("SS_HOST")
SS_PORT = int(_setting("SS_PORT", 3306))
SS_DATABASE = _setting("SS_DATABASE")
SS_USER = _setting("SS_USER")
SS_PASSWORD = _setting("SS_PASSWORD")
TBL_PROYECTOS = _setting("TBL_PROYECTOS", "proyectos")
TBL_CATALOGOS = _setting("TBL_CATALOGOS", "catalogos_config")
TBL_PERMISOS = _setting("TBL_PERMISOS", "permisos_catalogo")
HIVE_ENABLED = str(_setting("HIVE_ENABLED", "true")).lower() == "true"


def _connect():
    import singlestoredb as s2

    return s2.connect(
        host=SS_HOST,
        port=SS_PORT,
        user=SS_USER,
        password=SS_PASSWORD,
        database=SS_DATABASE,
    )


def _fetchall_dict(cursor) -> List[Dict[str, Any]]:
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _parse_schema(schema_value: Any) -> Dict[str, Any]:
    if schema_value is None:
        return {}
    if isinstance(schema_value, dict):
        return schema_value
    if isinstance(schema_value, (bytes, bytearray)):
        schema_value = schema_value.decode("utf-8", errors="ignore")
    if isinstance(schema_value, str):
        schema_value = schema_value.strip()
        if not schema_value:
            return {}
        return json.loads(schema_value)
    return {}


def get_proyectos_list() -> List[Dict[str, str]]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT project_id AS id, nombre FROM {TBL_PROYECTOS} WHERE activo = 1 ORDER BY nombre")
            rows = _fetchall_dict(cur)
            return [{"id": str(r["id"]), "nombre": str(r["nombre"])} for r in rows]


def get_catalogs_by_project(
    project_id: str, username: str = "", rol: str = ""
) -> List[Dict[str, Any]]:
    """
    Retorna catálogos del proyecto filtrados por permisos del usuario.
    - Admins ven todos los catálogos del proyecto.
    - Sin permisos configurados: accesible para todos.
    - Con permisos: solo si el rol o username del usuario coincide.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT
                  c.catalog_id,
                  c.nombre,
                  c.base_datos,
                  c.tabla_destino,
                  c.estrategia,
                  c.destino,
                  c.schema_json
                FROM {TBL_CATALOGOS} c
                LEFT JOIN {TBL_PERMISOS} p ON p.catalog_id = c.catalog_id
                WHERE c.project_id = %s AND c.activo = 1
                  AND (
                    %s = 'Admin'
                    OR p.catalog_id IS NULL
                    OR (p.tipo = 'rol'     AND p.valor = %s)
                    OR (p.tipo = 'usuario' AND p.valor = %s)
                  )
                ORDER BY c.nombre
                """,
                (project_id, rol, rol, username),
            )
            rows = _fetchall_dict(cur)
            if not HIVE_ENABLED:
                rows = [r for r in rows if str(r.get("destino", "")).lower() != "hive"]
            return [
                {
                    "catalog_id":    str(r["catalog_id"]),
                    "nombre":        str(r["nombre"]),
                    "base_datos":    str(r["base_datos"]),
                    "tabla_destino": str(r["tabla_destino"]),
                    "estrategia":    str(r["estrategia"]),
                    "destino":       str(r["destino"]),
                    "schema":        _parse_schema(r.get("schema_json")),
                }
                for r in rows
            ]


def get_catalog_by_id(project_id: str, catalog_id: str) -> Dict[str, Any]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                  catalog_id,
                  nombre,
                  base_datos,
                  tabla_destino,
                  estrategia,
                  destino,
                  schema_json
                FROM {TBL_CATALOGOS}
                WHERE project_id = %s AND catalog_id = %s
                LIMIT 1
                """,
                (project_id, catalog_id),
            )
            rows = _fetchall_dict(cur)
            if not rows:
                return {}
            r = rows[0]
            if not HIVE_ENABLED and str(r.get("destino", "")).lower() == "hive":
                return {}
            return {
                "catalog_id":    str(r["catalog_id"]),
                "nombre":        str(r["nombre"]),
                "base_datos":    str(r["base_datos"]),
                "tabla_destino": str(r["tabla_destino"]),
                "estrategia":    str(r["estrategia"]),
                "destino":       str(r["destino"]),
                "schema":        _parse_schema(r.get("schema_json")),
            }
