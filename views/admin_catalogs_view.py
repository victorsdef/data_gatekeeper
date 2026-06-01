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
    get_catalog_permissions, save_permissions, get_all_usuarios_activos, get_catalog_id_by_table,
    update_catalog_config,
    get_catalog_schema,
    export_catalogs_bundle, import_catalogs_bundle,
)
from services.db_writer import get_audit_log
from utils.error_messages import user_facing_error
from services.user_service import (
    get_all_usuarios, update_user_rol, toggle_user_activo,
    export_users_bundle, import_users_bundle,
)

_TIPOS       = ["str", "int", "float", "bool"]
_ESTRATEGIAS = ["overwrite", "append", "reproceso"]
HIVE_ENABLED = bool(getattr(settings, "HIVE_ENABLED", True))
_DESTINOS    = ["singlestore"] + (["hive"] if HIVE_ENABLED else [])
_ROLES       = ["Publicador", "Admin"]
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
    active = st.selectbox(
        "Tabla activa",
        options=selected,
        index=selected_idx,
        key=f"adm_active_table_selector_{key_suffix}",
    )
    if active != st.session_state.get("adm_active_table"):
        st.session_state.adm_active_table = active
    return active


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
    errors: List[str] = []
    fuente = st.session_state.get("adm_step2_fuente") or st.session_state.get("adm_fuente", "SingleStore")
    for table in tables:
        cfg = _get_bulk_table_config(db, table)
        schema = cfg.get("schema")
        if not schema:
            rows = describe_hive_table(db, table) if fuente == "Hive" else describe_table(db, table)
            schema = build_schema_json(rows)
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


def _sync_bulk_form_state(db: str, active_table: str) -> None:
    configs = st.session_state.setdefault("adm_bulk_table_configs", {})
    previous_table = st.session_state.get("adm_bulk_form_table")

    if previous_table:
        configs[previous_table] = {
            "catalog_id": st.session_state.get("adm_bulk_form_cid", "").strip(),
            "nombre": st.session_state.get("adm_bulk_form_nombre", "").strip(),
            "descripcion": st.session_state.get("adm_bulk_form_desc", "").strip(),
            "schema": configs.get(previous_table, {}).get("schema"),
        }

    if previous_table != active_table:
        cfg = configs.get(active_table, _default_bulk_table_config(db, active_table))
        st.session_state["adm_bulk_form_cid"] = cfg["catalog_id"]
        st.session_state["adm_bulk_form_nombre"] = cfg["nombre"]
        st.session_state["adm_bulk_form_desc"] = cfg["descripcion"]
        st.session_state["adm_bulk_form_table"] = active_table


def _persist_bulk_form_state() -> None:
    current_table = st.session_state.get("adm_bulk_form_table")
    if not current_table:
        return
    configs = st.session_state.setdefault("adm_bulk_table_configs", {})
    configs[current_table] = {
        "catalog_id": st.session_state.get("adm_bulk_form_cid", "").strip(),
        "nombre": st.session_state.get("adm_bulk_form_nombre", "").strip(),
        "descripcion": st.session_state.get("adm_bulk_form_desc", "").strip(),
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
        logo_img = f'<img src="data:image/png;base64,{b64}" width="38" style="flex-shrink:0;">' if b64 else ""
        st.markdown(f"""
        <div style="padding:4px 0 20px;">
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:4px;">
                {logo_img}
                <div>
                    <div style="font-size:9px;font-weight:500;color:#A8B4D8;letter-spacing:0.5px;">banco del</div>
                    <div style="font-size:16px;font-weight:800;color:white;letter-spacing:-0.3px;line-height:1;">Austro</div>
                </div>
            </div>
            <div style="font-size:10px;color:#7A8EC0;margin-left:48px;">Administración de Catálogos</div>
        </div>
        """, unsafe_allow_html=True)
        st.divider()
        if st.button("← Volver al portal", use_container_width=True):
            st.session_state.current_view = "upload"
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
# Tab 1: Wizard de 2 pasos
# ------------------------------------------------------------------
def _tab_registro() -> None:
    if st.session_state.get("adm_step", 1) == 2:
        _render_step2()
        return

    # ── Selector de fuente ────────────────────────────────────────────
    fuentes = ["SingleStore"] + (["Hive"] if HIVE_ENABLED else [])
    fuente = st.radio(
        "Fuente de datos",
        fuentes,
        horizontal=True,
        key="adm_fuente",
        label_visibility="collapsed",
    )

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

    # ── Paso 1: Explorador de tablas ──────────────────────────────────
    _render_step_indicator(current=1)

    col_left, col_right = st.columns([1, 2], gap="large")

    with col_left:
        st.markdown("##### Explorador de tablas")
        db_sel = st.selectbox(
            "Base de datos", databases, key="adm_db", label_visibility="collapsed"
        )

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
            mapped: Set[tuple] = get_mapped_tables()
            ph_tables.empty()
        except Exception as e:
            ph_tables.empty()
            st.error(user_facing_error(e, context="database"))
            return

        # Cache de permisos por tabla registrada (roles: Público / Admin / etc.)
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

        # Paginación: resetear si el filtro cambió
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

        # Controles de paginación
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

        if selected:
            st.markdown("##### Seleccionadas")
            st.caption(f"{len(selected)} tabla(s) en la lista actual de registro")

    with col_right:
        if not selected:
            _render_empty_state()
        else:
            active_table = _render_active_table_selector(selected, key_suffix="right")
            _render_schema_only(db_sel, active_table, mapped)

    # ── Barra de navegación ───────────────────────────────────────────
    st.divider()
    _, btn_col = st.columns([3, 1])
    with btn_col:
        n   = len(selected) if selected else 0
        lbl = f"Continuar con {n} tabla(s) →" if n else "Selecciona tablas para continuar"
        if st.button(lbl, type="primary", use_container_width=True,
                     disabled=not selected, key="adm_step1_next"):
            st.session_state.adm_step = 2
            st.session_state["adm_step2_db"]       = db_sel
            st.session_state["adm_step2_selected"] = list(selected)
            st.session_state["adm_step2_fuente"]   = st.session_state.get("adm_fuente", "SingleStore")
            st.rerun()


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
    steps = ["Selección de tablas", "Configuración y registro"]
    parts = []
    for i, name in enumerate(steps, start=1):
        if i < current:
            color, weight, prefix = "#16A34A", "600", "✓ "
        elif i == current:
            color, weight, prefix = "#534AB7", "700", ""
        else:
            color, weight, prefix = "#9CA3AF", "400", ""
        parts.append(
            f'<span style="color:{color};font-weight:{weight};">{prefix}Paso {i}: {name}</span>'
        )
    st.markdown(
        '<div style="font-size:13px;margin-bottom:16px;display:flex;gap:0;">'
        + "&nbsp;&nbsp;→&nbsp;&nbsp;".join(parts)
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


# ------------------------------------------------------------------
# Panel derecho: múltiples tablas seleccionadas (registro en lote)
# ------------------------------------------------------------------
def _render_bulk_panel(db: str, selected: List[str], mapped: Set[tuple], active_table: str, show_schema: bool = True) -> None:
    already_reg = [t for t in selected if (db, t) in mapped]
    to_register = [t for t in selected if (db, t) not in mapped]
    active_is_registered = (db, active_table) in mapped
    _sync_bulk_form_state(db, active_table)

    if show_schema:
        _render_active_table_editor(db, active_table, mapped)

    schema = _load_schema_into_state(db, active_table)
    if schema is not None:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # Master-Detail: lista de tablas izquierda, configuración derecha
        cfg_left, cfg_right = st.columns([1, 2], gap="large")

        with cfg_left:
            st.markdown("**Tablas seleccionadas**")
            for t in selected:
                is_active = t == active_table
                is_reg = (db, t) in mapped
                label = f"• {t} ✓" if (is_active and is_reg) else f"• {t}" if is_active else f"{t} ✓" if is_reg else t
                if st.button(label, key=f"bulk_nav_{t}", use_container_width=True,
                             type="primary" if is_active else "secondary"):
                    _persist_bulk_form_state()
                    st.session_state.adm_active_table = t
                    st.rerun()

        with cfg_right:
            if active_is_registered:
                catalog_id = _catalog_id_for_existing_table(db, active_table)
                _render_permission_manager_inline(catalog_id, f"pm_bulk_{catalog_id}")
            else:
                active_key_prefix = _bulk_table_prefix(active_table)
                _render_registro_form(db, active_table, schema, key_prefix=active_key_prefix,
                                      submit_label="Guardar configuración de esta tabla", bulk_mode=True)

    st.divider()

    # Resumen del lote con estado por tabla
    configs_saved = st.session_state.get("adm_bulk_table_configs", {})

    already_badge = (
        "&nbsp;·&nbsp;"
        '<span style="color:#16A34A; font-weight:600;">'
        + str(len(already_reg))
        + " ya registradas (se omitirán)</span>"
        if already_reg else ""
    )
    st.markdown(
        '<div style="padding:10px 14px; background:#F0F4FF; border-radius:8px; '
        'border-left:3px solid #534AB7; margin-bottom:12px;">'
        '<span style="font-weight:700; font-size:13px;">'
        + str(len(selected)) + " tablas"
        + '</span><span style="font-size:12px; color:#6B7280;"> de <code>' + db + "</code></span>"
        '<div style="margin-top:4px; font-size:12px;">'
        '<span style="color:#534AB7; font-weight:600;">' + str(len(to_register)) + " para registrar</span>"
        + already_badge
        + "</div></div>",
        unsafe_allow_html=True,
    )

    for t in to_register:
        is_active = t == active_table
        is_saved  = t in configs_saved
        icon   = "✓" if is_saved else "○"
        color  = "#16A34A" if is_saved else "#9CA3AF"
        border = "2px solid #534AB7" if is_active else ("1px solid #BBF7D0" if is_saved else "1px solid #E5E7EB")
        bg     = "#F5F0FF" if is_active else ("#F0FFF4" if is_saved else "var(--secondary-background-color)")
        tag    = (
            '<span style="font-size:10px;color:#534AB7;font-weight:600;margin-left:auto;">● editando</span>'
            if is_active else
            '<span style="font-size:10px;color:#16A34A;font-weight:600;margin-left:auto;">guardada</span>'
            if is_saved else ""
        )
        st.markdown(
            '<div style="display:flex;align-items:center;gap:8px;padding:7px 12px;'
            'background:' + bg + ';border-radius:7px;margin-bottom:5px;border:' + border + ';">'
            '<span style="font-size:13px;color:' + color + ';">' + icon + "</span>"
            '<code style="font-size:12px;color:#1C2F6E;">' + t + "</code>"
            + tag + "</div>",
            unsafe_allow_html=True,
        )

    if already_reg:
        st.caption(f"Se omitirán (ya existen): {', '.join(already_reg)}")

    if not to_register:
        st.info("Todas las tablas seleccionadas ya están registradas. Desmarque las verdes o vaya a **Catálogos activos** para gestionar sus permisos.")
        return

    st.divider()
    st.markdown(
        '<div style="padding:4px 0 12px;">'
        '<span style="font-size:15px;font-weight:700;color:var(--text-color);">Registro del lote</span>'
        '<div style="font-size:12px;color:#6B7280;margin-top:2px;">'
        "Configuración compartida que se aplica a todas las tablas al registrar."
        "</div></div>",
        unsafe_allow_html=True,
    )

    try:
        projects = get_all_projects()
    except Exception as e:
        st.error(user_facing_error(e, context="database"))
        return

    bk_proj, bk_project_name, create_project = _render_project_inputs(db, "adm_bk", projects, active_table)

    c1, c2 = st.columns(2)
    with c1:
        bk_est  = st.selectbox("Estrategia", _ESTRATEGIAS, key="adm_bk_est")
    with c2:
        _dest_default = 1 if HIVE_ENABLED and st.session_state.get("adm_step2_fuente") == "Hive" else 0
        bk_dest = st.selectbox("Destino", _DESTINOS, index=_dest_default, key="adm_bk_dest")

    _render_permisos_selector("bk")

    project_errors = _validate_project_fields(bk_proj, bk_project_name)
    bulk_config_errors = _validate_bulk_configs(db, to_register)
    for err in project_errors + bulk_config_errors:
        st.warning(err)

    st.caption("El registro guarda la configuración en `gatekeeper_meta.catalogos_config`.")

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
        f'padding:1px 7px;border-radius:20px;">{nullable_n} nullable</span>'
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
            'padding:1px 8px;border-radius:20px;">nullable</span>'
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

    st.markdown(
        '<div style="font-size:12px;font-weight:600;color:#6B7280;'
        'padding:8px 0 4px;border-top:1px solid #E5E9F5;margin-top:8px;">'
        'Reglas de calidad</div>',
        unsafe_allow_html=True,
    )
    sel_col = st.selectbox(
        "Columna",
        [col["nombre"] for col in col_defs],
        key=f"{state_key}_sel",
        label_visibility="collapsed",
    )
    sel_type = next((col["tipo"] for col in col_defs if col["nombre"] == sel_col), "str")
    regla_options = _REGLAS_POR_TIPO.get(sel_type, _REGLA_TIPOS)
    tipo_key = f"{state_key}_new_tipo"
    if st.session_state.get(tipo_key) not in regla_options:
        st.session_state.pop(tipo_key, None)
    col_rules = list(rules.get(sel_col, []))

    if col_rules:
        for i, regla in enumerate(col_rules):
            t = regla.get("tipo", "")
            if t == "isin":
                desc = "Dominio: " + ", ".join(str(v) for v in regla.get("valor", []))
            elif t == "gte":
                desc = f"Valor minimo: {regla.get('valor', '')}"
            elif t == "lte":
                desc = f"Valor maximo: {regla.get('valor', '')}"
            elif t == "min_length":
                desc = f"Longitud minima: {regla.get('valor', '')}"
            elif t == "str_length":
                desc = f"Longitud entre {regla.get('min','')} y {regla.get('max','')}"
            elif t == "regex":
                desc = f"Regex: {regla.get('valor', '')}"
            else:
                desc = str(regla)
            rc1, rc2 = st.columns([8, 1])
            with rc1:
                st.markdown(
                    f'<div style="background:#EEF2FF;color:#3730A3;font-size:12px;'
                    f'padding:4px 12px;border-radius:6px;margin-bottom:4px;">{desc}</div>',
                    unsafe_allow_html=True,
                )
            with rc2:
                if st.button("✕", key=f"{state_key}_del_{sel_col}_{i}", help="Eliminar"):
                    new_rules = dict(rules)
                    new_rules[sel_col] = [r for j, r in enumerate(col_rules) if j != i]
                    st.session_state[state_key] = new_rules
                    st.rerun()
    else:
        st.caption("Sin reglas para esta columna.")

    if not regla_options:
        st.info(f"El tipo `{sel_type}` no tiene reglas adicionales configurables.")
        return

    tipo_sel = st.selectbox(
        "Tipo de regla",
        regla_options,
        key=tipo_key,
        format_func=lambda x: _REGLA_LABELS.get(x, x),
    )

    nueva_regla = None
    if tipo_sel == "isin":
        val_str = st.text_input(
            "Valores permitidos (separados por coma)",
            key=f"{state_key}_new_isin",
            placeholder="C, D, N",
        )
        if st.button("Agregar regla", key=f"{state_key}_add_btn", type="primary"):
            valores = [v.strip() for v in val_str.split(",") if v.strip()]
            if valores:
                nueva_regla = {"tipo": "isin", "valor": valores}

    elif tipo_sel in ("gte", "lte"):
        num_val = st.number_input(
            "Valor limite",
            key=f"{state_key}_new_num",
            value=0.0,
        )
        if st.button("Agregar regla", key=f"{state_key}_add_btn", type="primary"):
            nueva_regla = {"tipo": tipo_sel, "valor": float(num_val)}

    elif tipo_sel == "min_length":
        min_val = st.number_input(
            "Caracteres minimos",
            key=f"{state_key}_new_minlen",
            value=1, min_value=1, step=1,
        )
        if st.button("Agregar regla", key=f"{state_key}_add_btn", type="primary"):
            nueva_regla = {"tipo": "min_length", "valor": int(min_val)}

    elif tipo_sel == "str_length":
        nc1, nc2 = st.columns(2)
        with nc1:
            sl_min = st.number_input("Min", key=f"{state_key}_new_slmin", value=1, min_value=0, step=1)
        with nc2:
            sl_max = st.number_input("Max", key=f"{state_key}_new_slmax", value=50, min_value=1, step=1)
        if st.button("Agregar regla", key=f"{state_key}_add_btn", type="primary"):
            nueva_regla = {"tipo": "str_length", "min": int(sl_min), "max": int(sl_max)}

    elif tipo_sel == "regex":
        pattern = st.text_input(
            "Patron regex",
            key=f"{state_key}_new_regex",
            placeholder=r"^[A-Z0-9_-]+$",
        )
        if st.button("Agregar regla", key=f"{state_key}_add_btn", type="primary"):
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


def _render_schema_editor(schema: dict, key_prefix: str) -> None:
    columnas = schema.get("columnas", [])
    schema_cache_key = st.session_state.get("adm_schema_key", "default")
    state_key  = f"{key_prefix}_schema_rows_{hash(schema_cache_key)}"
    result_key = f"{key_prefix}_schema_edited_{hash(schema_cache_key)}"
    rules_key  = f"{key_prefix}_rules_{hash(schema_cache_key)}"

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

    df = st.session_state[state_key]
    height = 38 + len(df) * 35 + 2

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
            f'padding:1px 7px;border-radius:20px;">{nullable_n} nullable</span>'
            f'<span style="background:#FEE2E2;color:#991B1B;font-size:10px;font-weight:600;'
            f'padding:1px 7px;border-radius:20px;">{len(df)-nullable_n} requerido</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # result_key ≠ state_key → no alimenta de vuelta al editor, evita el loop
    edited = st.data_editor(
        df,
        column_config={
            "nombre":   st.column_config.TextColumn("Columna",  width="medium"),
            "tipo":     st.column_config.SelectboxColumn("Tipo", options=_TIPOS, required=True, width="small"),
            "nullable": st.column_config.CheckboxColumn("Nullable", width="small"),
        },
        disabled=["nombre"],
        use_container_width=False,
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
    _render_rules_editor(rule_columns, rules_key)


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

    data = st.session_state.get(result_key)
    if data is None:
        data = st.session_state.get(state_key)
    if data is None:
        return schema

    rules_por_columna = st.session_state.get(rules_key, {})
    rows = data.to_dict("records") if isinstance(data, pd.DataFrame) else data
    return {
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


def _render_permisos_selector(key_prefix: str, current: List[Dict] | None = None) -> None:
    current    = current or []
    init_roles = [p["valor"] for p in current if p["tipo"] == "rol"]
    init_users = [p["valor"] for p in current if p["tipo"] == "usuario"]

    st.markdown("**Permisos de acceso**")
    st.caption("Sin selección = accesible para todos los publicadores")

    c1, c2 = st.columns(2)
    with c1:
        st.multiselect("Roles", _ROLES, default=init_roles, key=f"{key_prefix}_roles")
    with c2:
        try:
            users = get_all_usuarios_activos()
        except Exception:
            users = []
        st.multiselect("Usuarios específicos", users, default=init_users, key=f"{key_prefix}_users")


def _collect_permisos(key_prefix: str) -> List[Dict]:
    roles = st.session_state.get(f"{key_prefix}_roles", [])
    users = st.session_state.get(f"{key_prefix}_users", [])
    return (
        [{"tipo": "rol",     "valor": r} for r in roles] +
        [{"tipo": "usuario", "valor": u} for u in users]
    )


def _render_registro_form(
    db_sel: str,
    tbl_sel: str,
    schema: dict,
    key_prefix: str,
    submit_label: str = "Registrar catálogo",
    bulk_mode: bool = False,
) -> None:
    st.markdown("##### Explorador de configuración")

    if not bulk_mode:
        try:
            projects = get_all_projects()
        except Exception as e:
            st.error(user_facing_error(e, context="database"))
            return
        proj_sel, proj_name, create_project = _render_project_inputs(db_sel, key_prefix, projects, tbl_sel)

    cid_default, nombre_default = _catalog_defaults(db_sel, tbl_sel)
    catalog_id  = st.text_input(
        "ID del catálogo", value=st.session_state.get(f"{key_prefix}_cid", cid_default), key=f"{key_prefix}_cid",
        help="Se genera automáticamente a partir del proyecto y la tabla.",
        disabled=True,
    )
    nombre      = st.text_input(
        "Nombre legible", value=st.session_state.get(f"{key_prefix}_nombre", nombre_default), key=f"{key_prefix}_nombre"
    )
    descripcion = st.text_area(
        "Descripción (opcional)",
        value=st.session_state.get(f"{key_prefix}_desc", ""),
        key=f"{key_prefix}_desc",
        height=56,
    )

    if not bulk_mode:
        c1, c2 = st.columns(2)
        with c1:
            estrategia = st.selectbox("Estrategia", _ESTRATEGIAS, key=f"{key_prefix}_est")
        with c2:
            _dest_idx = 1 if HIVE_ENABLED and st.session_state.get("adm_step2_fuente") == "Hive" else 0
            destino = st.selectbox("Destino", _DESTINOS, index=_dest_idx, key=f"{key_prefix}_dest")
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        _render_permisos_selector(key_prefix)

    cid = catalog_id.strip()
    current_schema = _collect_schema(schema, key_prefix) if not bulk_mode else _collect_schema(schema, "bulk")
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
            st.rerun()
        except Exception as e:
            st.error(user_facing_error(e, context="database"))


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
            try:
                bundle = json.loads(uploaded_bundle.getvalue().decode("utf-8"))
                result = import_catalogs_bundle(bundle, overwrite_existing=overwrite_existing)
                st.success(
                    "Importación completada. "
                    f"Creados: {result['created']} · Actualizados: {result['updated']} · Omitidos: {result['skipped']}"
                )
                st.rerun()
            except Exception as e:
                st.error("No se pudo importar el backup. Verifica que sea un JSON válido exportado por Data Gatekeeper.")

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
                perm_text = ", ".join(
                    f"{_icon_user if p['tipo'] == 'usuario' else _icon_role}{p['valor']}"
                    for p in permisos
                ) or "Todos los publicadores"

                manage_key  = f"adm_ac_perm_{cid}"
                confirm_key = f"adm_ac_conf_{cid}"
                edit_key    = f"adm_ac_edit_{cid}"

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
                        f'<span style="font-size:11px;color:#6B7280;">Acceso: {perm_text}</span>'
                        '</div></div>',
                        unsafe_allow_html=True,
                    )

                with col_btns:
                    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
                    if st.button("Editar", key=f"btn_edit_{cid}", use_container_width=True):
                        st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                        st.session_state[manage_key] = False
                        st.rerun()
                    if st.button("Permisos", key=f"btn_perm_{cid}", use_container_width=True):
                        st.session_state[manage_key] = not st.session_state.get(manage_key, False)
                        st.session_state[edit_key] = False
                        st.rerun()
                    if st.button("Desactivar", key=f"btn_deact_{cid}", use_container_width=True):
                        st.session_state[confirm_key] = True

                if st.session_state.get(edit_key):
                    with st.container():
                        st.markdown(
                            '<div style="padding:10px 14px;background:#FFF7ED;'
                            'border:1px solid #FED7AA;border-radius:8px;margin-bottom:12px;">'
                            f'<span style="font-size:13px;font-weight:600;color:#92400E;">Editar — {cat["nombre"]}</span>'
                            '</div>',
                            unsafe_allow_html=True,
                        )
                        ek = f"ed_{cid}"
                        ed_nombre = st.text_input(
                            "Nombre", value=cat["nombre"], key=f"{ek}_nombre"
                        )
                        ed_desc = st.text_area(
                            "Descripción", value=cat.get("descripcion") or "", key=f"{ek}_desc", height=60
                        )
                        ec1, ec2 = st.columns(2)
                        with ec1:
                            ed_est = st.selectbox(
                                "Estrategia", _ESTRATEGIAS,
                                index=_ESTRATEGIAS.index(cat["estrategia"]) if cat["estrategia"] in _ESTRATEGIAS else 0,
                                key=f"{ek}_est",
                            )
                        with ec2:
                            ed_dest = st.selectbox(
                                "Destino", _DESTINOS,
                                index=_DESTINOS.index(cat["destino"]) if cat["destino"] in _DESTINOS else 0,
                                key=f"{ek}_dest",
                            )

                        st.markdown("**Columnas del esquema**")
                        st.caption("Puedes editar nombre, tipo y nulabilidad. Usa la última fila vacía para agregar columnas.")

                        schema_init_key = f"{ek}_schema_init"
                        rules_edit_key  = f"{ek}_rules_state"
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
                                    for c in col_defs if c.get("reglas")
                                }
                            except Exception as e:
                                st.error(user_facing_error(e, context="database"))
                                st.session_state[schema_init_key] = []

                        init_rows = st.session_state[schema_init_key]
                        init_df = pd.DataFrame(init_rows) if init_rows else pd.DataFrame(columns=["nombre", "tipo", "nullable"])

                        edited_df = st.data_editor(
                            init_df,
                            column_config={
                                "nombre":   st.column_config.TextColumn("Columna", required=True),
                                "tipo":     st.column_config.SelectboxColumn("Tipo", options=_TIPOS, required=True),
                                "nullable": st.column_config.CheckboxColumn("Nullable"),
                            },
                            use_container_width=True,
                            num_rows="dynamic",
                            hide_index=True,
                            key=f"{ek}_schema_editor",
                        )

                        edit_rule_columns = [
                            {
                                "nombre": str(r.get("nombre", "")).strip(),
                                "tipo": str(r.get("tipo", "str") or "str").strip().lower(),
                            }
                            for _, r in edited_df.iterrows()
                            if str(r.get("nombre", "")).strip()
                        ]
                        _render_rules_editor(edit_rule_columns, rules_edit_key)

                        edit_schema_errors = _validate_catalog_form(
                            catalog_id=cid,
                            nombre=ed_nombre.strip(),
                            schema={
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
                            },
                        )
                        for err in edit_schema_errors:
                            st.warning(err)

                        bc1, bc2 = st.columns(2)
                        with bc1:
                            if st.button("Guardar cambios", type="primary", key=f"{ek}_save", use_container_width=True, disabled=bool(edit_schema_errors)):
                                try:
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
                                    update_catalog_config(cid, ed_nombre.strip(), ed_desc.strip(), ed_est, ed_dest, new_schema)
                                    st.success("Catálogo actualizado.")
                                    st.session_state.pop(schema_init_key, None)
                                    st.session_state.pop(rules_edit_key, None)
                                    st.session_state[edit_key] = False
                                    st.rerun()
                                except Exception as e:
                                    st.error(user_facing_error(e, context="database"))
                        with bc2:
                            if st.button("Cancelar", key=f"{ek}_cancel", use_container_width=True):
                                st.session_state.pop(schema_init_key, None)
                                st.session_state.pop(rules_edit_key, None)
                                st.session_state[edit_key] = False
                                st.rerun()

                if st.session_state.get(manage_key):
                    with st.container():
                        st.markdown(
                            '<div style="padding:10px 14px;background:#F0F4FF;'
                            'border:1px solid #C7D2FE;border-radius:8px;margin-bottom:8px;">'
                            f'<span style="font-size:13px;font-weight:600;color:#1C2F6E;">Permisos — {cat["nombre"]}</span>'
                            '</div>',
                            unsafe_allow_html=True,
                        )
                        pk = f"ac_{cid}"
                        _render_permisos_selector(pk, current=permisos)
                        bc1, bc2 = st.columns(2)
                        with bc1:
                            if st.button("Guardar", type="primary", key=f"save_perm_{cid}", use_container_width=True):
                                try:
                                    save_permissions(cid, _collect_permisos(pk))
                                    st.success("Permisos actualizados.")
                                    st.session_state[manage_key] = False
                                    st.rerun()
                                except Exception as e:
                                    st.error(user_facing_error(e, context="database"))
                        with bc2:
                            if st.button("Cancelar", key=f"cancel_perm_{cid}", use_container_width=True):
                                st.session_state[manage_key] = False
                                st.rerun()

                if st.session_state.get(confirm_key):
                    st.warning(f"¿Desactivar **{cat['nombre']}**? Los publicadores perderán acceso inmediatamente.")
                    dc1, dc2 = st.columns(2)
                    with dc1:
                        if st.button("Confirmar", type="primary", key=f"yes_deact_{cid}", use_container_width=True):
                            try:
                                deactivate_catalog(cid)
                                st.session_state.pop(confirm_key, None)
                                st.success("Catálogo desactivado.")
                                st.rerun()
                            except Exception as e:
                                st.error(user_facing_error(e, context="database"))
                    with dc2:
                        if st.button("Cancelar", key=f"no_deact_{cid}", use_container_width=True):
                            st.session_state.pop(confirm_key, None)
                            st.rerun()


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
                    try:
                        update_user_rol(uname, nuevo_rol)
                        st.rerun()
                    except Exception as e:
                        st.error(user_facing_error(e, context="database"))
            with c2:
                if st.button("✓" if activo else "✗", key=f"usr_act_{uname}",
                             use_container_width=True, help="Activar/Desactivar"):
                    try:
                        toggle_user_activo(uname, not activo)
                        st.rerun()
                    except Exception as e:
                        st.error(user_facing_error(e, context="database"))


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
            try:
                bundle = json.loads(uploaded_users.getvalue().decode("utf-8"))
                result = import_users_bundle(bundle, overwrite_existing=overwrite_users)
                st.success(
                    "Importación de usuarios completada. "
                    f"Creados: {result['created']} · Actualizados: {result['updated']} · Omitidos: {result['skipped']}"
                )
                st.rerun()
            except Exception as e:
                st.error("No se pudo importar el backup de usuarios. Verifica que sea un JSON válido exportado por Data Gatekeeper.")

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
