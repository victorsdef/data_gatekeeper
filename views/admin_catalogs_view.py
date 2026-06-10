"""
views/admin_catalogs_view.py
Panel de administración: explorar BDs, registrar catálogos y gestionar permisos.

Flujo:
  1. Admin elige BD → ve tablas con checkboxes
  2. Selecciona 1 tabla → ve esquema editable + formulario de registro
     Selecciona N tablas → ve config común + botón "Registrar N tablas"
  3. Las tablas ya registradas aparecen marcadas (✓) y solo permiten gestionar permisos
  4. Tab "Catálogos activos" lista todo lo registrado en gatekeeper_meta
"""
from __future__ import annotations
import re
import base64
import json
from html import escape as html_escape
from pathlib import Path
from typing import Dict, List, Set
import pandas as pd
import streamlit as st

from config import settings
from services.db_admin import (
    get_all_databases, get_tables_from_db, get_mapped_tables,
    describe_table, build_schema_json,
    get_hive_databases, get_hive_tables, describe_hive_table,
    get_all_projects, get_active_catalogs,
    save_catalog_config, catalog_exists, deactivate_catalog, ensure_project_exists,
    get_catalog_permissions, save_permissions, get_catalog_id_by_table,
    update_catalog_config,
    get_catalog_schema,
    export_catalogs_bundle, import_catalogs_bundle,
)
from services.db_writer import get_audit_log
from utils.error_messages import user_facing_error
from services.user_service import (
    get_all_usuarios, update_user_rol, toggle_user_activo,
    export_users_bundle, import_users_bundle, create_or_promote_user,
)

_TIPOS       = ["str", "int", "float", "bool"]
_ESTRATEGIAS = [
    "overwrite",
    "append",
    # "reproceso",  # Oculto por ahora: no mostrar como opcion de registro.
]
HIVE_ENABLED = bool(getattr(settings, "HIVE_ENABLED", True))
_DESTINOS    = ["singlestore"] + (["hive"] if HIVE_ENABLED else [])
_ROLES       = ["Publicador"]
_REGLA_TIPOS = ["isin", "gte", "lte", "min_length", "str_length", "regex"]
_REGLAS_POR_TIPO = {
    "str": ["isin", "min_length", "str_length", "regex"],
    "int": ["gte", "lte"],
    "float": ["gte", "lte"],
    "bool": [],
}
_REGLA_LABELS = {
    "isin":       "Dominio (valores permitidos)",
    "gte":        "Valor mínimo (≥)",
    "lte":        "Valor máximo (≤)",
    "min_length": "Longitud mínima de texto",
    "str_length": "Longitud entre mín y máx",
}

_REGLA_LABELS["regex"] = "Patron de texto (regex)"

_INGESTION_MODES = {
    "sin_regla": "No hacer control especial",
    "evitar_duplicados": "Rechazar la carga si ya existe",
    "reemplazar_por_campo": "Reemplazar registros existentes",
}
_INGESTION_MODE_HELP = {
    "sin_regla": "No revisa duplicados ni reemplaza por campo. Usa la estrategia normal del catalogo.",
    "evitar_duplicados": "Si la tabla ya tiene datos con el mismo campo de control, no carga nada para evitar duplicados.",
    "reemplazar_por_campo": "Si la tabla ya tiene datos con el mismo campo de control, elimina solo esos registros y luego carga el archivo.",
}

_ID_PREFIX = "dg_au_agd"
_GENERIC_ORIGIN_TOKENS = {
    "bd", "db", "dbo", "tbl", "tmp",
    "catalogo", "catalogos", "catalog", "catalogs",
    "tabla", "tablas", "table", "tables",
    "manual", "manuales", "man", "data", "datos",
    "cliente", "ideal", "transacciones", "transaccion",
    "decrypt", "encrypt", "columna", "columnas", "campo", "campos",
    "fuente", "fuentes", "general", "maestro", "maestros",
}


def _load_usuarios_lookup() -> Dict[str, Dict]:
    try:
        usuarios = get_all_usuarios()
    except Exception:
        return {}
    return {
        str(u.get("username", "")).strip().lower(): u
        for u in usuarios
        if str(u.get("username", "")).strip()
    }


def _is_publicador_activo(username: str, usuarios_lookup: Dict[str, Dict]) -> bool:
    user = usuarios_lookup.get(str(username or "").strip().lower())
    if not user:
        return False
    rol = str(user.get("rol") or "Publicador").strip().lower()
    return bool(user.get("activo")) and rol == "publicador"


def _usuario_label(username: str, usuarios_lookup: Dict[str, Dict]) -> str:
    username = str(username or "").strip()
    user = usuarios_lookup.get(username.lower(), {})
    nombre = str(user.get("nombre") or "").strip()
    parts = []
    if nombre and nombre.lower() != username.lower():
        parts.append(nombre)
    parts.append(username)
    return " · ".join(parts)


def _format_permiso_label(permiso: Dict, usuarios_lookup: Dict[str, Dict], icon_user: str, icon_role: str) -> str:
    valor = str(permiso.get("valor", "")).strip()
    if permiso.get("tipo") == "usuario":
        return f"{icon_user}{html_escape(_usuario_label(valor, usuarios_lookup))}"
    return f"{icon_role}{html_escape(valor)}"


def _format_permiso_chip(permiso: Dict, usuarios_lookup: Dict[str, Dict], icon_user: str, icon_role: str) -> str:
    label = _format_permiso_label(permiso, usuarios_lookup, icon_user, icon_role)
    bg = "#EEF2FF" if permiso.get("tipo") == "usuario" else "#F3F4F6"
    color = "#1C2F6E" if permiso.get("tipo") == "usuario" else "#4B5563"
    return (
        f'<span style="display:inline-flex;align-items:center;gap:3px;'
        f'background:{bg};color:{color};border:1px solid #D1D9F0;'
        f'padding:2px 7px;border-radius:999px;font-size:11px;line-height:1.4;">'
        f'{label}</span>'
    )


def _normalize_ba_username(username: str) -> str:
    return str(username or "").strip().lower()


def _validate_ba_publicador_username(username: str, usuarios_lookup: Dict[str, Dict]) -> str | None:
    if not username:
        return "Ingresa el usuario BA."
    if not username.startswith("ba"):
        return "El usuario debe iniciar con `ba`, por ejemplo `ba01006646`."
    if not re.fullmatch(r"ba[\w.-]+", username):
        return "El usuario BA contiene caracteres no validos."

    existing = usuarios_lookup.get(username)
    if existing and str(existing.get("rol") or "").strip().lower() == "admin":
        return "Ese usuario ya es Admin. Los Admin ven todos los catalogos sin permisos puntuales."
    return None


def _skeleton_html(n_lines: int = 4, card: bool = False) -> str:
    """Placeholder animado (shimmer) mientras carga contenido de BD."""
    widths = ["long", "medium", "short", "long", "medium"]
    lines = "".join(
        f'<div class="skel-line {widths[i % len(widths)]}"></div>'
        for i in range(n_lines)
    )
    if card:
        single = f'<div class="skel-card">{lines}</div>'
        return single * 3
    return f'<div style="padding:8px 0">{lines}</div>'


def _logo_b64() -> str:
    logo = Path(__file__).parent.parent / "assets" / "logo.png"
    return base64.b64encode(logo.read_bytes()).decode() if logo.exists() else ""


def _sync_selected_tables(all_tables: List[str]) -> List[str]:
    adm_sel_set = st.session_state.setdefault("adm_sel_set", set())

    # Sincroniza solo los checkboxes renderizados (página actual) al set persistente
    for t in all_tables:
        chk_key = f"adm_chk_{t}"
        if chk_key in st.session_state:
            if st.session_state[chk_key]:
                adm_sel_set.add(t)
            else:
                adm_sel_set.discard(t)

    # La selección viene del set persistente, no de los widgets (sobrevive paginación)
    selected = [t for t in all_tables if t in adm_sel_set]

    prev_selected = set(st.session_state.get("adm_selected_tables") or [])
    newly_checked = [t for t in selected if t not in prev_selected]

    st.session_state.adm_selected_tables = selected

    active_table = st.session_state.get("adm_active_table")
    if selected:
        if newly_checked:
            st.session_state.adm_active_table = newly_checked[-1]
        elif active_table not in selected:
            st.session_state.adm_active_table = selected[-1]
    else:
        st.session_state.pop("adm_active_table", None)

    return selected


def _render_active_table_selector(selected: List[str], key_suffix: str = "main") -> str:
    if not selected:
        return ""
    current = st.session_state.get("adm_active_table")
    if current not in selected:
        current = selected[0]
        st.session_state.adm_active_table = current

    selected_idx = selected.index(current)
    label = "Tabla que vas a configurar ahora" if key_suffix == "bulk" else "Tabla activa"
    active = st.selectbox(
        label,
        options=selected,
        index=selected_idx,
        key=f"adm_active_table_selector_{key_suffix}",
    )
    if active != st.session_state.get("adm_active_table"):
        if key_suffix == "bulk":
            _persist_bulk_form_state()
        st.session_state.adm_active_table = active
    return active


def _bulk_picker_key(key_suffix: str) -> str:
    return f"adm_bulk_active_table_{key_suffix}"


def _sync_bulk_active_table_pickers(selected: List[str]) -> str:
    if not selected:
        return ""

    current = st.session_state.get("adm_active_table")
    if current not in selected:
        current = selected[0]

    picker_keys = [_bulk_picker_key("data"), _bulk_picker_key("columns")]
    for key in picker_keys:
        value = st.session_state.get(key)
        if value in selected and value != current:
            current = value
            break

    st.session_state.adm_active_table = current
    for key in picker_keys:
        if st.session_state.get(key) != current:
            st.session_state[key] = current
    return current


def _render_bulk_active_table_selector(selected: List[str], key_suffix: str) -> str:
    current = st.session_state.get("adm_active_table")
    if current not in selected:
        current = selected[0]
        st.session_state.adm_active_table = current
    return st.selectbox(
        "Tabla que vas a configurar ahora",
        options=selected,
        index=selected.index(current),
        key=_bulk_picker_key(key_suffix),
    )


def _slugify_id(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(text).lower()).strip("_")


def _pretty_label(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("_", " ")).strip().title()


def _infer_origin(database: str, table: str | None = None) -> str:
    candidates: List[str] = []
    if table:
        candidates.extend([tok for tok in _slugify_id(table).split("_") if tok])
    candidates.extend([tok for tok in _slugify_id(database).split("_") if tok])

    for token in reversed(candidates):
        if token not in _GENERIC_ORIGIN_TOKENS and len(token) >= 2:
            return token
    return "general"


def _project_defaults(database: str, table: str | None = None) -> tuple[str, str]:
    origin = _infer_origin(database, table)
    project_id = f"{_ID_PREFIX}_{origin}"
    project_name = f"Analitica y Gestion de Dato - {origin.upper()}"
    return project_id, project_name


def _catalog_defaults(database: str, table: str) -> tuple[str, str]:
    project_id, _ = _project_defaults(database, table)
    return f"{project_id}__{_slugify_id(table)}", _pretty_label(table)


def _catalog_id_for_existing_table(database: str, table: str) -> str:
    existing = get_catalog_id_by_table(database, table)
    if existing:
        return existing
    catalog_id, _ = _catalog_defaults(database, table)
    return catalog_id


def _validate_schema_definition(schema: dict) -> List[str]:
    errors: List[str] = []
    columnas = schema.get("columnas", []) or []
    if not columnas:
        return ["El esquema debe tener al menos una columna."]

    seen: Set[str] = set()
    for idx, col in enumerate(columnas, start=1):
        nombre = str(col.get("nombre", "")).strip()
        tipo = str(col.get("tipo", "")).strip()
        if not nombre:
            errors.append(f"La columna #{idx} no tiene nombre.")
            continue
        if nombre in seen:
            errors.append(f"La columna `{nombre}` está repetida en el esquema.")
        seen.add(nombre)
        if tipo not in _TIPOS:
            errors.append(f"La columna `{nombre}` tiene un tipo inválido.")

    ingestion_rule = schema.get("regla_ingesta") or {}
    if ingestion_rule:
        mode = str(ingestion_rule.get("modo") or "sin_regla").strip()
        reference_field = str(ingestion_rule.get("campo_referencia") or "").strip()
        column_names = {str(c.get("nombre", "")).strip() for c in columnas}
        if mode not in _INGESTION_MODES:
            errors.append("La regla de ingesta tiene un modo invalido.")
        elif mode != "sin_regla":
            if not reference_field:
                errors.append("La regla de ingesta requiere un campo referencial.")
            elif reference_field not in column_names:
                errors.append(f"El campo referencial `{reference_field}` no existe en el esquema.")
    return errors


def _validate_catalog_form(
    *,
    project_id: str | None = None,
    project_name: str | None = None,
    catalog_id: str,
    nombre: str,
    schema: dict,
) -> List[str]:
    errors: List[str] = []
    if project_id is not None:
        if not project_id:
            errors.append("El ID del proyecto no puede estar vacío.")
        elif not re.match(r"^[a-z0-9_]+$", project_id):
            errors.append("El ID del proyecto solo puede tener minúsculas, números y guiones bajos.")
    if project_name is not None and not project_name:
        errors.append("El nombre del proyecto no puede estar vacío.")
    if not catalog_id:
        errors.append("El ID del catálogo no puede estar vacío.")
    elif not re.match(r"^[a-z0-9_]+$", catalog_id):
        errors.append("El ID del catálogo solo puede tener minúsculas, números y guiones bajos.")
    if not nombre:
        errors.append("El nombre legible no puede estar vacío.")
    errors.extend(_validate_schema_definition(schema))
    return errors


def _validate_project_fields(project_id: str, project_name: str) -> List[str]:
    errors: List[str] = []
    if not project_id:
        errors.append("El ID del proyecto no puede estar vacío.")
    elif not re.match(r"^[a-z0-9_]+$", project_id):
        errors.append("El ID del proyecto solo puede tener minúsculas, números y guiones bajos.")
    if not project_name:
        errors.append("El nombre del proyecto no puede estar vacío.")
    return errors


def _validate_bulk_configs(db: str, tables: List[str]) -> List[str]:
    if not db:
        return []
    errors: List[str] = []
    fuente = st.session_state.get("adm_step2_fuente") or st.session_state.get("adm_fuente", "SingleStore")
    for table in tables:
        cfg = _get_bulk_table_config(db, table)
        schema = cfg.get("schema")
        if not schema:
            try:
                rows = describe_hive_table(db, table) if fuente == "Hive" else describe_table(db, table)
                schema = build_schema_json(rows)
            except Exception:
                continue
        cfg_errors = _validate_catalog_form(
            catalog_id=str(cfg.get("catalog_id", "")).strip(),
            nombre=str(cfg.get("nombre", "")).strip(),
            schema=schema,
        )
        for err in cfg_errors:
            errors.append(f"{table}: {err}")
    return errors


def _default_bulk_table_config(db: str, table: str) -> Dict[str, str]:
    catalog_id, nombre = _catalog_defaults(db, table)
    return {
        "catalog_id": catalog_id,
        "nombre": nombre,
        "descripcion": "",
        "schema": None,
    }


def _bulk_field_key(table: str, field: str) -> str:
    return f"adm_bulk_{_slugify_id(table)}_{field}"


def _ensure_bulk_table_fields(db: str, table: str) -> Dict[str, str]:
    configs = st.session_state.setdefault("adm_bulk_table_configs", {})
    cfg = configs.get(table, _default_bulk_table_config(db, table))
    for field in ("catalog_id", "nombre", "descripcion"):
        key = _bulk_field_key(table, field)
        if key not in st.session_state:
            st.session_state[key] = cfg.get(field, "")
    return cfg


def _sync_bulk_form_state(db: str, active_table: str) -> None:
    configs = st.session_state.setdefault("adm_bulk_table_configs", {})
    previous_table = st.session_state.get("adm_bulk_form_table")

    if previous_table:
        cid_key = _bulk_field_key(previous_table, "catalog_id")
        nombre_key = _bulk_field_key(previous_table, "nombre")
        desc_key = _bulk_field_key(previous_table, "descripcion")
        configs[previous_table] = {
            "catalog_id": st.session_state.get(cid_key, st.session_state.get("adm_bulk_form_cid", "")).strip(),
            "nombre": st.session_state.get(nombre_key, st.session_state.get("adm_bulk_form_nombre", "")).strip(),
            "descripcion": st.session_state.get(desc_key, st.session_state.get("adm_bulk_form_desc", "")).strip(),
            "schema": configs.get(previous_table, {}).get("schema"),
        }

    if previous_table != active_table:
        cfg = _ensure_bulk_table_fields(db, active_table)
        st.session_state["adm_bulk_form_cid"] = st.session_state[_bulk_field_key(active_table, "catalog_id")]
        st.session_state["adm_bulk_form_nombre"] = st.session_state[_bulk_field_key(active_table, "nombre")]
        st.session_state["adm_bulk_form_desc"] = st.session_state[_bulk_field_key(active_table, "descripcion")]
        st.session_state["adm_bulk_form_table"] = active_table


def _persist_bulk_form_state() -> None:
    current_table = st.session_state.get("adm_bulk_form_table")
    if not current_table:
        return
    configs = st.session_state.setdefault("adm_bulk_table_configs", {})
    cid_key = _bulk_field_key(current_table, "catalog_id")
    nombre_key = _bulk_field_key(current_table, "nombre")
    desc_key = _bulk_field_key(current_table, "descripcion")
    configs[current_table] = {
        "catalog_id": st.session_state.get(cid_key, st.session_state.get("adm_bulk_form_cid", "")).strip(),
        "nombre": st.session_state.get(nombre_key, st.session_state.get("adm_bulk_form_nombre", "")).strip(),
        "descripcion": st.session_state.get(desc_key, st.session_state.get("adm_bulk_form_desc", "")).strip(),
        "schema": configs.get(current_table, {}).get("schema"),
    }


def _render_project_inputs(
    db_name: str,
    key_prefix: str,
    projects: List[Dict],
    table_name: str | None = None,
) -> tuple[str, str, bool]:
    suggested_id, suggested_name = _project_defaults(db_name, table_name)
    existing_map = {p["id"]: p["nombre"] for p in projects}

    options = ["Crear o usar sugerido"]
    if existing_map:
        options.append("Usar proyecto existente")

    mode = st.radio(
        "Proyecto",
        options,
        key=f"{key_prefix}_project_mode",
        horizontal=True,
    )

    if mode == "Usar proyecto existente" and existing_map:
        selected_project_id = st.selectbox(
            "Proyecto existente",
            list(existing_map.keys()),
            format_func=lambda x: existing_map[x],
            key=f"{key_prefix}_project_existing",
        )
        return selected_project_id, existing_map[selected_project_id], False

    project_id = st.text_input(
        "ID del proyecto",
        value=st.session_state.get(f"{key_prefix}_project_id_default", suggested_id),
        key=f"{key_prefix}_project_id",
        help="Se genera automáticamente con la convención dg_au_agd_<origen>.",
        disabled=True,
    ).strip()
    project_name = st.text_input(
        "Nombre del proyecto",
        value=st.session_state.get(f"{key_prefix}_project_name_default", suggested_name),
        key=f"{key_prefix}_project_name",
        help="Puedes dejar el sugerido o escribir un nombre más amigable.",
    ).strip()
    return project_id, project_name, True


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
def render_admin_view() -> None:
    _inject_admin_css()

    with st.sidebar:
        b64 = _logo_b64()
        logo_img = f'<img src="data:image/png;base64,{b64}" width="56" style="flex-shrink:0;">' if b64 else ""
        st.markdown(f"""
        <div class="sidebar-brand" style="display:block;padding:8px 8px 12px;border-radius:12px;text-decoration:none;">
            <div style="display:flex; align-items:center; gap:12px; margin-bottom:6px;">
                {logo_img}
                <div style="min-width:0;">
                    <div style="font-size:11px;font-weight:500;color:#A8B4D8;letter-spacing:0.5px;">banco del</div>
                    <div style="font-size:22px;font-weight:800;color:white;letter-spacing:0;line-height:1.05;">Austro</div>
                </div>
            </div>
            <div style="font-size:12px;color:#DCE4FF;margin-left:68px;">Administración de Catálogos</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Volver al inicio", use_container_width=True, key="admin_brand_home", help="Volver al inicio"):
            st.session_state.current_view = "upload"
            st.session_state.current_step = "upload"
            st.session_state.pop("adm_catalog_dialog", None)
            for key in [
                "selected_project_id", "selected_project_name", "selected_catalog",
                "uploaded_df", "uploaded_bytes", "uploaded_audit_bytes",
                "uploaded_audit_name", "uploaded_name", "uploaded_preview_items",
                "uploaded_row_origins", "uploaded_validation_sources",
                "validation_result", "validation_failure_zip_path",
                "carga_ejecutada", "load_result",
            ]:
                st.session_state.pop(key, None)
            st.rerun()
        st.divider()
        if st.button("← Volver al portal", use_container_width=True):
            st.session_state.current_view = "upload"
            st.session_state.pop("adm_catalog_dialog", None)
            st.rerun()

    st.markdown("""
    <div style="padding: 8px 0 20px;">
        <h2 style="font-size:20px; font-weight:600; margin:0; color:var(--text-color);">
            Administración de catálogos
        </h2>
        <p style="font-size:13px; color:#6B7280; margin-top:4px;">
            Usa el explorador de tablas y el explorador de configuración para mapear
            catálogos en gatekeeper_meta y habilitarlos para los publicadores.
        </p>
    </div>
    """, unsafe_allow_html=True)

    admin_tabs = ["Resumen", "Registrar catálogos", "Catálogos activos", "Usuarios"]
    if st.session_state.get("adm_active_tab") not in admin_tabs:
        st.session_state.adm_active_tab = "Resumen"

    active_tab = st.radio(
        "Sección de administración",
        admin_tabs,
        horizontal=True,
        key="adm_active_tab",
        label_visibility="collapsed",
    )

    if active_tab != "Catálogos activos":
        st.session_state.pop("adm_catalog_dialog", None)

    if active_tab == "Resumen":
        _tab_resumen()
    elif active_tab == "Registrar catálogos":
        _tab_registro()
    elif active_tab == "Catálogos activos":
        _tab_activos()
    elif active_tab == "Usuarios":
        _tab_usuarios()


def _tab_resumen() -> None:
    st.markdown("""
    <div style="padding:4px 0 16px;">
        <p style="font-size:13px; color:#6B7280; margin:0;">
            Vista rápida de operación para revisar actividad reciente, fallos y catálogos más usados.
        </p>
    </div>
    """, unsafe_allow_html=True)

    ph = st.empty()
    ph.markdown(_skeleton_html(6, card=True), unsafe_allow_html=True)
    try:
        catalogs = get_active_catalogs()
        usuarios = get_all_usuarios()
        audit_rows = get_audit_log(limit=1000)
        ph.empty()
    except Exception as e:
        ph.empty()
        st.error(user_facing_error(e, context="database"))
        return

    total_catalogos = len(catalogs)
    total_usuarios = len(usuarios)
    usuarios_activos = sum(1 for u in usuarios if u.get("activo"))

    if audit_rows:
        audit_df = pd.DataFrame(audit_rows)
        total_cargas = len(audit_df)
        total_fallos = int((audit_df["estado_carga"] == "Fallo").sum())
        filas_ok = int(audit_df.loc[audit_df["estado_carga"] == "Exito", "filas_procesadas"].sum())
    else:
        audit_df = pd.DataFrame()
        total_cargas = 0
        total_fallos = 0
        filas_ok = 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Catálogos activos", f"{total_catalogos:,}")
    m2.metric("Usuarios", f"{total_usuarios:,}")
    m3.metric("Usuarios activos", f"{usuarios_activos:,}")
    m4.metric("Cargas recientes", f"{total_cargas:,}")
    m5.metric("Fallos recientes", f"{total_fallos:,}")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    s1, s2 = st.columns([2, 1])
    with s1:
        st.markdown("##### Actividad por día")
        if audit_df.empty:
            st.info("Aún no hay registros de auditoría para construir tendencias.")
        else:
            daily = audit_df.copy()
            daily["fecha"] = pd.to_datetime(daily["timestamp_carga"]).dt.date
            resumen_diario = (
                daily.groupby("fecha")
                .agg(cargas=("id", "count"), filas=("filas_procesadas", "sum"))
                .sort_index()
                .tail(14)
            )
            st.line_chart(resumen_diario["cargas"], use_container_width=True)
            st.caption(f"Filas exitosas acumuladas en la muestra: {filas_ok:,}")

    with s2:
        st.markdown("##### Estado rápido")
        st.markdown(
            f"""
            <div style="padding:12px 14px;background:var(--secondary-background-color);
                        border-radius:10px;border-left:3px solid {'#DC2626' if total_fallos else '#16A34A'};">
                <div style="font-size:13px;font-weight:600;">Últimos registros</div>
                <div style="font-size:12px;color:#6B7280;margin-top:6px;">
                    Exitosas: <b>{max(total_cargas - total_fallos, 0):,}</b><br>
                    Fallidas: <b>{total_fallos:,}</b><br>
                    Tasa de fallo: <b>{(total_fallos / total_cargas * 100 if total_cargas else 0):.1f}%</b>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### Catálogos más usados")
        if audit_df.empty:
            st.caption("Sin actividad todavía.")
        else:
            top_catalogs = (
                audit_df.groupby("id_catalogo")
                .agg(cargas=("id", "count"), filas=("filas_procesadas", "sum"))
                .sort_values(["cargas", "filas"], ascending=False)
                .head(10)
                .reset_index()
            )
            st.dataframe(top_catalogs, use_container_width=True, hide_index=True)

    with c2:
        st.markdown("##### Últimos fallos")
        if audit_df.empty or "estado_carga" not in audit_df.columns:
            st.caption("Sin fallos registrados.")
        else:
            fallos = audit_df[audit_df["estado_carga"] == "Fallo"].copy()
            if fallos.empty:
                st.success("No hay fallos en la muestra reciente.")
            else:
                cols = ["timestamp_carga", "usuario_ad", "id_catalogo", "nombre_archivo_original"]
                fallos = fallos[cols].copy().head(10)
                fallos.rename(columns={
                    "timestamp_carga": "Fecha/Hora",
                    "usuario_ad": "Usuario",
                    "id_catalogo": "Catálogo",
                    "nombre_archivo_original": "Archivo",
                }, inplace=True)
                fallos["Fecha/Hora"] = pd.to_datetime(fallos["Fecha/Hora"]).dt.strftime("%Y-%m-%d %H:%M")
                st.dataframe(fallos, use_container_width=True, hide_index=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("##### KPIs por proyecto")
    if audit_df.empty:
        st.caption("Sin actividad para calcular KPIs por proyecto.")
    else:
        by_project = (
            audit_df.groupby("project_id")
            .agg(
                cargas=("id", "count"),
                filas=("filas_procesadas", "sum"),
                fallos=("estado_carga", lambda s: int((s == "Fallo").sum())),
            )
            .sort_values(["cargas", "filas"], ascending=False)
            .reset_index()
        )
        by_project["tasa_fallo_pct"] = by_project.apply(
            lambda row: round((row["fallos"] / row["cargas"]) * 100, 1) if row["cargas"] else 0.0,
            axis=1,
        )
        st.dataframe(by_project, use_container_width=True, hide_index=True)


# ------------------------------------------------------------------
# Tab 1: Registro progresivo de catálogos
# ------------------------------------------------------------------
def _tab_registro() -> None:
    _SUBTABS = ["Selección", "Configuración", "Revisión"]

    # Aplicar navegación pendiente ANTES de crear el widget
    _pending = st.session_state.pop("adm_reg_subtab_next", None)
    if _pending in _SUBTABS:
        st.session_state.adm_reg_subtab = _pending
    elif st.session_state.get("adm_reg_subtab") not in _SUBTABS:
        st.session_state.adm_reg_subtab_next = "Selección"

    active_subtab = st.radio(
        "Paso del registro",
        _SUBTABS,
        horizontal=True,
        key="adm_reg_subtab",
        label_visibility="collapsed",
    )

    # Estado compartido entre sub-tabs (leído de session_state)
    # adm_db es clave de widget y puede perderse cuando el selectbox no renderiza;
    # adm_db_persist es clave no-widget que sobrevive entre sub-tabs.
    selected: List[str] = list(st.session_state.get("adm_selected_tables") or [])
    db_sel: str = str(
        st.session_state.get("adm_db")
        or st.session_state.get("adm_db_persist")
        or ""
    )
    mapped: Set[tuple] = set()

    # ── Sub-tab: Selección ─────────────────────────────────────────
    if active_subtab == "Selección":
        fuentes = ["SingleStore"] + (["Hive"] if HIVE_ENABLED else [])
        fuente = st.radio(
            "Fuente de datos",
            fuentes,
            horizontal=True,
            key="adm_fuente",
        )
        st.session_state["adm_step2_fuente"] = fuente

        if fuente == "SingleStore":
            try:
                databases = get_all_databases()
            except Exception as e:
                st.error(user_facing_error(e, context="singlestore"))
                return
            if not databases:
                st.warning("No hay bases de datos disponibles en SingleStore.")
                return
        else:
            try:
                databases = get_hive_databases()
            except Exception as e:
                st.error(user_facing_error(e, context="hive"))
                return
            if not databases:
                st.warning("No hay bases de datos disponibles en Hive.")
                return

        step_indicator = st.empty()

        col_left, col_right = st.columns([1, 2], gap="large")

        with col_left:
            st.markdown("##### Explorador de tablas")
            st.caption("Elige la base de datos y marca una o varias tablas para registrarlas.")

            db_sel = st.selectbox(
                "Base de datos", databases, key="adm_db", label_visibility="collapsed"
            )
            st.session_state["adm_db_persist"] = db_sel

            prev_key = f"adm_prev_db_{fuente}"
            if st.session_state.get(prev_key) != db_sel:
                for k in list(st.session_state.keys()):
                    if k.startswith("adm_chk_") or k.startswith("adm_cat_info_"):
                        del st.session_state[k]
                st.session_state.pop("adm_schema_key", None)
                st.session_state.pop("adm_selected_tables", None)
                st.session_state.pop("adm_active_table", None)
                st.session_state.pop("adm_tbl_page", None)
                st.session_state.pop("adm_sel_set", None)
                st.session_state[prev_key] = db_sel

            if st.button("↺ Refrescar tablas", key="adm_refresh_tables", use_container_width=True):
                get_tables_from_db.clear()
                if HIVE_ENABLED:
                    get_hive_tables.clear()
                st.rerun()

            ph_tables = st.empty()
            ph_tables.markdown(_skeleton_html(8), unsafe_allow_html=True)
            try:
                if fuente == "SingleStore":
                    all_tables = get_tables_from_db(db_sel)
                else:
                    all_tables = get_hive_tables(db_sel)
                mapped = get_mapped_tables()
                ph_tables.empty()
            except Exception as e:
                ph_tables.empty()
                st.error(user_facing_error(e, context="database"))
                return

            _ci_key = f"adm_cat_info_{db_sel}"
            if _ci_key not in st.session_state:
                try:
                    ci: Dict = {}
                    for cat in get_active_catalogs():
                        if cat["base_datos"] == db_sel:
                            perms = get_catalog_permissions(cat["catalog_id"])
                            ci[(db_sel, cat["tabla_destino"])] = [
                                p["valor"] for p in perms if p["tipo"] == "rol"
                            ]
                    st.session_state[_ci_key] = ci
                except Exception:
                    st.session_state[_ci_key] = {}
            cat_info: Dict = st.session_state[_ci_key]

            if not all_tables:
                st.warning(f"No hay tablas en `{db_sel}`.")
                return

            reg_count  = sum(1 for t in all_tables if (db_sel, t) in mapped)
            disp_count = len(all_tables) - reg_count
            st.markdown(f"""
            <div style="font-size:12px; color:#6B7280; margin:8px 0 10px;">
                <span style="color:#16A34A; font-weight:600;">✓ {reg_count} registradas</span>
                &nbsp;·&nbsp;
                <span style="color:#534AB7; font-weight:600;">{disp_count} disponibles</span>
                &nbsp;·&nbsp; {len(all_tables)} total
            </div>
            """, unsafe_allow_html=True)

            search = st.text_input(
                "Filtrar tablas", placeholder="Buscar tabla...",
                key="adm_tbl_search", label_visibility="collapsed"
            )
            filtered = [t for t in all_tables if not search or search.lower() in t.lower()]

            if not filtered:
                st.caption("Sin resultados.")
                return

            bc1, bc2 = st.columns(2)
            with bc1:
                if st.button("Sel. disponibles", use_container_width=True, key="adm_sel_all"):
                    _sel = st.session_state.setdefault("adm_sel_set", set())
                    for t in filtered:
                        if (db_sel, t) not in mapped:
                            st.session_state[f"adm_chk_{t}"] = True
                            _sel.add(t)
                    st.rerun()
            with bc2:
                if st.button("Limpiar", use_container_width=True, key="adm_sel_clear"):
                    for t in all_tables:
                        st.session_state[f"adm_chk_{t}"] = False
                    st.session_state.pop("adm_sel_set", None)
                    st.session_state.pop("adm_active_table", None)
                    st.rerun()

            prev_search = st.session_state.get("adm_tbl_prev_search", "")
            if search != prev_search:
                st.session_state.adm_tbl_page = 0
                st.session_state.adm_tbl_prev_search = search

            page      = st.session_state.get("adm_tbl_page", 0)
            page_size = 10
            total_pgs = max(1, (len(filtered) + page_size - 1) // page_size)
            page      = min(page, total_pgs - 1)
            page_tables = filtered[page * page_size : (page + 1) * page_size]

            st.markdown("##### Tablas")
            _adm_sel = st.session_state.setdefault("adm_sel_set", set())
            for t in page_tables:
                if (db_sel, t) not in mapped:
                    chk_key = f"adm_chk_{t}"
                    if chk_key not in st.session_state:
                        st.session_state[chk_key] = t in _adm_sel
            for t in page_tables:
                is_reg = (db_sel, t) in mapped
                if is_reg:
                    roles = cat_info.get((db_sel, t), [])
                    badge_html = "".join(
                        f'<span style="background:#EDE9FE;color:#534AB7;font-size:9px;font-weight:600;'
                        f'padding:1px 5px;border-radius:10px;margin-right:2px;">{r}</span>'
                        for r in roles
                    ) if roles else (
                        '<span style="background:#D1FAE5;color:#065F46;font-size:9px;font-weight:600;'
                        'padding:1px 5px;border-radius:10px;">Público</span>'
                    )
                    chk_col, lbl_col = st.columns([1, 8], gap="small")
                    with chk_col:
                        st.checkbox("", key=f"adm_chk_{t}", label_visibility="collapsed", disabled=True, value=False)
                    with lbl_col:
                        st.markdown(
                            '<div style="display:flex;align-items:center;gap:5px;margin-top:-4px;">'
                            f'<span style="color:#16A34A;font-size:12px;font-weight:500;">✓ {t}</span>'
                            + badge_html
                            + '</div>',
                            unsafe_allow_html=True,
                        )
                else:
                    st.checkbox(t, key=f"adm_chk_{t}")

            if total_pgs > 1:
                pg1, pg2, pg3 = st.columns([1, 2, 1])
                with pg1:
                    if st.button("←", use_container_width=True, key="adm_tbl_prev",
                                 disabled=page == 0):
                        st.session_state.adm_tbl_page = page - 1
                        st.rerun()
                with pg2:
                    st.caption(f"Página {page + 1} de {total_pgs}  ({len(filtered)} tablas)")
                with pg3:
                    if st.button("→", use_container_width=True, key="adm_tbl_next",
                                 disabled=page >= total_pgs - 1):
                        st.session_state.adm_tbl_page = page + 1
                        st.rerun()

            selected = _sync_selected_tables(all_tables)
            with step_indicator:
                _render_step_indicator(current=2 if selected else 1)

            if selected:
                st.markdown("##### Seleccionadas")
                st.caption(f"{len(selected)} tabla(s) en la lista actual de registro")

        with col_right:
            if not selected:
                _render_empty_state()
            else:
                active_table = _render_active_table_selector(selected, key_suffix="selection")
                _render_selection_schema_preview(db_sel, active_table, mapped)

        # ── Botón navegación ───────────────────────────────────────
        selected = list(st.session_state.get("adm_selected_tables") or [])
        if selected:
            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
            _, _nav_r = st.columns([3, 1])
            with _nav_r:
                if st.button("Configuración →", type="primary", use_container_width=True, key="btn_reg_sel_next"):
                    st.session_state.adm_reg_subtab_next = "Configuración"
                    st.rerun()

    # ── Sub-tab: Configuración ─────────────────────────────────────
    elif active_subtab == "Configuración":
        if not selected or not db_sel:
            st.info("Primero selecciona una base de datos y tablas en la pestaña **Selección**.")
        else:
            try:
                mapped = get_mapped_tables()
            except Exception as e:
                st.error(user_facing_error(e, context="database"))
                return
            if len(selected) == 1:
                active_table = selected[0]
                st.session_state.adm_active_table = active_table
                _render_config_only(db_sel, selected[0], mapped)
            else:
                active_table = st.session_state.get("adm_active_table")
                if active_table not in selected:
                    active_table = selected[0]
                    st.session_state.adm_active_table = active_table
                _render_bulk_panel(db_sel, selected, mapped, active_table)

    # ── Sub-tab: Revisión ──────────────────────────────────────────
    elif active_subtab == "Revisión":
        _back_rev, _ = st.columns([1, 3])
        with _back_rev:
            if st.button("← Configuración", use_container_width=True, key="btn_reg_rev_back"):
                st.session_state.adm_reg_subtab_next = "Configuración"
                st.rerun()

        if not selected or not db_sel:
            st.info("Cuando selecciones tablas, aquí verás el resumen antes de registrar.")
        else:
            try:
                mapped = get_mapped_tables()
            except Exception as e:
                st.error(user_facing_error(e, context="database"))
                return
            configs_saved = st.session_state.get("adm_bulk_table_configs", {})
            _render_register_flow_summary(db_sel, selected, mapped, configs_saved)


# ------------------------------------------------------------------
# Estado vacío
# ------------------------------------------------------------------
def _render_empty_state() -> None:
    st.markdown("""
    <div style="margin-top:80px; text-align:center; color:#9CA3AF;">
        <div style="margin-bottom:14px;">
            <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24"
                 fill="none" stroke="#9CA3AF" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                <ellipse cx="12" cy="5" rx="9" ry="3"/>
                <path d="M21 12c0 1.66-4.03 3-9 3S3 13.66 3 12"/>
                <path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/>
            </svg>
        </div>
        <div style="font-size:15px; font-weight:600; margin-bottom:8px; color:#6B7280;">
            Selecciona tablas para continuar
        </div>
        <div style="font-size:13px; line-height:1.8; color:#9CA3AF;">
            <b>1 tabla</b> → ver esquema y registrarla individualmente<br>
            <b>Varias tablas</b> → registrarlas en lote con una configuración común
        </div>
    </div>
    """, unsafe_allow_html=True)


# ------------------------------------------------------------------
# Wizard: indicador de pasos
# ------------------------------------------------------------------
def _render_step_indicator(current: int) -> None:
    steps = ["Seleccionar tablas", "Configurar catálogo", "Revisar y registrar"]
    parts = []
    for i, name in enumerate(steps, start=1):
        if i < current:
            cls, icon = "done", "✓"
        elif i == current:
            cls, icon = "active", str(i)
        else:
            cls, icon = "pending", str(i)
        parts.append(
            f'<div class="adm-step-card {cls}">'
            f'<span class="adm-step-dot">{icon}</span>'
            f'<span><b>Paso {i}</b><br>{name}</span>'
            f'</div>'
        )
    st.markdown(
        '<div class="adm-step-row">'
        + "".join(parts)
        + "</div>",
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------
# Wizard paso 1: esquema de tabla (sin form de registro)
# ------------------------------------------------------------------
def _render_schema_only(db: str, table: str, mapped: Set[tuple]) -> None:
    already    = (db, table) in mapped
    status_col = "#16A34A" if already else "#534AB7"
    status_txt = "Ya registrada" if already else "Disponible"

    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">'
        '<span style="font-size:15px;font-weight:700;">'
        '<code style="color:#6B7280;">' + db + '.</code>'
        '<code style="color:' + status_col + ';">' + table + '</code>'
        '</span>'
        '<span style="background:' + ('#DCFCE7' if already else '#EDE9FE') + ';'
        'color:' + status_col + ';font-size:11px;font-weight:600;'
        'padding:2px 10px;border-radius:20px;'
        'border:1px solid ' + status_col + '40;">' + status_txt + '</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    schema = _load_schema_into_state(db, table)
    if schema is None:
        return

    columnas = schema.get("columnas", [])
    st.markdown(f"**{len(columnas)} columna(s)**")

    if already:
        _render_schema_readonly(schema)
    else:
        with st.expander("Esquema — edita tipos y nulabilidad", expanded=True):
            _render_schema_editor(schema, key_prefix="ex")


def _render_selection_schema_preview(db: str, table: str, mapped: Set[tuple]) -> None:
    already = (db, table) in mapped
    status_col = "#16A34A" if already else "#534AB7"
    status_txt = "Ya registrada" if already else "Disponible"
    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">'
        '<span style="font-size:15px;font-weight:700;">'
        '<code style="color:#6B7280;">' + html_escape(db) + '.</code>'
        '<code style="color:' + status_col + ';">' + html_escape(table) + '</code>'
        '</span>'
        '<span style="background:' + ('#DCFCE7' if already else '#EDE9FE') + ';'
        'color:' + status_col + ';font-size:11px;font-weight:600;'
        'padding:2px 10px;border-radius:20px;'
        'border:1px solid ' + status_col + '40;">' + status_txt + '</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    schema = _load_schema_into_state(db, table)
    if schema is None:
        return
    columnas = schema.get("columnas", [])
    st.markdown(f"**Vista rápida de columnas ({len(columnas)})**")
    _render_schema_readonly(schema)
    st.info("La edición de columnas, reglas e ingesta se realiza en la pestaña **Configuración**.")


def _render_bulk_table_scroll(
    selected: List[str],
    mapped: Set[tuple],
    db: str,
    active_table: str,
    configs_saved: Dict[str, Dict],
) -> None:
    chips = []
    for table in selected:
        if (db, table) in mapped:
            label, cls = "Ya registrada", "done"
        elif table in configs_saved:
            label, cls = "Configurada", "done"
        elif table == active_table:
            label, cls = "Activa", "active"
        else:
            label, cls = "Pendiente", "pending"
        chips.append(
            f'<div class="adm-scroll-chip {cls}">'
            f'<code>{html_escape(table)}</code>'
            f'<span>{label}</span>'
            f'</div>'
        )
    st.markdown(
        '<div class="adm-scroll-strip">' + "".join(chips) + "</div>",
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------
# Wizard paso 2: configuración y registro (ancho completo)
# ------------------------------------------------------------------
def _render_step2() -> None:
    col_back, _ = st.columns([1, 5])
    with col_back:
        if st.button("← Volver", use_container_width=True, key="adm_step2_back"):
            st.session_state.adm_step = 1
            st.rerun()

    _render_step_indicator(current=2)

    db_sel   = st.session_state.get("adm_step2_db", "")
    selected: List[str] = list(st.session_state.get("adm_step2_selected", []))

    if not selected or not db_sel:
        st.warning("No hay tablas seleccionadas. Vuelve al paso anterior.")
        return

    try:
        mapped: Set[tuple] = get_mapped_tables()
    except Exception as e:
        st.error(user_facing_error(e, context="database"))
        return

    if len(selected) == 1:
        _render_config_only(db_sel, selected[0], mapped)
    else:
        active_table = _render_active_table_selector(selected, key_suffix="step2")
        _render_bulk_panel(db_sel, selected, mapped, active_table, show_schema=False)


# ------------------------------------------------------------------
# Wizard paso 2: config de 1 sola tabla
# ------------------------------------------------------------------
def _render_config_only(db: str, table: str, mapped: Set[tuple]) -> None:
    already    = (db, table) in mapped
    status_col = "#16A34A" if already else "#534AB7"
    status_txt = "Ya registrada" if already else "Disponible"

    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">'
        '<span style="font-size:15px;font-weight:700;">'
        '<code style="color:#6B7280;">' + db + '.</code>'
        '<code style="color:' + status_col + ';">' + table + '</code>'
        '</span>'
        '<span style="background:' + ('#DCFCE7' if already else '#EDE9FE') + ';'
        'color:' + status_col + ';font-size:11px;font-weight:600;'
        'padding:2px 10px;border-radius:20px;'
        'border:1px solid ' + status_col + '40;">' + status_txt + '</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    schema = _load_schema_into_state(db, table)
    if schema is None:
        return

    if already:
        catalog_id = _catalog_id_for_existing_table(db, table)
        _render_permission_manager_inline(catalog_id, f"pm_{catalog_id}")
    else:
        _render_registro_form(db, table, schema, key_prefix="ex")


# ------------------------------------------------------------------
# Panel derecho: 1 tabla seleccionada
# ------------------------------------------------------------------
def _render_single_panel(db: str, table: str, mapped: Set[tuple]) -> None:
    already    = (db, table) in mapped
    status_col = "#16A34A" if already else "#534AB7"
    status_txt = "Ya registrada" if already else "Disponible"

    st.markdown(f"""
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
        <span style="font-size:15px; font-weight:700;">
            <code style="color:#6B7280;">{db}.</code><code style="color:{status_col};">{table}</code>
        </span>
        <span style="background:{'#DCFCE7' if already else '#EDE9FE'};
                     color:{status_col}; font-size:11px; font-weight:600;
                     padding:2px 10px; border-radius:20px;
                     border:1px solid {status_col}40;">{status_txt}</span>
    </div>
    """, unsafe_allow_html=True)

    schema = _load_schema_into_state(db, table)
    if schema is None:
        return
    columnas = schema.get("columnas", [])
    st.markdown(f"**{len(columnas)} columnas**")

    if already:
        # Esquema solo lectura + gestión de permisos
        _render_schema_readonly(schema)
        st.divider()
        catalog_id = _catalog_id_for_existing_table(db, table)
        _render_permission_manager_inline(catalog_id, f"pm_{catalog_id}")
    else:
        # Esquema editable + formulario de registro
        with st.expander("Esquema — edita tipos y nulabilidad", expanded=True):
            _render_schema_editor(schema, key_prefix="ex")
        st.divider()
        _render_registro_form(db, table, schema, key_prefix="ex")


@st.experimental_dialog("Regla de calidad", width="large")
def _dialog_reglas_calidad(columns: list, state_key: str) -> None:
    _render_rules_editor(columns, state_key)


@st.experimental_dialog("Reglas de ingesta", width="large")
def _dialog_reglas_ingesta(columns: list, state_key: str, estrategia: str) -> None:
    _render_ingestion_rule_form(columns, state_key, estrategia)


# ------------------------------------------------------------------
# Panel derecho: múltiples tablas seleccionadas (registro en lote)
# ------------------------------------------------------------------
def _render_bulk_panel(db: str, selected: List[str], mapped: Set[tuple], active_table: str, show_schema: bool = True) -> None:
    already_reg = [t for t in selected if (db, t) in mapped]
    to_register = [t for t in selected if (db, t) not in mapped]
    configs_saved = st.session_state.get("adm_bulk_table_configs", {})
    saved_count = sum(1 for t in to_register if t in configs_saved)

    try:
        projects = get_all_projects()
    except Exception as e:
        st.error(user_facing_error(e, context="database"))
        return

    st.markdown("##### Configuración del registro")
    st.caption(
        "El proyecto, permisos y carga aplican a todas las tablas. "
        "Los datos, columnas y reglas se configuran para la tabla activa."
    )

    active_table = _sync_bulk_active_table_pickers(selected)
    _sync_bulk_form_state(db, active_table)
    _ensure_bulk_table_fields(db, active_table)

    status_items = []
    for t in selected:
        is_active = t == active_table
        is_reg = (db, t) in mapped
        is_saved = t in configs_saved
        if is_reg:
            label, cls = "Ya registrada", "done"
        elif is_saved:
            label, cls = "Configurada", "done"
        elif is_active:
            label, cls = "En edición", "active"
        else:
            label, cls = "Pendiente", "pending"
        status_items.append(
            f'<div class="adm-table-status {cls}">'
            f'<code>{html_escape(t)}</code>'
            f'<span>{label}</span>'
            f'</div>'
        )
    st.markdown(
        '<div class="adm-table-status-list">' + "".join(status_items) + "</div>",
        unsafe_allow_html=True,
    )

    tab_project, tab_data, tab_columns, tab_permissions, tab_load, tab_review = st.tabs(
        ["Proyecto", "Datos", "Columnas", "Permisos", "Carga", "Revisión"]
    )

    with tab_project:
        bk_proj, bk_project_name, create_project = _render_project_inputs(db, "adm_bk", projects, active_table)
        st.info("Este proyecto agrupa todas las tablas seleccionadas en este registro.")

    with tab_data:
        active_table = _render_bulk_active_table_selector(selected, "data")
        _sync_bulk_form_state(db, active_table)
        _ensure_bulk_table_fields(db, active_table)
        active_is_registered = (db, active_table) in mapped
        cid_key = _bulk_field_key(active_table, "catalog_id")
        nombre_key = _bulk_field_key(active_table, "nombre")
        desc_key = _bulk_field_key(active_table, "descripcion")
        if active_is_registered:
            st.info("Esta tabla ya está registrada. Se omitirá en el registro del lote.")
        catalog_id = st.text_input(
            "ID del catálogo",
            key=cid_key,
            disabled=True,
        )
        nombre = st.text_input(
            "Nombre legible",
            key=nombre_key,
            disabled=active_is_registered,
        )
        descripcion = st.text_area(
            "Descripción (opcional)",
            key=desc_key,
            height=74,
            disabled=active_is_registered,
        )

    active_key_prefix = _bulk_table_prefix(active_table)
    schema = _load_schema_into_state(db, active_table)
    current_schema = schema or {"columnas": []}
    rule_columns: List[Dict] = []

    with tab_columns:
        st.markdown("**Columnas de la tabla activa**")
        active_table = _render_bulk_active_table_selector(selected, "columns")
        _sync_bulk_form_state(db, active_table)
        _ensure_bulk_table_fields(db, active_table)
        active_key_prefix = _bulk_table_prefix(active_table)
        schema = _load_schema_into_state(db, active_table)
        current_schema = schema or {"columnas": []}
        _render_bulk_table_scroll(selected, mapped, db, active_table, configs_saved)
        st.caption(f"Tabla activa: `{active_table}`")
        if schema is None:
            st.warning("No se pudo leer el esquema de la tabla activa.")
        elif (db, active_table) in mapped:
            st.info("Esta tabla ya está registrada. El esquema se muestra solo como referencia.")
            _render_schema_readonly(schema)
        else:
            st.caption("Edita nombre, tipo y si cada columna acepta vacíos.")
            _render_schema_editor(schema, key_prefix=active_key_prefix, show_rule_actions=False)
            current_schema = _collect_schema(schema, active_key_prefix)

        schema_cache_key = st.session_state.get("adm_schema_key", "default")
        rules_key = f"{active_key_prefix}_rules_{hash(schema_cache_key)}"
        ingestion_key = f"{active_key_prefix}_ingestion_{hash(schema_cache_key)}"
        rule_columns = [
            {
                "nombre": str(col.get("nombre", "")).strip(),
                "tipo": str(col.get("tipo", "str") or "str").strip().lower(),
            }
            for col in current_schema.get("columnas", [])
            if str(col.get("nombre", "")).strip()
        ]
        st.markdown("---")
        _bk_est = st.session_state.get("adm_bk_est", "overwrite")
        _bcal, _bing = st.columns(2)
        with _bcal:
            if st.button("Regla de calidad", use_container_width=True, key=f"btn_calidad_{active_key_prefix}"):
                _dialog_reglas_calidad(rule_columns, rules_key)
        with _bing:
            if st.button("Reglas de ingesta", use_container_width=True, key=f"btn_ingesta_{active_key_prefix}"):
                _dialog_reglas_ingesta(rule_columns, ingestion_key, _bk_est)

    if schema is not None:
        current_schema = _collect_schema(schema, active_key_prefix) if (db, active_table) not in mapped else schema
        rule_columns = [
            {
                "nombre": str(col.get("nombre", "")).strip(),
                "tipo": str(col.get("tipo", "str") or "str").strip().lower(),
            }
            for col in current_schema.get("columnas", [])
            if str(col.get("nombre", "")).strip()
        ]
    schema_cache_key = st.session_state.get("adm_schema_key", "default")
    rules_key = f"{active_key_prefix}_rules_{hash(schema_cache_key)}"
    ingestion_key = f"{active_key_prefix}_ingestion_{hash(schema_cache_key)}"

    with tab_permissions:
        _render_permisos_selector("bk")

    with tab_load:
        c1, c2 = st.columns(2)
        with c1:
            bk_est = st.selectbox("Estrategia", _ESTRATEGIAS, key="adm_bk_est")
        with c2:
            _dest_default = 1 if HIVE_ENABLED and st.session_state.get("adm_step2_fuente") == "Hive" else 0
            bk_dest = st.selectbox("Destino", _DESTINOS, index=_dest_default, key="adm_bk_dest")
        st.info("La estrategia y el destino se aplican a todas las tablas pendientes del lote.")

    with tab_review:
        project_errors = _validate_project_fields(bk_proj, bk_project_name)
        active_errors: List[str] = []
        if active_table in to_register:
            active_errors = _validate_catalog_form(
                catalog_id=st.session_state.get(_bulk_field_key(active_table, "catalog_id"), "").strip(),
                nombre=st.session_state.get(_bulk_field_key(active_table, "nombre"), "").strip(),
                schema=current_schema,
            )

        if active_table in to_register:
            if st.button(
                "Guardar configuración de esta tabla",
                type="primary",
                use_container_width=True,
                key=f"adm_bulk_save_{active_table}",
                disabled=bool(active_errors),
            ):
                configs = st.session_state.setdefault("adm_bulk_table_configs", {})
                configs[active_table] = {
                    "catalog_id": st.session_state.get(_bulk_field_key(active_table, "catalog_id"), "").strip(),
                    "nombre": st.session_state.get(_bulk_field_key(active_table, "nombre"), "").strip(),
                    "descripcion": st.session_state.get(_bulk_field_key(active_table, "descripcion"), "").strip(),
                    "schema": current_schema,
                }
                st.success(f"Configuración de **{active_table}** guardada.")
                st.rerun()

        configs_saved = st.session_state.get("adm_bulk_table_configs", {})
        bulk_config_errors = _validate_bulk_configs(db, to_register)

        _render_bulk_registration_review(
            db=db,
            tables=to_register,
            project_name=bk_project_name,
            estrategia=bk_est,
            destino=bk_dest,
            permisos=_collect_permisos("bk"),
            configs_saved=configs_saved,
        )

        for err in project_errors + active_errors + bulk_config_errors:
            st.warning(err)

        if already_reg:
            st.caption(f"Se omitirán (ya existen): {', '.join(already_reg)}")

        if not to_register:
            st.info("Todas las tablas seleccionadas ya están registradas.")
            return

        if st.button(
            f"Registrar tablas seleccionadas ({len(to_register)})", type="primary",
            use_container_width=True, key="adm_bk_go", disabled=bool(project_errors or bulk_config_errors)
        ):
            _persist_bulk_form_state()
            if create_project:
                ensure_project_exists(bk_proj, bk_project_name)
            permisos = _collect_permisos("bk")
            _ejecutar_registro_masivo(db, to_register, bk_proj, bk_est, bk_dest, permisos)


def _ejecutar_registro_masivo(
    db: str, tables: List[str], project_id: str,
    estrategia: str, destino: str, permisos: List[Dict],
) -> None:
    progress = st.progress(0, text="Iniciando registro...")
    ok, skipped, failed = [], [], []

    for i, table in enumerate(tables):
        progress.progress((i + 1) / len(tables), text=f"Procesando `{table}`...")
        try:
            cfg = _get_bulk_table_config(db, table)
            schema = cfg.get("schema")
            if not schema:
                fuente = st.session_state.get("adm_step2_fuente") or st.session_state.get("adm_fuente", "SingleStore")
                rows = describe_hive_table(db, table) if fuente == "Hive" else describe_table(db, table)
                schema = build_schema_json(rows)
            catalog_id = cfg["catalog_id"]
            if catalog_exists(catalog_id):
                skipped.append(table)
                continue
            save_catalog_config(
                catalog_id    = catalog_id,
                project_id    = project_id,
                nombre        = cfg["nombre"],
                descripcion   = cfg["descripcion"],
                base_datos    = db,
                tabla_destino = table,
                destino       = destino,
                estrategia    = estrategia,
                schema_json   = schema,
            )
            save_permissions(catalog_id, permisos)
            ok.append(table)
        except Exception as e:
            failed.append(f"{table}: {user_facing_error(e, context='database')}")

    progress.empty()

    if ok:
        st.success(f"{len(ok)} catálogo(s) registrado(s): {', '.join(ok)}")
    if skipped:
        st.info(f"{len(skipped)} ya existían (ignorados): {', '.join(skipped)}")
    if failed:
        st.error(f"{len(failed)} error(es) al registrar:")
        for msg in failed:
            st.caption(f"• {msg}")

    # Limpiar selección y forzar recarga
    for k in list(st.session_state.keys()):
        if k.startswith("adm_chk_"):
            st.session_state[k] = False
    st.rerun()


# ------------------------------------------------------------------
# Componentes reutilizables
# ------------------------------------------------------------------
def _render_schema_readonly(schema: dict) -> None:
    cols = schema.get("columnas", [])
    if not cols:
        st.caption("Sin columnas definidas.")
        return

    from collections import Counter
    tipo_counts = Counter(c["tipo"] for c in cols)
    nullable_n  = sum(1 for c in cols if c.get("nullable"))

    tipo_badges = "".join(
        f'<span style="background:#EDE9FE;color:#534AB7;font-size:10px;font-weight:600;'
        f'padding:1px 7px;border-radius:20px;margin-right:4px;">{t} ×{n}</span>'
        for t, n in sorted(tipo_counts.items())
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:6px 0 10px;">'
        f'<span style="font-size:12px;color:#6B7280;font-weight:500;">{len(cols)} columnas</span>'
        f'<span style="color:#D1D9F0;">|</span>'
        f'{tipo_badges}'
        f'<span style="background:#D1FAE5;color:#065F46;font-size:10px;font-weight:600;'
        f'padding:1px 7px;border-radius:20px;">{nullable_n} aceptan vacíos</span>'
        f'<span style="background:#FEE2E2;color:#991B1B;font-size:10px;font-weight:600;'
        f'padding:1px 7px;border-radius:20px;">{len(cols)-nullable_n} requerido</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    rows_html = "".join(
        f'<div style="display:flex;align-items:center;justify-content:space-between;'
        f'padding:6px 10px;background:{"#F8FAFF" if i%2==0 else "white"};'
        f'border-radius:6px;margin-bottom:2px;">'
        f'<code style="font-size:12px;color:#1C2F6E;">{col["nombre"]}</code>'
        f'<div style="display:flex;align-items:center;gap:6px;">'
        f'<span style="background:#EDE9FE;color:#534AB7;font-size:10px;font-weight:600;'
        f'padding:1px 8px;border-radius:20px;">{col["tipo"]}</span>'
        + (
            '<span style="background:#D1FAE5;color:#065F46;font-size:10px;font-weight:500;'
            'padding:1px 8px;border-radius:20px;">Acepta vacíos</span>'
            if col.get("nullable") else
            '<span style="background:#FEE2E2;color:#991B1B;font-size:10px;font-weight:500;'
            'padding:1px 8px;border-radius:20px;">requerido</span>'
        )
        + '</div></div>'
        for i, col in enumerate(cols)
    )
    st.markdown(
        f'<div style="border:1px solid #E5E9F5;border-radius:8px;overflow:hidden;padding:4px;">'
        + rows_html
        + '</div>',
        unsafe_allow_html=True,
    )


_RULE_CHIP = {
    "isin":       ("#92400E", "#FEF3C7", "#F59E0B", "DOMINIO"),
    "gte":        ("#1E3A8A", "#DBEAFE", "#3B82F6", "MÍN ≥"),
    "lte":        ("#1E3A8A", "#DBEAFE", "#3B82F6", "MÁX ≤"),
    "min_length": ("#065F46", "#D1FAE5", "#10B981", "LONG. MÍN"),
    "str_length": ("#065F46", "#D1FAE5", "#10B981", "LONGITUD"),
    "regex":      ("#4C1D95", "#F5F3FF", "#7C3AED", "REGEX"),
}


def _rule_chip_html(regla: dict) -> str:
    t = regla.get("tipo", "")
    fg, bg, border, label = _RULE_CHIP.get(t, ("#374151", "#F3F4F6", "#9CA3AF", t.upper()))
    if t == "isin":
        vals = ", ".join(str(v) for v in regla.get("valor", []))
        body = f"<b>{vals}</b>"
    elif t == "gte":
        body = f"<b>{regla.get('valor', '')}</b>"
    elif t == "lte":
        body = f"<b>{regla.get('valor', '')}</b>"
    elif t == "min_length":
        body = f"<b>{regla.get('valor', '')}</b> caracteres"
    elif t == "str_length":
        body = f"<b>{regla.get('min', '')}</b> – <b>{regla.get('max', '')}</b> caracteres"
    elif t == "regex":
        body = f"<code style='font-size:11px;background:transparent;'>{regla.get('valor', '')}</code>"
    else:
        body = str(regla)
    return (
        f'<div style="display:flex;align-items:center;gap:8px;'
        f'background:{bg};border:1px solid {border}40;'
        f'border-radius:8px;padding:7px 12px;margin-bottom:6px;">'
        f'<span style="background:{border};color:white;font-size:10px;font-weight:700;'
        f'padding:1px 7px;border-radius:20px;white-space:nowrap;">{label}</span>'
        f'<span style="color:{fg};font-size:12px;">{body}</span>'
        f'</div>'
    )


def _rule_detail_text(regla: dict) -> str:
    t = str(regla.get("tipo", "") or "").strip().lower()
    if t == "isin":
        vals = ", ".join(str(v) for v in regla.get("valor", []))
        return f"Dominio permitido: {vals or 'sin valores'}"
    if t == "gte":
        return f"Valor minimo: {regla.get('valor', '')}"
    if t == "lte":
        return f"Valor maximo: {regla.get('valor', '')}"
    if t == "min_length":
        return f"Longitud minima: {regla.get('valor', '')} caracteres"
    if t == "str_length":
        return f"Longitud entre {regla.get('min', '')} y {regla.get('max', '')} caracteres"
    if t == "regex":
        return f"Patron: {regla.get('valor', '')}"
    return str(regla)


def _rules_count_hover_html(display_df: pd.DataFrame, rules: dict) -> str:
    if display_df.empty:
        return ""

    chips = []
    for _, row in display_df.iterrows():
        name = str(row.get("nombre", "")).strip()
        col_rules = list(rules.get(name, []))
        if not name or not col_rules:
            continue
        title = html_escape(" | ".join(_rule_detail_text(r) for r in col_rules), quote=True)
        chips.append(
            f'<span title="{title}" style="display:inline-flex;align-items:center;gap:5px;'
            f'background:#EEF2FF;color:#1C2F6E;border:1px solid #C7D2FE;'
            f'padding:3px 8px;border-radius:999px;font-size:11px;cursor:help;">'
            f'<code style="font-size:11px;color:#1C2F6E;background:transparent;">{html_escape(name)}</code>'
            f'<b>{len(col_rules)}</b> regla(s)</span>'
        )

    if not chips:
        return (
            '<div style="font-size:12px;color:#9CA3AF;margin:6px 0 10px;">'
            'Sin reglas de calidad configuradas.</div>'
        )
    return (
        '<div style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 10px;">'
        '<span style="font-size:11px;color:#6B7280;font-weight:600;margin-right:2px;">'
        'Pasa el cursor sobre una regla:</span>'
        + "".join(chips)
        + '</div>'
    )


def _normalize_ingestion_rule(rule: dict | None) -> dict:
    rule = rule or {}
    mode = str(rule.get("modo") or rule.get("mode") or "sin_regla").strip()
    if mode not in _INGESTION_MODES:
        mode = "sin_regla"
    return {
        "modo": mode,
        "campo_referencia": str(rule.get("campo_referencia") or rule.get("reference_field") or "").strip(),
        "valor_unico_en_archivo": bool(rule.get("valor_unico_en_archivo", True)),
    }


def _init_ingestion_state(state_key: str, rule: dict | None) -> None:
    init_key = f"{state_key}_initialized"
    if st.session_state.get(init_key):
        return
    normalized = _normalize_ingestion_rule(rule)
    st.session_state[f"{state_key}_mode"] = normalized["modo"]
    st.session_state[f"{state_key}_field"] = normalized["campo_referencia"]
    st.session_state[f"{state_key}_single"] = normalized["valor_unico_en_archivo"]
    st.session_state[init_key] = True


def _collect_ingestion_rule(state_key: str) -> dict | None:
    mode = str(st.session_state.get(f"{state_key}_mode") or "sin_regla")
    if mode == "sin_regla":
        return None
    field = str(st.session_state.get(f"{state_key}_field") or "").strip()
    return {
        "modo": mode,
        "campo_referencia": field,
        "valor_unico_en_archivo": bool(st.session_state.get(f"{state_key}_single", True)),
    }


def _ingestion_allowed_modes(estrategia: str) -> list[str]:
    return ["sin_regla", "evitar_duplicados", "reemplazar_por_campo"]


def _render_ingestion_rule_form(columns: list, state_key: str, estrategia: str) -> None:
    column_names = [str(c.get("nombre", "")).strip() for c in columns if str(c.get("nombre", "")).strip()]
    allowed_modes = _ingestion_allowed_modes(estrategia)
    current_mode = str(st.session_state.get(f"{state_key}_mode") or "sin_regla")
    if current_mode not in allowed_modes:
        st.session_state[f"{state_key}_mode"] = "sin_regla"
        current_mode = "sin_regla"

    mode = st.selectbox(
        "Accion antes de cargar",
        allowed_modes,
        key=f"{state_key}_mode",
        format_func=lambda x: _INGESTION_MODES.get(x, x),
        help="Define que debe hacer el sistema antes de insertar los datos en la tabla destino.",
    )
    st.caption(_INGESTION_MODE_HELP.get(mode, ""))

    if mode == "sin_regla":
        st.info(
            "Sin control especial, el sistema usara la estrategia normal del catalogo: "
            "append agrega filas y overwrite reemplaza toda la tabla."
        )
        return

    if not column_names:
        st.warning("No hay columnas disponibles para usar como campo referencial.")
        return

    current_field = str(st.session_state.get(f"{state_key}_field") or "")
    field_index = column_names.index(current_field) if current_field in column_names else 0
    st.selectbox(
        "Campo de control",
        column_names,
        index=field_index,
        key=f"{state_key}_field",
        help="Campo que identifica el bloque de datos. Ejemplos: fecha_proceso, periodo, fecha_corte, codigo_lote o version.",
    )
    st.checkbox(
        "El archivo debe traer un solo valor para este campo",
        value=bool(st.session_state.get(f"{state_key}_single", True)),
        key=f"{state_key}_single",
        help="Recomendado cuando el archivo corresponde a una sola fecha, periodo, lote o corte.",
    )


@st.experimental_dialog("Reglas de calidad", width="large")
def _render_quality_rules_dialog(columns: list, state_key: str, open_key: str) -> None:
    st.caption(
        "Configura reglas por columna. Estas reglas se validan en memoria antes de cargar datos."
    )
    _render_rules_editor(columns, state_key)
    if st.button("Cerrar", use_container_width=True, key=f"{open_key}_close"):
        st.session_state[open_key] = False
        st.rerun()


@st.experimental_dialog("Accion de ingesta", width="large")
def _render_ingestion_rule_dialog(columns: list, state_key: str, estrategia: str, open_key: str) -> None:
    st.caption(
        "Define que debe pasar si ya existen datos para el campo de control seleccionado."
    )
    _render_ingestion_rule_form(columns, state_key, estrategia)
    if st.button("Cerrar", use_container_width=True, key=f"{open_key}_close"):
        st.session_state[open_key] = False
        st.rerun()


def _render_schema_rule_actions(
    columns: list,
    rules_key: str,
    ingestion_key: str,
    estrategia: str,
    key_prefix: str,
) -> None:
    rules = st.session_state.get(rules_key, {})
    total_rules = sum(len(v) for v in rules.values())
    ingestion_rule = _collect_ingestion_rule(ingestion_key)
    ingestion_label = (
        _INGESTION_MODES.get(ingestion_rule.get("modo"), "Regla activa")
        if ingestion_rule else "Sin regla de ingesta"
    )

    a1, a2 = st.columns(2)
    quality_open_key = f"{key_prefix}_quality_dialog_open"
    ingestion_open_key = f"{key_prefix}_ingestion_dialog_open"
    active_dialog_key = f"{key_prefix}_schema_rule_dialog"
    with a1:
        if st.button(
            f"Reglas de calidad ({total_rules})",
            key=f"{key_prefix}_open_quality_rules",
            use_container_width=True,
        ):
            st.session_state[active_dialog_key] = "quality"
            st.session_state[quality_open_key] = True
            st.session_state[ingestion_open_key] = False
    with a2:
        if st.button(
            f"Accion de ingesta: {ingestion_label}",
            key=f"{key_prefix}_open_ingestion_rule",
            use_container_width=True,
        ):
            st.session_state[active_dialog_key] = "ingestion"
            st.session_state[quality_open_key] = False
            st.session_state[ingestion_open_key] = True

    active_dialog = st.session_state.get(active_dialog_key)
    if active_dialog == "quality" and st.session_state.get(quality_open_key):
        _render_quality_rules_dialog(columns, rules_key, quality_open_key)
    elif active_dialog == "ingestion" and st.session_state.get(ingestion_open_key):
        _render_ingestion_rule_dialog(columns, ingestion_key, estrategia, ingestion_open_key)


def _render_rules_editor(columns: list, state_key: str) -> None:
    """Editor visual de reglas de calidad por columna. state_key → {col_name: [reglas]}."""
    if not columns:
        return
    col_defs = []
    for col in columns:
        if isinstance(col, dict):
            name = str(col.get("nombre", "")).strip()
            tipo = str(col.get("tipo", "str") or "str").strip().lower()
        else:
            name = str(col).strip()
            tipo = "str"
        if name:
            col_defs.append({"nombre": name, "tipo": tipo if tipo in _TIPOS else "str"})
    if not col_defs:
        return
    if state_key not in st.session_state:
        st.session_state[state_key] = {}
    rules: dict = st.session_state[state_key]

    # ── Encabezado ──────────────────────────────────────────────
    total_rules = sum(len(v) for v in rules.values())
    badge = (
        f'<span style="background:#534AB7;color:white;font-size:10px;font-weight:700;'
        f'padding:1px 8px;border-radius:20px;margin-left:6px;">{total_rules}</span>'
        if total_rules else ""
    )
    st.markdown(
        f'<div style="font-size:13px;font-weight:700;color:#1C2F6E;'
        f'padding:10px 0 6px;border-top:2px solid #E5E9F5;margin-top:4px;">'
        f'Reglas de calidad{badge}</div>',
        unsafe_allow_html=True,
    )

    sel_col = st.selectbox(
        "Columna",
        [col["nombre"] for col in col_defs],
        key=f"{state_key}_sel",
        label_visibility="visible",
    )
    sel_type = next((col["tipo"] for col in col_defs if col["nombre"] == sel_col), "str")
    regla_options = _REGLAS_POR_TIPO.get(sel_type, _REGLA_TIPOS)
    tipo_key = f"{state_key}_new_tipo"
    if st.session_state.get(tipo_key) not in regla_options:
        st.session_state.pop(tipo_key, None)
    col_rules = list(rules.get(sel_col, []))

    # ── Reglas activas ───────────────────────────────────────────
    n_col = len(col_rules)
    st.markdown(
        f'<div style="font-size:11px;font-weight:600;color:#6B7280;'
        f'text-transform:uppercase;letter-spacing:0.5px;margin:8px 0 6px;">'
        f'Reglas activas ({n_col})</div>',
        unsafe_allow_html=True,
    )
    if col_rules:
        scroll_h = min(220, 60 + len(col_rules) * 52)
        with st.container(height=scroll_h, border=False):
            for i, regla in enumerate(col_rules):
                c1, c2 = st.columns([8, 1])
                with c1:
                    st.markdown(_rule_chip_html(regla), unsafe_allow_html=True)
                with c2:
                    if st.button("×", key=f"{state_key}_del_{sel_col}_{i}",
                                 help="Eliminar", use_container_width=True):
                        new_rules = dict(rules)
                        new_rules[sel_col] = [r for j, r in enumerate(col_rules) if j != i]
                        st.session_state[state_key] = new_rules
                        st.rerun()
    else:
        st.markdown(
            '<div style="color:#9CA3AF;font-size:12px;font-style:italic;'
            'padding:6px 0 8px;">Sin reglas para esta columna.</div>',
            unsafe_allow_html=True,
        )

    if not regla_options:
        st.info(f"El tipo `{sel_type}` no admite reglas adicionales.")
        return

    # ── Agregar regla ────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:11px;font-weight:600;color:#6B7280;'
        'text-transform:uppercase;letter-spacing:0.5px;'
        'border-top:1px solid #E5E9F5;margin-top:10px;padding-top:10px;margin-bottom:6px;">'
        'Agregar regla</div>',
        unsafe_allow_html=True,
    )
    tipo_sel = st.selectbox(
        "Tipo de regla",
        regla_options,
        key=tipo_key,
        format_func=lambda x: _REGLA_LABELS.get(x, x),
        label_visibility="collapsed",
    )

    nueva_regla = None
    if tipo_sel == "isin":
        val_str = st.text_input(
            "Valores permitidos (separados por coma)",
            key=f"{state_key}_new_isin",
            placeholder="Ej: C, D, N",
        )
        if st.button("+ Agregar", key=f"{state_key}_add_btn", type="primary", use_container_width=True):
            valores = [v.strip() for v in val_str.split(",") if v.strip()]
            if valores:
                nueva_regla = {"tipo": "isin", "valor": valores}

    elif tipo_sel in ("gte", "lte"):
        label = "Valor mínimo (≥)" if tipo_sel == "gte" else "Valor máximo (≤)"
        num_val = st.number_input(label, key=f"{state_key}_new_num", value=0.0)
        if st.button("+ Agregar", key=f"{state_key}_add_btn", type="primary", use_container_width=True):
            nueva_regla = {"tipo": tipo_sel, "valor": float(num_val)}

    elif tipo_sel == "min_length":
        min_val = st.number_input(
            "Caracteres mínimos",
            key=f"{state_key}_new_minlen",
            value=1, min_value=1, step=1,
        )
        if st.button("+ Agregar", key=f"{state_key}_add_btn", type="primary", use_container_width=True):
            nueva_regla = {"tipo": "min_length", "valor": int(min_val)}

    elif tipo_sel == "str_length":
        sl_min = st.number_input("Longitud mínima", key=f"{state_key}_new_slmin", value=1, min_value=0, step=1)
        sl_max = st.number_input("Longitud máxima", key=f"{state_key}_new_slmax", value=50, min_value=1, step=1)
        if st.button("+ Agregar", key=f"{state_key}_add_btn", type="primary", use_container_width=True):
            nueva_regla = {"tipo": "str_length", "min": int(sl_min), "max": int(sl_max)}

    elif tipo_sel == "regex":
        pattern = st.text_input(
            "Patrón regex",
            key=f"{state_key}_new_regex",
            placeholder=r"^[A-Z0-9_-]+$",
        )
        if st.button("+ Agregar", key=f"{state_key}_add_btn", type="primary", use_container_width=True):
            pattern = pattern.strip()
            if pattern:
                nueva_regla = {"tipo": "regex", "valor": pattern}

    if nueva_regla is not None:
        new_rules = dict(rules)
        col_list = list(new_rules.get(sel_col, []))
        col_list.append(nueva_regla)
        new_rules[sel_col] = col_list
        st.session_state[state_key] = new_rules
        st.rerun()


def _render_schema_editor(schema: dict, key_prefix: str, show_rule_actions: bool = True) -> None:
    columnas = schema.get("columnas", [])
    schema_cache_key = st.session_state.get("adm_schema_key", "default")
    state_key  = f"{key_prefix}_schema_rows_{hash(schema_cache_key)}"
    result_key = f"{key_prefix}_schema_edited_{hash(schema_cache_key)}"
    rules_key  = f"{key_prefix}_rules_{hash(schema_cache_key)}"
    ingestion_key = f"{key_prefix}_ingestion_{hash(schema_cache_key)}"

    # state_key se escribe UNA sola vez (fuente inmutable del data_editor)
    if state_key not in st.session_state:
        st.session_state[state_key] = pd.DataFrame(
            [
                {
                    "nombre":   col["nombre"],
                    "tipo":     col["tipo"] if col["tipo"] in _TIPOS else "str",
                    "nullable": bool(col.get("nullable", True)),
                }
                for col in columnas
            ],
            columns=["nombre", "tipo", "nullable"],
        )

    # Inicializar reglas existentes del schema (solo una vez)
    if rules_key not in st.session_state:
        st.session_state[rules_key] = {
            col["nombre"]: list(col.get("reglas", []))
            for col in columnas
            if col.get("reglas")
        }
    _init_ingestion_state(ingestion_key, schema.get("regla_ingesta"))

    df = st.session_state[state_key]
    display_df = df.copy()
    height = 38 + len(display_df) * 35 + 2

    # Stats bar
    if not df.empty:
        from collections import Counter
        tipo_counts   = Counter(df["tipo"].tolist())
        nullable_n    = int(df["nullable"].sum())
        tipo_badges   = "".join(
            f'<span style="background:#EDE9FE;color:#534AB7;font-size:10px;font-weight:600;'
            f'padding:1px 7px;border-radius:20px;margin-right:4px;">{t} ×{n}</span>'
            for t, n in sorted(tipo_counts.items())
        )
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;'
            f'padding:6px 0 8px;">'
            f'<span style="font-size:12px;color:#6B7280;font-weight:500;">{len(df)} columnas</span>'
            f'<span style="color:#D1D9F0;">|</span>'
            f'{tipo_badges}'
            f'<span style="background:#D1FAE5;color:#065F46;font-size:10px;font-weight:600;'
        f'padding:1px 7px;border-radius:20px;">{nullable_n} aceptan vacíos</span>'
            f'<span style="background:#FEE2E2;color:#991B1B;font-size:10px;font-weight:600;'
            f'padding:1px 7px;border-radius:20px;">{len(df)-nullable_n} requerido</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # result_key ≠ state_key → no alimenta de vuelta al editor, evita el loop
    edited = st.data_editor(
        display_df,
        column_config={
            "nombre":   st.column_config.TextColumn("Columna y Regla", width="medium"),
            "tipo":     st.column_config.SelectboxColumn("Tipo", options=_TIPOS, required=True, width="small"),
            "nullable": st.column_config.CheckboxColumn("Acepta vacíos", width="small"),
        },
        disabled=["nombre"],
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        height=height,
        key=f"de_{state_key}",
    )
    st.session_state[result_key] = edited

    rule_columns = [
        {
            "nombre": str(row.get("nombre", "")).strip(),
            "tipo": str(row.get("tipo", "str") or "str").strip().lower(),
        }
        for _, row in edited.iterrows()
        if str(row.get("nombre", "")).strip()
    ]
    if show_rule_actions:
        current_strategy = st.session_state.get(f"{key_prefix}_est", "overwrite")
        _render_schema_rule_actions(
            columns=rule_columns,
            rules_key=rules_key,
            ingestion_key=ingestion_key,
            estrategia=current_strategy,
            key_prefix=f"{key_prefix}_{abs(hash(schema_cache_key))}",
        )


def _bulk_table_prefix(table: str) -> str:
    return f"bulk_{re.sub(r'[^a-z0-9_]', '_', table.lower())}"


def _get_bulk_table_config(db: str, table: str) -> Dict[str, str]:
    configs = st.session_state.get("adm_bulk_table_configs", {})
    return configs.get(table, _default_bulk_table_config(db, table))

def _load_schema_into_state(db: str, table: str) -> dict | None:
    schema_key = f"{db}.{table}"
    if st.session_state.get("adm_schema_key") != schema_key:
        ph = st.empty()
        ph.markdown(_skeleton_html(6), unsafe_allow_html=True)
        try:
            fuente = st.session_state.get("adm_step2_fuente") or st.session_state.get("adm_fuente", "SingleStore")
            if fuente == "Hive":
                rows = describe_hive_table(db, table)
            else:
                rows = describe_table(db, table)
            st.session_state.adm_schema = build_schema_json(rows)
            st.session_state.adm_schema_key = schema_key
            ph.empty()
        except Exception as e:
            ph.empty()
            st.error(user_facing_error(e, context="database"))
            return None
    return st.session_state.get("adm_schema", {})


def _render_active_table_editor(db: str, table: str, mapped: Set[tuple]) -> None:
    already = (db, table) in mapped
    badge_bg = "#DCFCE7" if already else "#EDE9FE"
    badge_fg = "#16A34A" if already else "#534AB7"
    badge_text = "Ya registrada" if already else "Lista para registrar"

    st.markdown(f"""
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
        <span style="font-size:14px; font-weight:700;">
            <code style="color:#6B7280;">{db}.</code><code style="color:{badge_fg};">{table}</code>
        </span>
        <span style="background:{badge_bg}; color:{badge_fg}; font-size:11px; font-weight:600;
                     padding:2px 10px; border-radius:20px; border:1px solid {badge_fg}40;">
            {badge_text}
        </span>
    </div>
    """, unsafe_allow_html=True)

    schema = _load_schema_into_state(db, table)
    if schema is None:
        return

    st.caption(f"{len(schema.get('columnas', []))} columna(s)")
    if already:
        _render_schema_readonly(schema)
    else:
        with st.expander("Esquema — edita tipos y nulabilidad", expanded=True):
            _render_schema_editor(schema, key_prefix="bulk")


def _collect_schema(schema: dict, key_prefix: str) -> dict:
    schema_cache_key = st.session_state.get("adm_schema_key", "default")
    state_key  = f"{key_prefix}_schema_rows_{hash(schema_cache_key)}"
    result_key = f"{key_prefix}_schema_edited_{hash(schema_cache_key)}"
    rules_key  = f"{key_prefix}_rules_{hash(schema_cache_key)}"
    ingestion_key = f"{key_prefix}_ingestion_{hash(schema_cache_key)}"

    data = st.session_state.get(result_key)
    if data is None:
        data = st.session_state.get(state_key)
    if data is None:
        return schema

    rules_por_columna = st.session_state.get(rules_key, {})
    rows = data.to_dict("records") if isinstance(data, pd.DataFrame) else data
    collected = {
        "columnas": [
            {
                "nombre":   str(r.get("nombre", "")).strip(),
                "tipo":     str(r.get("tipo", "str")),
                "nullable": bool(r.get("nullable", True)),
                "reglas":   rules_por_columna.get(str(r.get("nombre", "")).strip(), []),
            }
            for r in rows
            if str(r.get("nombre", "")).strip()
        ]
    }
    ingestion_rule = _collect_ingestion_rule(ingestion_key)
    if ingestion_rule:
        collected["regla_ingesta"] = ingestion_rule
    return collected


def _render_permisos_selector(key_prefix: str, current: List[Dict] | None = None) -> None:
    current    = current or []
    usuarios_lookup = _load_usuarios_lookup()
    init_users = [
        p["valor"] for p in current
        if p["tipo"] == "usuario" and _is_publicador_activo(p["valor"], usuarios_lookup)
    ]
    publicador_default = bool(init_users)

    st.markdown("**Permisos de acceso**")
    st.caption(
        "Los Admin siempre tienen acceso. "
        "Activa Publicador para seleccionar los publicadores puntuales que veran el catalogo."
    )

    c1, c2 = st.columns(2)
    with c1:
        publicador_enabled = st.toggle(
            "Permitir publicadores",
            value=publicador_default,
            key=f"{key_prefix}_role_display",
            help="Si esta activo, debes seleccionar que publicadores tendran acceso.",
        )
    with c2:
        users = [
            username
            for username in usuarios_lookup
            if _is_publicador_activo(username, usuarios_lookup)
        ]
        users = list(dict.fromkeys(users + init_users))
        if publicador_enabled:
            st.multiselect(
                "Publicadores autorizados",
                users,
                default=init_users,
                key=f"{key_prefix}_users",
                format_func=lambda username: _usuario_label(username, usuarios_lookup),
            )

            st.caption("Si el publicador aun no ingreso al portal, agregalo por usuario BA.")
            add_col, btn_col = st.columns([3, 1])
            with add_col:
                new_ba = st.text_input(
                    "Agregar BA como publicador",
                    placeholder="ba01006646",
                    key=f"{key_prefix}_new_publicador_ba",
                    label_visibility="collapsed",
                )
            with btn_col:
                add_clicked = st.button(
                    "Agregar BA",
                    key=f"{key_prefix}_add_publicador_ba",
                    use_container_width=True,
                )

            if add_clicked:
                username_norm = _normalize_ba_username(new_ba)
                validation_error = _validate_ba_publicador_username(username_norm, usuarios_lookup)
                if validation_error:
                    st.error(validation_error)
                else:
                    try:
                        actor = st.session_state.get("user_info", {}).get("username", "")
                        create_or_promote_user(
                            username=username_norm,
                            rol="Publicador",
                            actor_username=actor,
                        )
                        selected_users = list(st.session_state.get(f"{key_prefix}_users", []))
                        if username_norm not in selected_users:
                            selected_users.append(username_norm)
                        st.session_state[f"{key_prefix}_users"] = selected_users
                        st.session_state[f"{key_prefix}_role_display"] = True
                        st.session_state.pop(f"{key_prefix}_new_publicador_ba", None)
                        st.success(f"Usuario `{username_norm}` agregado como Publicador autorizado.")
                        st.rerun()
                    except Exception as e:
                        st.error(user_facing_error(e, context="database"))
        else:
            st.caption("Sin publicadores autorizados. Solo Admins veran el catalogo.")
            st.session_state[f"{key_prefix}_users"] = []


def _collect_permisos(key_prefix: str) -> List[Dict]:
    publicador_enabled = bool(st.session_state.get(f"{key_prefix}_role_display", False))
    if not publicador_enabled:
        return []
    usuarios_lookup = _load_usuarios_lookup()
    users = [
        u for u in st.session_state.get(f"{key_prefix}_users", [])
        if _is_publicador_activo(u, usuarios_lookup)
    ]
    return [{"tipo": "usuario", "valor": u} for u in users]


def _render_registration_review(
    project_name: str,
    catalog_id: str,
    nombre: str,
    db_sel: str,
    tbl_sel: str,
    estrategia: str,
    destino: str,
    schema: dict,
    permisos: List[Dict],
    bulk_mode: bool = False,
) -> None:
    columns = schema.get("columnas", []) if isinstance(schema, dict) else []
    total_rules = sum(len(col.get("reglas", []) or []) for col in columns)
    nullable_n = sum(1 for col in columns if col.get("nullable"))
    ingestion_rule = schema.get("regla_ingesta") if isinstance(schema, dict) else None
    ingestion_label = (
        _INGESTION_MODES.get(ingestion_rule.get("modo"), "Regla activa")
        if ingestion_rule else "Sin control especial"
    )
    access_label = (
        "Todos los publicadores"
        if not permisos else
        ", ".join(p["valor"] for p in permisos if p.get("tipo") == "usuario") or "Permisos configurados"
    )
    project_text = project_name or ("Se toma del registro del lote" if bulk_mode else "Pendiente")

    st.markdown(
        f"""
        <div style="border:1px solid #C7D2FE;background:#F8FAFF;border-radius:10px;padding:14px 16px;margin-bottom:12px;">
            <div style="font-size:14px;font-weight:800;color:#1C2F6E;margin-bottom:10px;">Revisión antes de guardar</div>
            <div style="display:grid;grid-template-columns:repeat(2,minmax(220px,1fr));gap:10px 18px;">
                <div><span class="review-label">Proyecto</span><div class="review-value">{html_escape(project_text)}</div></div>
                <div><span class="review-label">Catálogo</span><div class="review-value">{html_escape(nombre or "Pendiente")}</div></div>
                <div><span class="review-label">ID catálogo</span><div class="review-value"><code>{html_escape(catalog_id or "pendiente")}</code></div></div>
                <div><span class="review-label">Tabla destino</span><div class="review-value"><code>{html_escape(db_sel)}.{html_escape(tbl_sel)}</code></div></div>
                <div><span class="review-label">Destino</span><div class="review-value">{html_escape(str(destino).upper())}</div></div>
                <div><span class="review-label">Estrategia</span><div class="review-value">{html_escape(str(estrategia).upper())}</div></div>
                <div><span class="review-label">Columnas</span><div class="review-value">{len(columns)} total · {nullable_n} aceptan vacíos</div></div>
                <div><span class="review-label">Reglas</span><div class="review-value">{total_rules} regla(s) de calidad</div></div>
                <div><span class="review-label">Acceso</span><div class="review-value">{html_escape(access_label)}</div></div>
                <div><span class="review-label">Ingesta</span><div class="review-value">{html_escape(ingestion_label)}</div></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_bulk_registration_review(
    db: str,
    tables: List[str],
    project_name: str,
    estrategia: str,
    destino: str,
    permisos: List[Dict],
    configs_saved: Dict[str, Dict],
) -> None:
    saved_count = sum(1 for table in tables if table in configs_saved)
    total_columns = 0
    total_rules = 0
    for table in tables:
        schema = (configs_saved.get(table) or {}).get("schema") or {}
        cols = schema.get("columnas", []) if isinstance(schema, dict) else []
        total_columns += len(cols)
        total_rules += sum(len(col.get("reglas", []) or []) for col in cols)

    access_label = (
        "Todos los publicadores"
        if not permisos else
        ", ".join(p["valor"] for p in permisos if p.get("tipo") == "usuario") or "Permisos configurados"
    )
    table_list = "".join(
        f"<li><code>{html_escape(table)}</code>"
        f"<span style='color:{'#16A34A' if table in configs_saved else '#B45309'};font-weight:700;margin-left:6px;'>"
        f"{'configurada' if table in configs_saved else 'pendiente'}</span></li>"
        for table in tables
    )
    st.markdown(
        f"""
        <div style="border:1px solid #C7D2FE;background:#F8FAFF;border-radius:10px;padding:14px 16px;margin-bottom:12px;">
            <div style="font-size:14px;font-weight:800;color:#1C2F6E;margin-bottom:10px;">Revisión del lote</div>
            <div style="display:grid;grid-template-columns:repeat(2,minmax(220px,1fr));gap:10px 18px;margin-bottom:10px;">
                <div><span class="review-label">Proyecto</span><div class="review-value">{html_escape(project_name or "Pendiente")}</div></div>
                <div><span class="review-label">Base de datos</span><div class="review-value"><code>{html_escape(db)}</code></div></div>
                <div><span class="review-label">Tablas</span><div class="review-value">{len(tables)} total · {saved_count} configurada(s)</div></div>
                <div><span class="review-label">Columnas / reglas</span><div class="review-value">{total_columns} columnas · {total_rules} regla(s)</div></div>
                <div><span class="review-label">Destino</span><div class="review-value">{html_escape(str(destino).upper())}</div></div>
                <div><span class="review-label">Estrategia</span><div class="review-value">{html_escape(str(estrategia).upper())}</div></div>
                <div style="grid-column:1 / -1;"><span class="review-label">Acceso</span><div class="review-value">{html_escape(access_label)}</div></div>
            </div>
            <ol style="margin:8px 0 0;padding-left:18px;font-size:12px;color:#1C2F6E;">{table_list}</ol>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_register_flow_summary(
    db: str,
    selected: List[str],
    mapped: Set[tuple],
    configs_saved: Dict[str, Dict],
) -> None:
    to_register = [table for table in selected if (db, table) not in mapped]
    already_registered = [table for table in selected if (db, table) in mapped]
    configured = [table for table in to_register if table in configs_saved]
    pending = [table for table in to_register if table not in configs_saved]

    st.markdown(
        f"""
        <div class="adm-guided-panel">
            <div class="adm-guided-title">Revisión del registro</div>
            <div class="adm-guided-copy">
                Antes de registrar, confirma que las tablas, permisos y reglas estén completos.
            </div>
            <div class="adm-mini-metrics">
                <span><b>{len(selected)}</b> seleccionada(s)</span>
                <span><b>{len(configured)}</b> configurada(s)</span>
                <span><b>{len(pending)}</b> pendiente(s)</span>
                <span><b>{len(already_registered)}</b> ya registrada(s)</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    rows = []
    for table in selected:
        if (db, table) in mapped:
            status = "Ya registrada"
            cls = "done"
        elif table in configs_saved:
            status = "Configurada"
            cls = "done"
        else:
            status = "Pendiente"
            cls = "pending"
        rows.append(
            f'<div class="adm-table-status {cls}">'
            f'<code>{html_escape(table)}</code><span>{status}</span></div>'
        )
    st.markdown(
        '<div class="adm-table-status-list">' + "".join(rows) + "</div>",
        unsafe_allow_html=True,
    )

    if pending:
        st.warning(
            "Aún hay tablas pendientes de configurar. Entra a **Configuración**, "
            "elige cada tabla pendiente y guarda su configuración."
        )
    else:
        st.success("Las tablas pendientes ya tienen configuración lista.")

    st.caption(
        "El botón final de registro aparece en la revisión de la configuración. "
        "Para varias tablas, aparece dentro del bloque de registro del lote."
    )


def _render_registro_form(
    db_sel: str,
    tbl_sel: str,
    schema: dict,
    key_prefix: str,
    submit_label: str = "Registrar catálogo",
    bulk_mode: bool = False,
) -> None:
    st.markdown("##### Configuración de la tabla")

    cid_default, nombre_default = _catalog_defaults(db_sel, tbl_sel)

    if not bulk_mode:
        try:
            projects = get_all_projects()
        except Exception as e:
            st.error(user_facing_error(e, context="database"))
            return
    else:
        projects = []

    tabs = (
        st.tabs(["Proyecto", "Datos", "Columnas", "Permisos", "Revisión"])
        if not bulk_mode else
        st.tabs(["Datos", "Columnas", "Revisión"])
    )

    if not bulk_mode:
        tab_project, tab_catalog, tab_columns, tab_permissions, tab_review = tabs
        with tab_project:
            proj_sel, proj_name, create_project = _render_project_inputs(db_sel, key_prefix, projects, tbl_sel)
    else:
        tab_catalog, tab_columns, tab_review = tabs
        proj_sel = proj_name = ""
        create_project = False

    with tab_catalog:
        catalog_id = st.text_input(
            "ID del catálogo",
            value=st.session_state.get(f"{key_prefix}_cid", cid_default),
            key=f"{key_prefix}_cid",
            help="Se genera automáticamente a partir del proyecto y la tabla.",
            disabled=True,
        )
        nombre = st.text_input(
            "Nombre legible",
            value=st.session_state.get(f"{key_prefix}_nombre", nombre_default),
            key=f"{key_prefix}_nombre",
        )
        descripcion = st.text_area(
            "Descripción (opcional)",
            value=st.session_state.get(f"{key_prefix}_desc", ""),
            key=f"{key_prefix}_desc",
            height=74,
        )

        if not bulk_mode:
            c1, c2 = st.columns(2)
            with c1:
                estrategia = st.selectbox("Estrategia", _ESTRATEGIAS, key=f"{key_prefix}_est")
            with c2:
                _dest_idx = 1 if HIVE_ENABLED and st.session_state.get("adm_step2_fuente") == "Hive" else 0
                destino = st.selectbox("Destino", _DESTINOS, index=_dest_idx, key=f"{key_prefix}_dest")
        else:
            estrategia = st.session_state.get("adm_bk_est", "overwrite")
            destino = st.session_state.get("adm_bk_dest", "singlestore")

    schema_cache_key = st.session_state.get("adm_schema_key", "default")
    rules_key = f"{key_prefix}_rules_{hash(schema_cache_key)}"
    ingestion_key = f"{key_prefix}_ingestion_{hash(schema_cache_key)}"

    with tab_columns:
        st.markdown("**Columnas del esquema**")
        st.caption("Edita nombre, tipo y si cada columna acepta vacíos. Usa la última fila vacía para agregar columnas.")
        _render_schema_editor(schema, key_prefix=key_prefix, show_rule_actions=False)
        current_schema = _collect_schema(schema, key_prefix)
        rule_columns = [
            {
                "nombre": str(col.get("nombre", "")).strip(),
                "tipo": str(col.get("tipo", "str") or "str").strip().lower(),
            }
            for col in current_schema.get("columnas", [])
            if str(col.get("nombre", "")).strip()
        ]
        st.markdown("---")
        _rcal, _ring = st.columns(2)
        with _rcal:
            if st.button("Regla de calidad", use_container_width=True, key=f"btn_calidad_{key_prefix}"):
                _dialog_reglas_calidad(rule_columns, rules_key)
        with _ring:
            if st.button("Reglas de ingesta", use_container_width=True, key=f"btn_ingesta_{key_prefix}"):
                _dialog_reglas_ingesta(rule_columns, ingestion_key, estrategia)

    current_schema = _collect_schema(schema, key_prefix)
    rule_columns = [
        {
            "nombre": str(col.get("nombre", "")).strip(),
            "tipo": str(col.get("tipo", "str") or "str").strip().lower(),
        }
        for col in current_schema.get("columnas", [])
        if str(col.get("nombre", "")).strip()
    ]

    if not bulk_mode:
        with tab_permissions:
            _render_permisos_selector(key_prefix)

    cid = catalog_id.strip()
    current_schema = _collect_schema(schema, key_prefix)
    errores: List[str] = []
    if not bulk_mode:
        errores.extend(
            _validate_catalog_form(
                project_id=proj_sel,
                project_name=proj_name,
                catalog_id=cid,
                nombre=nombre.strip(),
                schema=current_schema,
            )
        )
    else:
        errores.extend(
            _validate_catalog_form(
                catalog_id=cid,
                nombre=nombre.strip(),
                schema=current_schema,
            )
        )

    with tab_review:
        _render_registration_review(
            project_name=proj_name,
            catalog_id=cid,
            nombre=nombre.strip(),
            db_sel=db_sel,
            tbl_sel=tbl_sel,
            estrategia=estrategia,
            destino=destino,
            schema=current_schema,
            permisos=[] if bulk_mode else _collect_permisos(key_prefix),
            bulk_mode=bulk_mode,
        )
        for e in errores:
            st.warning(e)
        if not bulk_mode:
            st.caption("El registro guarda la configuración en `gatekeeper_meta.catalogos_config`.")

        if st.button(
            submit_label, type="primary", use_container_width=True,
            key=f"{key_prefix}_save", disabled=bool(errores)
        ):
            if bulk_mode:
                configs = st.session_state.setdefault("adm_bulk_table_configs", {})
                configs[tbl_sel] = {
                    "catalog_id": cid,
                    "nombre": nombre.strip(),
                    "descripcion": descripcion.strip(),
                    "schema": current_schema,
                }
                st.success(f"Configuración de **{tbl_sel}** guardada en memoria.")
                return

            if catalog_exists(cid):
                st.error(f"Ya existe un catálogo con ID `{cid}`.")
                return
            _registro_ok = False
            try:
                if create_project:
                    ensure_project_exists(proj_sel, proj_name)
                save_catalog_config(
                    catalog_id    = cid,
                    project_id    = proj_sel,
                    nombre        = nombre.strip(),
                    descripcion   = descripcion.strip(),
                    base_datos    = db_sel,
                    tabla_destino = tbl_sel,
                    destino       = destino,
                    estrategia    = estrategia,
                    schema_json   = current_schema,
                )
                save_permissions(cid, _collect_permisos(key_prefix))
                st.success(f"Catálogo **{nombre.strip()}** registrado correctamente.")
                st.session_state.pop("adm_schema_key", None)
                st.session_state[f"adm_chk_{tbl_sel}"] = False
                _registro_ok = True
            except Exception as e:
                st.error(user_facing_error(e, context="database"))
            if _registro_ok:
                st.rerun()


def _render_permission_manager_inline(catalog_id: str, key_prefix: str) -> None:
    st.markdown("##### Gestionar permisos")
    try:
        current = get_catalog_permissions(catalog_id)
    except Exception:
        current = []
    _render_permisos_selector(key_prefix, current=current)
    if st.button("Actualizar permisos", key=f"{key_prefix}_upd", use_container_width=True):
        try:
            save_permissions(catalog_id, _collect_permisos(key_prefix))
            st.success("Permisos actualizados.")
        except Exception as e:
            st.error(user_facing_error(e, context="database"))


@st.experimental_dialog("Editar catálogo", width="large")
def _render_edit_catalog_dialog(cat: dict) -> None:
    cid = cat["catalog_id"]
    ek = f"ed_{cid}"

    st.markdown(
        '<div style="padding:10px 14px;background:#FFF7ED;'
        'border:1px solid #FED7AA;border-radius:8px;margin-bottom:12px;">'
        f'<span style="font-size:13px;font-weight:600;color:#92400E;">Editar — {cat["nombre"]}</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    schema_init_key = f"{ek}_schema_init"
    rules_edit_key = f"{ek}_rules_state"
    ingestion_edit_key = f"{ek}_ingestion_state"
    if schema_init_key not in st.session_state:
        try:
            raw = get_catalog_schema(cid)
            col_defs = raw.get("columnas", [])
            st.session_state[schema_init_key] = [
                {"nombre": c["nombre"], "tipo": c["tipo"], "nullable": bool(c.get("nullable", True))}
                for c in col_defs
            ]
            st.session_state[rules_edit_key] = {
                c["nombre"]: list(c.get("reglas", []))
                for c in col_defs
                if c.get("reglas")
            }
            _init_ingestion_state(ingestion_edit_key, raw.get("regla_ingesta"))
        except Exception as e:
            st.error(user_facing_error(e, context="database"))
            st.session_state[schema_init_key] = []

    init_rows = st.session_state[schema_init_key]
    init_df = pd.DataFrame(init_rows) if init_rows else pd.DataFrame(columns=["nombre", "tipo", "nullable"])
    edit_rules = st.session_state.get(rules_edit_key, {})
    display_init_df = init_df.copy()
    display_init_df["reglas"] = display_init_df["nombre"].apply(
        lambda name: len(edit_rules.get(str(name).strip(), []))
    )

    tab_general, tab_columnas, tab_reglas, tab_ingesta = st.tabs(
        ["General", "Columnas", "Reglas", "Ingesta"]
    )

    with tab_general:
        ed_nombre = st.text_input("Nombre", value=cat["nombre"], key=f"{ek}_nombre")
        ed_desc = st.text_area("Descripción", value=cat.get("descripcion") or "", key=f"{ek}_desc", height=86)
        ec1, ec2 = st.columns(2)
        with ec1:
            ed_est = st.selectbox(
                "Estrategia",
                _ESTRATEGIAS,
                index=_ESTRATEGIAS.index(cat["estrategia"]) if cat["estrategia"] in _ESTRATEGIAS else 0,
                key=f"{ek}_est",
            )
        with ec2:
            ed_dest = st.selectbox(
                "Destino",
                _DESTINOS,
                index=_DESTINOS.index(cat["destino"]) if cat["destino"] in _DESTINOS else 0,
                key=f"{ek}_dest",
            )
        st.info(
            "Usa las pestañas superiores para revisar columnas, reglas de calidad y acción de ingesta. "
            "Los cambios se aplican cuando guardas."
        )

    with tab_columnas:
        st.markdown("**Columnas del esquema**")
        st.caption("Puedes editar nombre, tipo y si la columna acepta vacíos. Usa la última fila vacía para agregar columnas.")
        edited_df = st.data_editor(
            display_init_df,
            column_config={
                "nombre": st.column_config.TextColumn("Columna", required=True),
                "tipo": st.column_config.SelectboxColumn("Tipo", options=_TIPOS, required=True),
                "nullable": st.column_config.CheckboxColumn("Acepta vacíos"),
                "reglas": st.column_config.NumberColumn(
                    "Reglas",
                    help="Cantidad de reglas de calidad configuradas. Revisa el detalle en la pestaña Reglas.",
                ),
            },
            disabled=["reglas"],
            use_container_width=True,
            num_rows="dynamic",
            hide_index=True,
            key=f"{ek}_schema_editor",
            height=min(300, 88 + max(4, len(display_init_df) + 1) * 36),
        )
        st.markdown(_rules_count_hover_html(edited_df, edit_rules), unsafe_allow_html=True)

    edit_rule_columns = [
        {
            "nombre": str(r.get("nombre", "")).strip(),
            "tipo": str(r.get("tipo", "str") or "str").strip().lower(),
        }
        for _, r in edited_df.iterrows()
        if str(r.get("nombre", "")).strip()
    ]

    with tab_reglas:
        _render_rules_editor(edit_rule_columns, rules_edit_key)

    with tab_ingesta:
        _render_ingestion_rule_form(edit_rule_columns, ingestion_edit_key, ed_est)

    edit_schema_candidate = {
        "columnas": [
            {
                "nombre": str(r.get("nombre", "")).strip(),
                "tipo": str(r.get("tipo", "str")),
                "nullable": bool(r.get("nullable", True)),
                "reglas": st.session_state.get(rules_edit_key, {}).get(str(r.get("nombre", "")).strip(), []),
            }
            for _, r in edited_df.iterrows()
            if str(r.get("nombre", "")).strip()
        ]
    }
    edit_ingestion_rule = _collect_ingestion_rule(ingestion_edit_key)
    if edit_ingestion_rule:
        edit_schema_candidate["regla_ingesta"] = edit_ingestion_rule

    edit_schema_errors = _validate_catalog_form(
        catalog_id=cid,
        nombre=ed_nombre.strip(),
        schema=edit_schema_candidate,
    )
    for err in edit_schema_errors:
        st.warning(err)

    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("Guardar cambios", type="primary", key=f"{ek}_save", use_container_width=True, disabled=bool(edit_schema_errors)):
            edit_rules = st.session_state.get(rules_edit_key, {})
            new_schema = {
                "columnas": [
                    {
                        "nombre": str(r.get("nombre", "")).strip(),
                        "tipo": str(r.get("tipo", "str")),
                        "nullable": bool(r.get("nullable", True)),
                        "reglas": edit_rules.get(str(r.get("nombre", "")).strip(), []),
                    }
                    for _, r in edited_df.iterrows()
                    if str(r.get("nombre", "")).strip()
                ]
            }
            edit_ingestion_rule = _collect_ingestion_rule(ingestion_edit_key)
            if edit_ingestion_rule:
                new_schema["regla_ingesta"] = edit_ingestion_rule
            try:
                update_catalog_config(cid, ed_nombre.strip(), ed_desc.strip(), ed_est, ed_dest, new_schema)
            except Exception as e:
                st.error(user_facing_error(e, context="database"))
                return

            st.session_state.pop(schema_init_key, None)
            st.session_state.pop(rules_edit_key, None)
            st.session_state.pop(ingestion_edit_key, None)
            st.session_state.pop(f"{ingestion_edit_key}_initialized", None)
            st.session_state.pop("adm_catalog_dialog", None)
            selected_catalog = st.session_state.get("selected_catalog")
            if isinstance(selected_catalog, dict) and selected_catalog.get("catalog_id") == cid:
                st.session_state.pop("selected_catalog", None)
                st.session_state.pop("selected_project_id", None)
                st.session_state.pop("selected_project_name", None)
                st.session_state.current_step = "upload"
            st.rerun()
    with bc2:
        if st.button("Cancelar", key=f"{ek}_cancel", use_container_width=True):
            st.session_state.pop(schema_init_key, None)
            st.session_state.pop(rules_edit_key, None)
            st.session_state.pop(ingestion_edit_key, None)
            st.session_state.pop(f"{ingestion_edit_key}_initialized", None)
            st.session_state.pop("adm_catalog_dialog", None)
            st.rerun()


@st.experimental_dialog("Permisos del catálogo", width="large")
def _render_catalog_permissions_dialog(cat: dict) -> None:
    cid = cat["catalog_id"]
    st.markdown(
        '<div style="padding:10px 14px;background:#F0F4FF;'
        'border:1px solid #C7D2FE;border-radius:8px;margin-bottom:8px;">'
        f'<span style="font-size:13px;font-weight:600;color:#1C2F6E;">Permisos — {cat["nombre"]}</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    try:
        permisos = get_catalog_permissions(cid)
    except Exception as e:
        st.error(user_facing_error(e, context="database"))
        permisos = []

    pk = f"ac_{cid}"
    _render_permisos_selector(pk, current=permisos)
    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("Guardar", type="primary", key=f"save_perm_{cid}", use_container_width=True):
            try:
                save_permissions(cid, _collect_permisos(pk))
            except Exception as e:
                st.error(user_facing_error(e, context="database"))
                return
            st.session_state.pop("adm_catalog_dialog", None)
            st.rerun()
    with bc2:
        if st.button("Cancelar", key=f"cancel_perm_{cid}", use_container_width=True):
            st.session_state.pop("adm_catalog_dialog", None)
            st.rerun()


@st.experimental_dialog("Desactivar catálogo", width="small")
def _render_deactivate_catalog_dialog(cat: dict) -> None:
    cid = cat["catalog_id"]
    st.warning(f"¿Desactivar **{cat['nombre']}**? Los publicadores perderán acceso inmediatamente.")
    dc1, dc2 = st.columns(2)
    with dc1:
        if st.button("Confirmar", type="primary", key=f"yes_deact_{cid}", use_container_width=True):
            try:
                deactivate_catalog(cid)
            except Exception as e:
                st.error(user_facing_error(e, context="database"))
                return
            st.session_state.pop("adm_catalog_dialog", None)
            st.rerun()
    with dc2:
        if st.button("Cancelar", key=f"no_deact_{cid}", use_container_width=True):
            st.session_state.pop("adm_catalog_dialog", None)
            st.rerun()


# ------------------------------------------------------------------
# Tab 2: Catálogos activos
# ------------------------------------------------------------------
def _tab_activos() -> None:
    st.markdown("##### Respaldo y restauración")
    ex1, ex2 = st.columns([1, 2])
    with ex1:
        if st.button("Preparar backup JSON", use_container_width=True, key="adm_export_bundle"):
            try:
                st.session_state["adm_export_bundle_payload"] = export_catalogs_bundle()
                st.success("Backup preparado.")
            except Exception as e:
                st.error(user_facing_error(e, context="database"))
        payload = st.session_state.get("adm_export_bundle_payload")
        if payload:
            st.download_button(
                "Descargar backup",
                data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                file_name="catalogos_config_backup.json",
                mime="application/json",
                use_container_width=True,
            )
    with ex2:
        uploaded_bundle = st.file_uploader(
            "Restaurar configuración desde backup JSON",
            type=["json"],
            key="adm_import_bundle",
            label_visibility="collapsed",
            help="Importa proyectos, catálogos y permisos desde un backup exportado por el sistema.",
        )
        overwrite_existing = st.checkbox(
            "Sobrescribir catálogos existentes",
            key="adm_import_overwrite",
            help="Si está activo, actualiza configuración y permisos de catálogos ya registrados.",
        )
        if uploaded_bundle is not None and st.button("Importar backup", use_container_width=True, key="adm_import_bundle_btn"):
            _import_ok = False
            try:
                bundle = json.loads(uploaded_bundle.getvalue().decode("utf-8"))
                result = import_catalogs_bundle(bundle, overwrite_existing=overwrite_existing)
                st.success(
                    "Importación completada. "
                    f"Creados: {result['created']} · Actualizados: {result['updated']} · Omitidos: {result['skipped']}"
                )
                _import_ok = True
            except Exception as e:
                st.error("No se pudo importar el backup. Verifica que sea un JSON válido exportado por Data Gatekeeper.")
            if _import_ok:
                st.rerun()

    st.divider()
    search = st.text_input(
        "Buscar", placeholder="Nombre, base de datos o tabla...",
        key="adm_ac_search", label_visibility="collapsed"
    )

    ph = st.empty()
    ph.markdown(_skeleton_html(4, card=True), unsafe_allow_html=True)
    try:
        catalogs = get_active_catalogs()
        ph.empty()
    except Exception as e:
        ph.empty()
        st.error(user_facing_error(e, context="database"))
        return

    if search:
        q        = search.lower()
        catalogs = [c for c in catalogs if
                    q in c["nombre"].lower() or
                    q in c["base_datos"].lower() or
                    q in c["tabla_destino"].lower()]

    if not catalogs:
        st.info("No hay catálogos activos." if not search else f"Sin resultados para '{search}'.")
        return

    st.caption(f"{len(catalogs)} catálogo(s) activo(s)")

    # Group by project preserving order
    from collections import defaultdict
    by_project: Dict[str, List] = defaultdict(list)
    for cat in catalogs:
        by_project[cat["proyecto"]].append(cat)
    usuarios_lookup = _load_usuarios_lookup()

    for project_name, group in by_project.items():
        with st.expander(f"📁 {project_name}  —  {len(group)} catálogo(s)", expanded=False):
            for cat in group:
                cid = cat["catalog_id"]
                try:
                    permisos = get_catalog_permissions(cid)
                except Exception:
                    permisos = []

                _icon_user = '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="#6B7280" style="vertical-align:middle;margin-right:2px;"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>'
                _icon_role = '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="#6B7280" style="vertical-align:middle;margin-right:2px;"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/></svg>'
                user_permisos = [
                    p for p in permisos
                    if p["tipo"] == "usuario"
                    and _is_publicador_activo(p["valor"], usuarios_lookup)
                ]
                user_chips = "".join(
                    _format_permiso_chip(p, usuarios_lookup, _icon_user, _icon_role)
                    for p in user_permisos
                )
                if user_permisos:
                    perm_text = (
                        '<span style="font-size:11px;color:#6B7280;font-weight:600;">Publicadores autorizados:</span>'
                        f'{user_chips}'
                    )
                else:
                    perm_text = (
                        '<span style="font-size:11px;color:#6B7280;font-weight:600;">Acceso:</span>'
                        '<span style="font-size:11px;color:#6B7280;">Solo Admins</span>'
                    )

                col_card, col_btns = st.columns([5, 2])

                with col_card:
                    st.markdown(
                        '<div style="padding:12px 16px;background:var(--secondary-background-color);'
                        'border-radius:10px;margin-bottom:4px;border-left:3px solid #534AB7;">'
                        f'<div style="font-weight:600;font-size:14px;">{cat["nombre"]}</div>'
                        '<div style="font-size:12px;color:#6B7280;margin-top:4px;display:flex;align-items:center;gap:4px;flex-wrap:wrap;">'
                        '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#6B7280" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4.03 3-9 3S3 13.66 3 12"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/></svg>'
                        f'<code>{cat["base_datos"]}.{cat["tabla_destino"]}</code>'
                        '</div>'
                        '<div style="margin-top:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">'
                        f'<span style="background:#E0E7FF;color:#3730A3;padding:1px 8px;border-radius:20px;font-size:11px;">{cat["estrategia"].upper()}</span>'
                        f'<span style="background:#D1FAE5;color:#065F46;padding:1px 8px;border-radius:20px;font-size:11px;">{cat["destino"].upper()}</span>'
                        '</div>'
                        '<div style="margin-top:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">'
                        f'{perm_text}'
                        '</div></div>',
                        unsafe_allow_html=True,
                    )

                with col_btns:
                    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
                    if st.button("Editar", key=f"btn_edit_{cid}", use_container_width=True):
                        st.session_state["adm_catalog_dialog"] = {"action": "edit", "catalog_id": cid}
                    if st.button("Permisos", key=f"btn_perm_{cid}", use_container_width=True):
                        st.session_state["adm_catalog_dialog"] = {"action": "permissions", "catalog_id": cid}
                    if st.button("Desactivar", key=f"btn_deact_{cid}", use_container_width=True):
                        st.session_state["adm_catalog_dialog"] = {"action": "deactivate", "catalog_id": cid}

                active_dialog = st.session_state.get("adm_catalog_dialog") or {}
                if active_dialog.get("catalog_id") == cid:
                    action = active_dialog.get("action")
                    if action == "edit":
                        _render_edit_catalog_dialog(cat)
                    elif action == "permissions":
                        _render_catalog_permissions_dialog(cat)
                    elif action == "deactivate":
                        _render_deactivate_catalog_dialog(cat)


# ------------------------------------------------------------------
# Tab 3: Gestión de usuarios
# ------------------------------------------------------------------
def _render_user_list(usuarios: list, system_admin: str) -> None:
    if not usuarios:
        st.caption("Sin usuarios en este grupo.")
        return
    for u in usuarios:
        uname        = u["username"]
        es_sys_admin = uname == system_admin.lower()
        activo       = bool(u["activo"])
        rol_actual   = u["rol"] or "Publicador"
        nombre       = u["nombre"] or uname
        email        = u["email"] or "—"
        ultimo       = str(u["ultimo_acceso"])[:16] if u["ultimo_acceso"] else "Nunca"

        rol_color = "#1C2F6E" if rol_actual == "Admin" else "#534AB7"
        act_color = "#16A34A" if activo else "#9CA3AF"
        act_text  = "Activo" if activo else "Inactivo"

        st.markdown(
            f'<div style="padding:10px 14px;background:var(--secondary-background-color);'
            f'border-radius:10px;border-left:3px solid {rol_color};margin-bottom:8px;">'
            f'<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:4px;">'
            f'<span style="font-weight:700;font-size:14px;">{uname}</span>'
            f'<span style="background:{"#DCFCE7" if activo else "#F3F4F6"};color:{act_color};'
            f'font-size:10px;font-weight:600;padding:1px 8px;border-radius:20px;">{act_text}</span>'
            + (f'<span style="font-size:10px;color:#F5A800;font-weight:600;">⭐ sistema</span>' if es_sys_admin else "")
            + f'</div><span style="font-size:12px;color:#6B7280;">{nombre} · {email}<br>Último acceso: {ultimo}</span></div>',
            unsafe_allow_html=True,
        )

        if not es_sys_admin:
            c1, c2 = st.columns([3, 1])
            with c1:
                nuevo_rol = st.selectbox(
                    "Rol", ["Publicador", "Admin"],
                    index=0 if rol_actual == "Publicador" else 1,
                    key=f"usr_rol_{uname}",
                    label_visibility="collapsed",
                )
                if nuevo_rol != rol_actual:
                    _rol_ok = False
                    try:
                        actor = st.session_state.get("user_info", {}).get("username", "")
                        update_user_rol(uname, nuevo_rol, actor_username=actor)
                        _rol_ok = True
                    except Exception as e:
                        st.error(user_facing_error(e, context="database"))
                    if _rol_ok:
                        st.rerun()
            with c2:
                nuevo_activo = st.toggle(
                    "Activo",
                    value=activo,
                    key=f"usr_act_{uname}",
                    help="Activar o desactivar usuario",
                )
                if nuevo_activo != activo:
                    _activo_ok = False
                    try:
                        actor = st.session_state.get("user_info", {}).get("username", "")
                        toggle_user_activo(uname, nuevo_activo, actor_username=actor)
                        _activo_ok = True
                    except Exception as e:
                        st.error(user_facing_error(e, context="database"))
                    if _activo_ok:
                        st.rerun()


def _render_add_admin_form() -> None:
    st.markdown("##### Agregar admin BA")
    st.caption(
        "Preautoriza un usuario BA como Admin. "
        "Cuando inicie sesion con LDAP, tomara este rol automaticamente."
    )

    with st.form("usr_add_admin_ba", clear_on_submit=True):
        username = st.text_input(
            "Usuario BA",
            placeholder="ba01006646",
            key="usr_add_admin_username",
        )

        submitted = st.form_submit_button(
            "Guardar como Admin",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    username_norm = str(username or "").strip().lower()
    if not username_norm:
        st.error("Ingresa el usuario BA.")
        return
    if not username_norm.startswith("ba"):
        st.error("El usuario debe iniciar con `ba`, por ejemplo `ba01006646`.")
        return
    if not re.fullmatch(r"ba[\w.-]+", username_norm):
        st.error("El usuario BA contiene caracteres no validos.")
        return

    _admin_ok = False
    try:
        result = create_or_promote_user(
            username=username_norm,
            rol="Admin",
            actor_username=st.session_state.get("user_info", {}).get("username", ""),
        )
        if result["created"]:
            st.success(f"Usuario `{username_norm}` creado como Admin.")
        else:
            st.success(f"Usuario `{username_norm}` actualizado a Admin.")
        _admin_ok = True
    except Exception as e:
        st.error(user_facing_error(e, context="database"))
    if _admin_ok:
        st.rerun()


def _tab_usuarios() -> None:
    from config.settings import SYSTEM_ADMIN_USERNAME

    st.markdown("""
    <div style="padding:4px 0 16px;">
        <p style="font-size:13px; color:#6B7280; margin:0;">
            Usuarios registrados automáticamente al iniciar sesión.
            Cambia el rol o desactiva el acceso desde aquí.
        </p>
    </div>
    """, unsafe_allow_html=True)

    _render_add_admin_form()
    st.divider()

    st.markdown("##### Respaldo y restauración")
    ux1, ux2 = st.columns([1, 2])
    with ux1:
        if st.button("Preparar backup usuarios", use_container_width=True, key="usr_export_bundle"):
            try:
                st.session_state["usr_export_bundle_payload"] = export_users_bundle()
                st.success("Backup de usuarios preparado.")
            except Exception as e:
                st.error(user_facing_error(e, context="database"))
        users_payload = st.session_state.get("usr_export_bundle_payload")
        if users_payload:
            st.download_button(
                "Descargar backup usuarios",
                data=json.dumps(users_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                file_name="usuarios_backup.json",
                mime="application/json",
                use_container_width=True,
            )
    with ux2:
        uploaded_users = st.file_uploader(
            "Restaurar usuarios desde backup JSON",
            type=["json"],
            key="usr_import_bundle",
            label_visibility="collapsed",
            help="Importa usuarios, roles y estado activo/inactivo.",
        )
        overwrite_users = st.checkbox(
            "Sobrescribir usuarios existentes",
            key="usr_import_overwrite",
            help="Si está activo, actualiza rol, nombre, correo y estado de usuarios ya existentes.",
        )
        if uploaded_users is not None and st.button("Importar usuarios", use_container_width=True, key="usr_import_bundle_btn"):
            _users_import_ok = False
            try:
                bundle = json.loads(uploaded_users.getvalue().decode("utf-8"))
                result = import_users_bundle(bundle, overwrite_existing=overwrite_users)
                st.success(
                    "Importación de usuarios completada. "
                    f"Creados: {result['created']} · Actualizados: {result['updated']} · Omitidos: {result['skipped']}"
                )
                _users_import_ok = True
            except Exception as e:
                st.error("No se pudo importar el backup de usuarios. Verifica que sea un JSON válido exportado por Data Gatekeeper.")
            if _users_import_ok:
                st.rerun()

    st.divider()

    ph = st.empty()
    ph.markdown(_skeleton_html(5), unsafe_allow_html=True)
    try:
        usuarios = get_all_usuarios()
        ph.empty()
    except Exception as e:
        ph.empty()
        st.error(user_facing_error(e, context="database"))
        return

    if not usuarios:
        st.info("Aún no hay usuarios registrados.")
        return

    admins      = [u for u in usuarios if u["rol"] == "Admin"]
    publicadores = [u for u in usuarios if u["rol"] != "Admin"]

    st.caption(f"{len(admins)} admin(s) · {len(publicadores)} publicador(es)")

    col_pub, col_adm = st.columns(2)

    with col_pub:
        st.markdown("##### Publicadores")
        _render_user_list(publicadores, SYSTEM_ADMIN_USERNAME)

    with col_adm:
        st.markdown("##### Admins")
        _render_user_list(admins, SYSTEM_ADMIN_USERNAME)


# ------------------------------------------------------------------
# CSS
# ------------------------------------------------------------------
def _inject_admin_css() -> None:
    st.markdown("""
    <style>
        #MainMenu, footer, header { visibility: hidden; }
        .block-container { padding-top: 24px !important; }

        section[data-testid="stSidebar"] {
            background: #1C2F6E !important;
            border-right: none;
        }
        section[data-testid="stSidebar"]::before {
            content: '';
            display: block;
            height: 4px;
            background: linear-gradient(90deg, #F5A800, #E52422);
            position: absolute;
            top: 0; left: 0; right: 0;
        }
        section[data-testid="stSidebar"] div.element-container:has(.sidebar-brand) + div.element-container {
            margin-top: -112px !important;
            height: 112px !important;
            margin-bottom: 18px !important;
            position: relative !important;
            z-index: 5 !important;
        }
        section[data-testid="stSidebar"] div.element-container:has(.sidebar-brand) + div.element-container .stButton {
            height: 112px !important;
        }
        section[data-testid="stSidebar"] div.element-container:has(.sidebar-brand) + div.element-container button {
            height: 112px !important;
            background: transparent !important;
            border: 0 !important;
            color: transparent !important;
            box-shadow: none !important;
        }
        section[data-testid="stSidebar"] div.element-container:has(.sidebar-brand) + div.element-container button:hover {
            background: rgba(255,255,255,0.08) !important;
            border-radius: 12px !important;
        }
        section[data-testid="stSidebar"] div.element-container:has(.sidebar-brand) + div.element-container button * {
            color: transparent !important;
        }
        section[data-testid="stSidebar"] * { color: #E8ECF8 !important; }
        section[data-testid="stSidebar"] hr { border-color: #2E4090 !important; }
        section[data-testid="stSidebar"] button {
            background: rgba(255,255,255,0.10) !important;
            border: 1px solid rgba(255,255,255,0.20) !important;
            color: white !important;
            border-radius: 8px !important;
        }

        div[data-testid="stTabs"] button {
            font-size: 14px;
            font-weight: 500;
        }
        div[data-testid="stTabs"] button[aria-selected="true"] {
            border-bottom: 2px solid #F5A800 !important;
        }

        /* ── Pill-tab para st.radio(horizontal=True) ──────────── */
        div[data-testid="stRadio"] {
            margin-bottom: 14px !important;
        }
        div[data-testid="stRadio"] > div:last-child {
            background: #EAEDF7;
            border-radius: 12px;
            padding: 4px 5px;
            display: inline-flex !important;
            gap: 2px;
            align-items: center;
        }
        div[data-testid="stRadio"] input[type="radio"] {
            position: absolute;
            opacity: 0;
            pointer-events: none;
            width: 0;
            height: 0;
        }
        div[data-testid="stRadio"] label {
            display: inline-flex !important;
            align-items: center;
            padding: 7px 18px !important;
            border-radius: 9px !important;
            cursor: pointer !important;
            font-size: 14px !important;
            font-weight: 500 !important;
            color: #6B7280 !important;
            margin: 0 !important;
            transition: background 0.2s ease, box-shadow 0.2s ease,
                        color 0.2s ease, transform 0.15s ease !important;
            transform: scale(1);
        }
        div[data-testid="stRadio"] label:hover {
            background: rgba(255,255,255,0.55) !important;
            transform: scale(1.03) !important;
        }
        div[data-testid="stRadio"] label p,
        div[data-testid="stRadio"] label div {
            font-size: 14px !important;
            font-weight: inherit !important;
            color: inherit !important;
            margin: 0 !important;
        }
        div[data-testid="stRadio"] label:has(input[type="radio"]:checked) {
            background: #FFFFFF !important;
            color: #1C2F6E !important;
            font-weight: 700 !important;
            box-shadow: 0 2px 8px rgba(28,47,110,0.18) !important;
            transform: scale(1) !important;
            animation: pill-pop 0.25s ease !important;
        }
        @keyframes pill-pop {
            0%   { transform: scale(0.92); box-shadow: none; opacity: 0.7; }
            60%  { transform: scale(1.05); }
            100% { transform: scale(1);   box-shadow: 0 2px 8px rgba(28,47,110,0.18); opacity: 1; }
        }
        .adm-step-row {
            display: grid;
            grid-template-columns: repeat(3, minmax(160px, 1fr));
            gap: 10px;
            margin: 6px 0 18px;
        }
        .adm-step-card {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 11px 13px;
            border-radius: 8px;
            border: 1px solid #D8DEF2;
            background: #FFFFFF;
            color: #6B7280;
            font-size: 12px;
            line-height: 1.3;
        }
        .adm-step-card b {
            color: inherit;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: .03em;
        }
        .adm-step-card.active {
            border-color: #534AB7;
            background: #F4F2FF;
            color: #1C2F6E;
        }
        .adm-step-card.done {
            border-color: #B7E7C6;
            background: #F2FFF6;
            color: #166534;
        }
        .adm-step-dot {
            width: 24px;
            min-width: 24px;
            height: 24px;
            border-radius: 999px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: #E7EAF5;
            color: #1C2F6E;
            font-weight: 800;
            font-size: 12px;
        }
        .adm-step-card.active .adm-step-dot {
            background: #534AB7;
            color: white;
        }
        .adm-step-card.done .adm-step-dot {
            background: #16A34A;
            color: white;
        }
        .adm-guided-panel {
            border: 1px solid #D8DEF2;
            background: #FFFFFF;
            border-radius: 8px;
            padding: 14px 16px;
            margin-bottom: 14px;
        }
        .adm-guided-title {
            font-size: 17px;
            font-weight: 800;
            color: #1C2F6E;
            margin-bottom: 4px;
        }
        .adm-guided-copy,
        .adm-section-copy {
            font-size: 12px;
            color: #6B7280;
            line-height: 1.45;
        }
        .adm-mini-metrics {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 12px;
        }
        .adm-mini-metrics span {
            border: 1px solid #D8DEF2;
            border-radius: 999px;
            padding: 5px 10px;
            background: #F8FAFF;
            color: #1C2F6E;
            font-size: 12px;
        }
        .adm-table-status-list {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 8px;
            margin: 8px 0 14px;
        }
        .adm-table-status {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            border: 1px solid #D8DEF2;
            border-radius: 8px;
            padding: 8px 10px;
            background: #FFFFFF;
        }
        .adm-table-status code {
            color: #1C2F6E;
            font-size: 11px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .adm-table-status span {
            font-size: 10px;
            font-weight: 800;
            border-radius: 999px;
            padding: 3px 7px;
            white-space: nowrap;
        }
        .adm-table-status.pending span {
            background: #F3F4F6;
            color: #6B7280;
        }
        .adm-table-status.active {
            border-color: #534AB7;
            background: #F4F2FF;
        }
        .adm-table-status.active span {
            background: #EDE9FE;
            color: #534AB7;
        }
        .adm-table-status.done {
            border-color: #B7E7C6;
            background: #F2FFF6;
        }
        .adm-table-status.done span {
            background: #DCFCE7;
            color: #166534;
        }
        .adm-scroll-strip {
            display: flex;
            gap: 8px;
            overflow-x: auto;
            padding: 4px 2px 10px;
            margin: 4px 0 10px;
            scrollbar-width: thin;
        }
        .adm-scroll-chip {
            min-width: 220px;
            max-width: 280px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            border: 1px solid #D8DEF2;
            border-radius: 8px;
            padding: 8px 10px;
            background: #FFFFFF;
            flex: 0 0 auto;
        }
        .adm-scroll-chip code {
            color: #1C2F6E;
            font-size: 11px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .adm-scroll-chip span {
            font-size: 10px;
            font-weight: 800;
            border-radius: 999px;
            padding: 3px 7px;
            white-space: nowrap;
            background: #F3F4F6;
            color: #6B7280;
        }
        .adm-scroll-chip.active {
            border-color: #534AB7;
            background: #F4F2FF;
        }
        .adm-scroll-chip.active span {
            background: #EDE9FE;
            color: #534AB7;
        }
        .adm-scroll-chip.done {
            border-color: #B7E7C6;
            background: #F2FFF6;
        }
        .adm-scroll-chip.done span {
            background: #DCFCE7;
            color: #166534;
        }
        .adm-section-title {
            color: #1C2F6E;
            font-size: 17px;
            font-weight: 800;
            margin-bottom: 4px;
        }
        .review-label {
            display: block;
            font-size: 10px;
            color: #6B7280;
            text-transform: uppercase;
            letter-spacing: .04em;
            font-weight: 700;
            margin-bottom: 2px;
        }
        .review-value {
            font-size: 13px;
            color: #1C2F6E;
            font-weight: 600;
            word-break: break-word;
        }
        div[data-testid="stExpander"] {
            border: 1px solid #D1D9F0 !important;
            border-radius: 8px !important;
        }
        @keyframes skel-shimmer {
            0%   { background-position: -600px 0; }
            100% { background-position:  600px 0; }
        }
        .skel-line {
            background: linear-gradient(90deg, #EAECF4 25%, #D8DCF0 50%, #EAECF4 75%);
            background-size: 1200px 100%;
            animation: skel-shimmer 1.5s ease infinite;
            border-radius: 4px;
            height: 13px;
            margin-bottom: 10px;
        }
        .skel-line.long   { width: 88%; }
        .skel-line.medium { width: 60%; }
        .skel-line.short  { width: 35%; }
        .skel-card {
            background: var(--secondary-background-color);
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 8px;
            border-left: 3px solid #E5E9F5;
        }

        div[data-testid="stButton"] button[kind="primary"] {
            background: #1C2F6E !important;
            border: none !important;
            border-radius: 8px !important;
            color: white !important;
            font-weight: 600 !important;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover {
            background: #15245A !important;
        }
        div[data-testid="stCheckbox"] label {
            font-size: 13px !important;
        }
    </style>
    """, unsafe_allow_html=True)
