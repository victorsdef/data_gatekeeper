"""
config/mock_catalogs.py
Fallback vacío para cuando REAL_CATALOGS=false y no hay BD disponible.
Los catálogos reales vienen de la tabla catalogos_config en SingleStore.
"""
from typing import Dict, List, Any

PROYECTOS: Dict[str, Any] = {}


def get_proyectos_list() -> List[Dict[str, str]]:
    return []


def get_catalogs_by_project(project_id: str) -> List[Dict[str, Any]]:
    return []


def get_catalog_by_id(project_id: str, catalog_id: str) -> Dict[str, Any]:
    return {}
