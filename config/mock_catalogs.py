"""
config/mock_catalogs.py
Datos de proyectos y catálogos para modo demo.
En producción esto viene de la tabla catalogos_config en SingleStore.
"""
from typing import Dict, List, Any

# ------------------------------------------------------------------
# Estructura del catálogo:
#   project_id  -> nombre del proyecto
#   catalogs    -> lista de catálogos del proyecto
#     catalog_id     -> id único
#     nombre         -> nombre legible
#     tabla_destino  -> tabla en SingleStore/Hive
#     estrategia     -> "append" | "overwrite" | "reproceso"
#     destino        -> "singlestore" | "hive"
#     schema         -> definición de columnas para pandera
# ------------------------------------------------------------------

PROYECTOS: Dict[str, Any] = {
    "COMERCIAL_BDA": {
        "nombre": "Comercial",
        "catalogs": [
            {
                "catalog_id":    "CAT_CATALOGO_PRODUCTOS",
                "nombre":        "Catálogo de Productos",
                "tabla_destino": "tbsc_catalogo_productos",
                "estrategia":    "overwrite",
                "destino":       "singlestore",
                "schema": {
                    "columnas": [
                        # TODO: primera columna sin nombre en el header del CSV.
                        # Confirmar con DBA el nombre real en la tabla y reemplazar "" por ese nombre.
                        {"nombre": "",                   "tipo": "str",  "nullable": False, "reglas": []},
                        {"nombre": "subcategoria",       "tipo": "str",  "nullable": False, "reglas": [
                            {"tipo": "isin", "valor": ["CAPTACIONES", "COLOCACIONES"]}
                        ]},
                        # csubsistema, cgrupoproducto y cproducto son str (tienen ceros a la izquierda: "001", "05")
                        {"nombre": "csubsistema",        "tipo": "str",  "nullable": False, "reglas": []},
                        {"nombre": "subsistema",         "tipo": "str",  "nullable": False, "reglas": []},
                        {"nombre": "cgrupoproducto",     "tipo": "str",  "nullable": False, "reglas": []},
                        {"nombre": "grupoproducto",      "tipo": "str",  "nullable": False, "reglas": []},
                        {"nombre": "cproducto",          "tipo": "str",  "nullable": False, "reglas": []},
                        {"nombre": "producto",           "tipo": "str",  "nullable": False, "reglas": []},
                        {"nombre": "desc_grupo_tablero", "tipo": "str",  "nullable": False, "reglas": [
                            {"tipo": "isin", "valor": ["CDPS", "CONSUMO", "MONETARIOS", "AHORROS",
                                                       "TARJETAS", "INMOBILIARIO", "MICRO", "DIGITAL"]}
                        ]},
                        # crol es el único campo realmente numérico; valores válidos: 1, 2, 3, 5
                        {"nombre": "crol",               "tipo": "int",  "nullable": False, "reglas": [
                            {"tipo": "isin", "valor": [1, 2, 3, 5]}
                        ]},
                        {"nombre": "rol",                "tipo": "str",  "nullable": False, "reglas": [
                            {"tipo": "isin", "valor": ["MASIVO", "MASIVO AFLUENTE", "AFLUENTE", "JEFE DE AGENCIA"]}
                        ]},
                        # fecha_proceso en formato YYYYMMDD (8 caracteres exactos)
                        {"nombre": "fecha_proceso",      "tipo": "str",  "nullable": False, "reglas": [
                            {"tipo": "str_length", "min": 8, "max": 8}
                        ]},
                    ]
                },
            }
        ]
    },
    "CREDITOS": {
        "nombre": "Créditos",
        "catalogs": [
            {
                "catalog_id":    "CAT_ROLES_CREDITO",
                "nombre":        "Roles de crédito",
                "tabla_destino": "cat_roles_credito",
                "estrategia":    "overwrite",
                "destino":       "singlestore",
                "schema": {
                    "columnas": [
                        {"nombre": "cod_rol",  "tipo": "int",    "nullable": False, "reglas": []},
                        {"nombre": "rol",      "tipo": "str",    "nullable": False, "reglas": [{"tipo": "min_length", "valor": 2}]},
                        {"nombre": "estado",   "tipo": "int",    "nullable": False, "reglas": [{"tipo": "isin", "valor": [0, 1]}]},
                        {"nombre": "peso_1",   "tipo": "int",    "nullable": False, "reglas": [{"tipo": "gte", "valor": 0}]},
                    ]
                },
            },
            {
                "catalog_id":    "CAT_TIPO_CREDITO",
                "nombre":        "Tipos de crédito",
                "tabla_destino": "cat_tipo_credito",
                "estrategia":    "append",
                "destino":       "singlestore",
                "schema": {
                    "columnas": [
                        {"nombre": "cod_tipo",      "tipo": "str",  "nullable": False, "reglas": []},
                        {"nombre": "descripcion",   "tipo": "str",  "nullable": False, "reglas": [{"tipo": "min_length", "valor": 3}]},
                        {"nombre": "tasa_max",      "tipo": "float","nullable": False, "reglas": [{"tipo": "gte", "valor": 0}]},
                        {"nombre": "activo",        "tipo": "int",  "nullable": False, "reglas": [{"tipo": "isin", "valor": [0, 1]}]},
                    ]
                },
            },
        ]
    },
    "TARJETAS": {
        "nombre": "Tarjetas de Débito",
        "catalogs": [
            {
                "catalog_id":    "CAT_SEGMENTOS_TD",
                "nombre":        "Segmentos de tarjeta",
                "tabla_destino": "cat_segmentos_td",
                "estrategia":    "overwrite",
                "destino":       "hive",
                "schema": {
                    "columnas": [
                        {"nombre": "cod_segmento",  "tipo": "str",  "nullable": False, "reglas": []},
                        {"nombre": "nombre",        "tipo": "str",  "nullable": False, "reglas": [{"tipo": "min_length", "valor": 2}]},
                        {"nombre": "limite_diario", "tipo": "float","nullable": False, "reglas": [{"tipo": "gte", "valor": 0}]},
                        {"nombre": "estado",        "tipo": "int",  "nullable": False, "reglas": [{"tipo": "isin", "valor": [0, 1]}]},
                    ]
                },
            },
        ]
    },
    "CUMPLIMIENTO": {
        "nombre": "Cumplimiento",
        "catalogs": [
            {
                "catalog_id":    "CAT_LISTAS_CONTROL",
                "nombre":        "Listas de control",
                "tabla_destino": "cat_listas_control",
                "estrategia":    "reproceso",
                "destino":       "singlestore",
                "schema": {
                    "columnas": [
                        {"nombre": "cedula",        "tipo": "str",  "nullable": False, "reglas": [{"tipo": "str_length", "min": 10, "max": 13}]},
                        {"nombre": "nombres",       "tipo": "str",  "nullable": False, "reglas": [{"tipo": "min_length", "valor": 3}]},
                        {"nombre": "tipo_lista",    "tipo": "str",  "nullable": False, "reglas": [{"tipo": "isin", "valor": ["NEGRA", "GRIS", "BLANCA"]}]},
                        {"nombre": "fecha_proceso", "tipo": "str",  "nullable": False, "reglas": []},
                    ]
                },
            },
        ]
    },
}


def get_proyectos_list() -> List[Dict[str, str]]:
    """Retorna lista de proyectos para el selector."""
    return [
        {"id": pid, "nombre": data["nombre"]}
        for pid, data in PROYECTOS.items()
    ]


def get_catalogs_by_project(project_id: str) -> List[Dict[str, Any]]:
    """Retorna catálogos de un proyecto."""
    proyecto = PROYECTOS.get(project_id)
    if not proyecto:
        return []
    return proyecto["catalogs"]


def get_catalog_by_id(project_id: str, catalog_id: str) -> Dict[str, Any]:
    """Retorna la config completa de un catálogo."""
    catalogs = get_catalogs_by_project(project_id)
    for cat in catalogs:
        if cat["catalog_id"] == catalog_id:
            return cat
    return {}
