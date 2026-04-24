"""
config/catalogs.py

Proveedor de catálogos.

- En DEMO_MODE=true usa `config.mock_catalogs` (datos hardcodeados).
- En DEMO_MODE=false lee proyectos/catálogos desde SingleStore (SS_DATABASE).

La UI consume estas funciones:
  - get_proyectos_list() -> [{"id": "...", "nombre": "..."}]
  - get_catalogs_by_project(project_id) -> lista de catálogos con schema
  - get_catalog_by_id(project_id, catalog_id) -> dict catálogo
"""

from __future__ import annotations

from typing import Any, Dict, List

from config.settings import REAL_CATALOGS

if not REAL_CATALOGS:
    from config.mock_catalogs import (  # type: ignore
        get_proyectos_list,
        get_catalogs_by_project,
        get_catalog_by_id,
    )
else:
    import json

    from config.settings import SS_DATABASE, SS_HOST, SS_PASSWORD, SS_PORT, SS_USER

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
        """
        Retorna lista de proyectos para el selector.

        Espera tablas en SS_DATABASE:
          - `proyectos(id, nombre)` (preferido) o
          - `catalogos_config(project_id, [project_nombre|project_name])` (fallback)
        """
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT project_id AS id, nombre FROM proyectos WHERE activo = 1 ORDER BY nombre")
                rows = _fetchall_dict(cur)
                return [{"id": str(r["id"]), "nombre": str(r["nombre"])} for r in rows]

    def get_catalogs_by_project(project_id: str) -> List[Dict[str, Any]]:
        """
        Retorna catálogos del proyecto desde `catalogos_config`.

        Columnas esperadas:
          - project_id, catalog_id, nombre, tabla_destino, estrategia, destino, schema_json
        """
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      catalog_id,
                      nombre,
                      base_datos,
                      tabla_destino,
                      estrategia,
                      destino,
                      schema_json
                    FROM catalogos_config
                    WHERE project_id = %s AND activo = 1
                    ORDER BY nombre
                    """,
                    (project_id,),
                )
                rows = _fetchall_dict(cur)
                catalogs: List[Dict[str, Any]] = []
                for r in rows:
                    catalogs.append(
                        {
                            "catalog_id":   str(r["catalog_id"]),
                            "nombre":       str(r["nombre"]),
                            "base_datos":   str(r["base_datos"]),
                            "tabla_destino": str(r["tabla_destino"]),
                            "estrategia":   str(r["estrategia"]),
                            "destino":      str(r["destino"]),
                            "schema":       _parse_schema(r.get("schema_json")),
                        }
                    )
                return catalogs

    def get_catalog_by_id(project_id: str, catalog_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      catalog_id,
                      nombre,
                      base_datos,
                      tabla_destino,
                      estrategia,
                      destino,
                      schema_json
                    FROM catalogos_config
                    WHERE project_id = %s AND catalog_id = %s
                    LIMIT 1
                    """,
                    (project_id, catalog_id),
                )
                rows = _fetchall_dict(cur)
                if not rows:
                    return {}
                r = rows[0]
                return {
                    "catalog_id":    str(r["catalog_id"]),
                    "nombre":        str(r["nombre"]),
                    "base_datos":    str(r["base_datos"]),
                    "tabla_destino": str(r["tabla_destino"]),
                    "estrategia":    str(r["estrategia"]),
                    "destino":       str(r["destino"]),
                    "schema":        _parse_schema(r.get("schema_json")),
                }

