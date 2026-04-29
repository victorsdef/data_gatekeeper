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
from pathlib import Path
from typing import Dict, List, Set
import pandas as pd
import streamlit as st

from utils.db_admin import (
    get_all_databases, get_tables_from_db, get_mapped_tables,
    describe_table, build_schema_json,
    get_all_projects, get_active_catalogs,
    save_catalog_config, catalog_exists, deactivate_catalog, ensure_project_exists,
    get_catalog_permissions, save_permissions, get_all_usuarios_activos,
)

_TIPOS       = ["str", "int", "float", "bool"]
_ESTRATEGIAS = ["overwrite", "append", "reproceso"]
_DESTINOS    = ["singlestore", "hive"]
_ROLES       = ["Publicador", "Admin"]


def _logo_b64() -> str:
    logo = Path(__file__).parent.parent / "assets" / "logo.png"
    return base64.b64encode(logo.read_bytes()).decode() if logo.exists() else ""


def _sync_selected_tables(all_tables: List[str]) -> List[str]:
    selected = [t for t in all_tables if st.session_state.get(f"adm_chk_{t}", False)]
    st.session_state.adm_selected_tables = selected

    active_table = st.session_state.get("adm_active_table")
    if selected:
        if active_table not in selected:
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


def _default_bulk_table_config(db: str, table: str) -> Dict[str, str]:
    return {
        "catalog_id": re.sub(r"[^a-z0-9_]", "_", f"{db}__{table}".lower()).strip(),
        "nombre": table.replace("_", " ").title(),
        "descripcion": "",
    }


def _sync_bulk_form_state(db: str, active_table: str) -> None:
    configs = st.session_state.setdefault("adm_bulk_table_configs", {})
    previous_table = st.session_state.get("adm_bulk_form_table")

    if previous_table:
        configs[previous_table] = {
            "catalog_id": st.session_state.get("adm_bulk_form_cid", "").strip(),
            "nombre": st.session_state.get("adm_bulk_form_nombre", "").strip(),
            "descripcion": st.session_state.get("adm_bulk_form_desc", "").strip(),
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
    }


def _project_defaults_from_db(database: str) -> tuple[str, str]:
    project_id = re.sub(r"[^a-z0-9_]+", "_", database.lower()).strip("_")
    project_name = database.replace("_", " ").strip().title()
    return project_id or "nuevo_proyecto", project_name or "Nuevo Proyecto"


def _render_project_inputs(db_name: str, key_prefix: str, projects: List[Dict]) -> tuple[str, str, bool]:
    suggested_id, suggested_name = _project_defaults_from_db(db_name)
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
        help="Se generó a partir del nombre de la base de datos, pero puedes cambiarlo.",
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

    tab1, tab2 = st.tabs(["Registrar catálogos", "Catálogos activos"])

    with tab1:
        _tab_registro()
    with tab2:
        _tab_activos()


# ------------------------------------------------------------------
# Tab 1: Wizard de 2 pasos
# ------------------------------------------------------------------
def _tab_registro() -> None:
    try:
        databases = get_all_databases()
    except Exception as e:
        st.error(f"Sin conexión a SingleStore: {e}")
        return

    if not databases:
        st.warning("No hay bases de datos de negocio disponibles. Verifica permisos del usuario de SingleStore.")
        return

    if st.session_state.get("adm_step", 1) == 2:
        _render_step2()
        return

    # ── Paso 1: Explorador de tablas ──────────────────────────────────
    _render_step_indicator(current=1)

    col_left, col_right = st.columns([1, 2], gap="large")

    with col_left:
        st.markdown("##### Explorador de tablas")
        db_sel = st.selectbox(
            "Base de datos", databases, key="adm_db", label_visibility="collapsed"
        )

        if st.session_state.get("adm_prev_db") != db_sel:
            for k in list(st.session_state.keys()):
                if k.startswith("adm_chk_") or k.startswith("adm_cat_info_"):
                    del st.session_state[k]
            st.session_state.pop("adm_schema_key", None)
            st.session_state.pop("adm_selected_tables", None)
            st.session_state.pop("adm_active_table", None)
            st.session_state.pop("adm_tbl_page", None)
            st.session_state.adm_prev_db = db_sel

        try:
            all_tables = get_tables_from_db(db_sel)
            mapped: Set[tuple] = get_mapped_tables()
        except Exception as e:
            st.error(f"Error al listar tablas: {e}")
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
                for t in filtered:
                    if (db_sel, t) not in mapped:
                        st.session_state[f"adm_chk_{t}"] = True
                st.rerun()
        with bc2:
            if st.button("Limpiar", use_container_width=True, key="adm_sel_clear"):
                for t in all_tables:
                    st.session_state[f"adm_chk_{t}"] = False
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
                    st.checkbox("", key=f"adm_chk_{t}", label_visibility="collapsed")
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
        st.error(f"Error al cargar tablas registradas: {e}")
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
        catalog_id = re.sub(r"[^a-z0-9_]", "_", f"{db}__{table}".lower())
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
        catalog_id = re.sub(r"[^a-z0-9_]", "_", f"{db}__{table}".lower())
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
                catalog_id = re.sub(r"[^a-z0-9_]", "_", f"{db}__{active_table}".lower())
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
        st.error(f"Error al cargar proyectos: {e}")
        return

    bk_proj, bk_project_name, create_project = _render_project_inputs(db, "adm_bk", projects)

    c1, c2 = st.columns(2)
    with c1:
        bk_est  = st.selectbox("Estrategia", _ESTRATEGIAS, key="adm_bk_est")
    with c2:
        bk_dest = st.selectbox("Destino", _DESTINOS, key="adm_bk_dest")

    _render_permisos_selector("bk")

    project_errors: List[str] = []
    if not bk_proj:
        project_errors.append("El ID del proyecto no puede estar vacío.")
    elif not re.match(r"^[a-z0-9_]+$", bk_proj):
        project_errors.append("El ID del proyecto solo puede tener minúsculas, números y guiones bajos.")
    if not bk_project_name:
        project_errors.append("El nombre del proyecto no puede estar vacío.")
    for err in project_errors:
        st.warning(err)

    st.caption("El registro guarda la configuración en `gatekeeper_meta.catalogos_config`.")

    if st.button(
        f"Registrar tablas seleccionadas ({len(to_register)})", type="primary",
        use_container_width=True, key="adm_bk_go", disabled=bool(project_errors)
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
            schema     = build_schema_json(describe_table(db, table))
            cfg = _get_bulk_table_config(db, table)
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
            failed.append(f"{table}: {e}")

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
    for col in schema.get("columnas", []):
        null_tag = (
            "<span style='color:#6EE7B7;font-size:10px;margin-left:4px;'>nullable</span>"
            if col.get("nullable") else ""
        )
        st.markdown(
            f"<div style='font-size:12px;padding:3px 8px;background:#F8F9FA;"
            f"border-radius:4px;margin-bottom:3px;display:flex;justify-content:space-between;'>"
            f"<code style='color:#534AB7'>{col['nombre']}</code>"
            f"<span style='color:#6B7280'>{col['tipo']}{null_tag}</span></div>",
            unsafe_allow_html=True,
        )


def _render_schema_editor(schema: dict, key_prefix: str) -> None:
    columnas = schema.get("columnas", [])
    schema_cache_key = st.session_state.get("adm_schema_key", "default")
    state_key = f"{key_prefix}_schema_rows_{hash(schema_cache_key)}"
    active_key = f"{key_prefix}_active_col_{hash(schema_cache_key)}"
    filter_key = f"{key_prefix}_filter_col_{hash(schema_cache_key)}"

    if state_key not in st.session_state:
        st.session_state[state_key] = [
            {
                "nombre": col["nombre"],
                "tipo": col["tipo"] if col["tipo"] in _TIPOS else "str",
                "nullable": col.get("nullable", True),
                "reglas": col.get("reglas", []),
            }
            for col in columnas
        ]

    rows = st.session_state[state_key]
    if not rows:
        st.info("Sin columnas para editar.")
        return

    if active_key not in st.session_state or st.session_state[active_key] not in [r["nombre"] for r in rows]:
        st.session_state[active_key] = rows[0]["nombre"]

    left, right = st.columns([1.1, 1.6], gap="large")

    with left:
        st.markdown("**Columnas**")
        st.text_input(
            "Buscar columna",
            placeholder="Buscar columna...",
            key=filter_key,
            label_visibility="collapsed",
        )
        query = st.session_state.get(filter_key, "").strip().lower()
        filtered_rows = [r for r in rows if not query or query in r["nombre"].lower()]

        if not filtered_rows:
            st.caption("Sin coincidencias.")
        else:
            for row in filtered_rows:
                is_active = st.session_state.get(active_key) == row["nombre"]
                label = f"• {row['nombre']}" if is_active else row["nombre"]
                if st.button(
                    label,
                    key=f"{active_key}_btn_{row['nombre']}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state[active_key] = row["nombre"]
                    st.rerun()

    with right:
        active_name = st.session_state.get(active_key, rows[0]["nombre"])
        current_idx = next((i for i, r in enumerate(rows) if r["nombre"] == active_name), 0)
        current = rows[current_idx]

        st.markdown(
            f"""
            <div style="text-align:center; padding:8px 10px; background:#F6F8FC; border:1px solid #D1D9F0;
                        border-radius:10px; font-size:14px; font-weight:700; color:#1C2F6E;">
                {current['nombre']}
                <div style="font-size:11px; font-weight:500; color:#6B7280; margin-top:3px;">
                    Columna {current_idx + 1} de {len(rows)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        if st.button("← Anterior", use_container_width=True, key=f"{active_key}_prev", disabled=current_idx == 0):
            st.session_state[active_key] = rows[current_idx - 1]["nombre"]
            st.rerun()
        if st.button("Siguiente →", use_container_width=True, key=f"{active_key}_next", disabled=current_idx >= len(rows) - 1):
            st.session_state[active_key] = rows[current_idx + 1]["nombre"]
            st.rerun()

        new_tipo = st.selectbox(
            "Tipo",
            _TIPOS,
            index=_TIPOS.index(current["tipo"]) if current["tipo"] in _TIPOS else 0,
            key=f"{active_key}_tipo_{current['nombre']}",
        )
        new_nullable = st.checkbox(
            "Nullable",
            value=bool(current.get("nullable", True)),
            key=f"{active_key}_nullable_{current['nombre']}",
        )

        rows[current_idx]["tipo"] = new_tipo
        rows[current_idx]["nullable"] = new_nullable
        st.session_state[state_key] = rows

        preview_df = pd.DataFrame([
            {
                "Columna": row["nombre"],
                "Tipo": row["tipo"],
                "Nullable": "Sí" if row["nullable"] else "No",
            }
            for row in rows
        ])
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        st.dataframe(
            preview_df,
            use_container_width=True,
            hide_index=True,
            height=min(len(preview_df) * 35 + 38, 240),
        )


def _bulk_table_prefix(table: str) -> str:
    return f"bulk_{re.sub(r'[^a-z0-9_]', '_', table.lower())}"


def _get_bulk_table_config(db: str, table: str) -> Dict[str, str]:
    configs = st.session_state.get("adm_bulk_table_configs", {})
    return configs.get(table, _default_bulk_table_config(db, table))

def _load_schema_into_state(db: str, table: str) -> dict | None:
    schema_key = f"{db}.{table}"
    if st.session_state.get("adm_schema_key") != schema_key:
        try:
            rows = describe_table(db, table)
            st.session_state.adm_schema = build_schema_json(rows)
            st.session_state.adm_schema_key = schema_key
        except Exception as e:
            st.error(f"Error al consultar esquema: {e}")
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
    orig = schema.get("columnas", [])
    schema_cache_key = st.session_state.get("adm_schema_key", "default")
    state_key = f"{key_prefix}_schema_rows_{hash(schema_cache_key)}"
    rows = st.session_state.get(state_key)
    if not rows:
        return schema

    reglas_por_columna = {
        col.get("nombre"): col.get("reglas", [])
        for col in orig
    }
    return {
        "columnas": [
            {
                "nombre": str(row["nombre"]),
                "tipo": str(row["tipo"]),
                "nullable": bool(row["nullable"]),
                "reglas": reglas_por_columna.get(row["nombre"], row.get("reglas", [])),
            }
            for row in rows
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
            st.error(f"Error al cargar proyectos: {e}")
            return
        proj_sel, proj_name, create_project = _render_project_inputs(db_sel, key_prefix, projects)

    cid_default = re.sub(r"[^a-z0-9_]", "_", f"{db_sel}__{tbl_sel}".lower())
    catalog_id  = st.text_input(
        "ID del catálogo", value=st.session_state.get(f"{key_prefix}_cid", cid_default), key=f"{key_prefix}_cid",
        help="Solo minúsculas, números y guiones bajos."
    )
    nombre      = st.text_input(
        "Nombre legible", value=st.session_state.get(f"{key_prefix}_nombre", tbl_sel.replace("_", " ").title()), key=f"{key_prefix}_nombre"
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
            destino = st.selectbox("Destino", _DESTINOS, key=f"{key_prefix}_dest")
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        _render_permisos_selector(key_prefix)

    cid    = catalog_id.strip()
    errores: List[str] = []
    if not bulk_mode:
        if not proj_sel:
            errores.append("El ID del proyecto no puede estar vacío.")
        elif not re.match(r"^[a-z0-9_]+$", proj_sel):
            errores.append("El ID del proyecto solo puede tener minúsculas, números y guiones bajos.")
        if not proj_name:
            errores.append("El nombre del proyecto no puede estar vacío.")
    if not cid:
        errores.append("El ID no puede estar vacío.")
    elif not re.match(r"^[a-z0-9_]+$", cid):
        errores.append("El ID solo puede tener minúsculas, números y guiones bajos.")
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
                schema_json   = _collect_schema(schema, key_prefix),
            )
            save_permissions(cid, _collect_permisos(key_prefix))
            st.success(f"Catálogo **{nombre.strip()}** registrado correctamente.")
            st.session_state.pop("adm_schema_key", None)
            st.session_state[f"adm_chk_{tbl_sel}"] = False
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar: {e}")


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
            st.error(f"Error: {e}")


# ------------------------------------------------------------------
# Tab 2: Catálogos activos
# ------------------------------------------------------------------
def _tab_activos() -> None:
    search = st.text_input(
        "Buscar", placeholder="Nombre, base de datos o tabla...",
        key="adm_ac_search", label_visibility="collapsed"
    )

    try:
        catalogs = get_active_catalogs()
    except Exception as e:
        st.error(f"Error al cargar catálogos: {e}")
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

    for cat in catalogs:
        cid = cat["catalog_id"]
        try:
            permisos = get_catalog_permissions(cid)
        except Exception:
            permisos = []

        _icon_user  = '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="#6B7280" style="vertical-align:middle;margin-right:2px;"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>'
        _icon_role  = '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="#6B7280" style="vertical-align:middle;margin-right:2px;"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/></svg>'
        perm_text = ", ".join(
            f"{_icon_user if p['tipo'] == 'usuario' else _icon_role}{p['valor']}"
            for p in permisos
        ) or "Todos los publicadores"

        manage_key  = f"adm_ac_perm_{cid}"
        confirm_key = f"adm_ac_conf_{cid}"

        col_card, col_btns = st.columns([5, 2])

        with col_card:
            st.markdown(f"""
            <div style="padding:12px 16px; background:var(--secondary-background-color);
                        border-radius:10px; margin-bottom:4px;
                        border-left:3px solid #534AB7;">
                <div style="font-weight:600; font-size:14px;">{cat['nombre']}</div>
                <div style="font-size:12px; color:#6B7280; margin-top:4px; display:flex; align-items:center; gap:4px; flex-wrap:wrap;">
                    <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="#6B7280"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
                    <b>{cat['proyecto']}</b>
                    <span style="margin:0 4px;">|</span>
                    <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#6B7280" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4.03 3-9 3S3 13.66 3 12"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/></svg>
                    <code>{cat['base_datos']}.{cat['tabla_destino']}</code>
                </div>
                <div style="margin-top:6px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                    <span style="background:#E0E7FF;color:#3730A3;padding:1px 8px;
                                 border-radius:20px;font-size:11px;">{cat['estrategia'].upper()}</span>
                    <span style="background:#D1FAE5;color:#065F46;padding:1px 8px;
                                 border-radius:20px;font-size:11px;">{cat['destino'].upper()}</span>
                    <span style="font-size:11px;color:#6B7280;">Acceso: {perm_text}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with col_btns:
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            if st.button("Permisos", key=f"btn_perm_{cid}", use_container_width=True):
                st.session_state[manage_key] = not st.session_state.get(manage_key, False)
                st.rerun()
            if st.button("Desactivar", key=f"btn_deact_{cid}", use_container_width=True):
                st.session_state[confirm_key] = True

        # Panel de permisos inline
        if st.session_state.get(manage_key):
            with st.container():
                st.markdown(f"""
                <div style="padding:10px 14px; background:#F0F4FF;
                            border:1px solid #C7D2FE; border-radius:8px; margin-bottom:8px;">
                    <span style="font-size:13px; font-weight:600; color:#1C2F6E;">
                        Permisos — {cat['nombre']}
                    </span>
                </div>
                """, unsafe_allow_html=True)
                pk = f"ac_{cid}"
                _render_permisos_selector(pk, current=permisos)
                bc1, bc2 = st.columns(2)
                with bc1:
                    if st.button("Guardar", type="primary", key=f"save_perm_{cid}",
                                 use_container_width=True):
                        try:
                            save_permissions(cid, _collect_permisos(pk))
                            st.success("Permisos actualizados.")
                            st.session_state[manage_key] = False
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
                with bc2:
                    if st.button("Cancelar", key=f"cancel_perm_{cid}",
                                 use_container_width=True):
                        st.session_state[manage_key] = False
                        st.rerun()

        # Confirmación de desactivación
        if st.session_state.get(confirm_key):
            st.warning(
                f"¿Desactivar **{cat['nombre']}**? "
                "Los publicadores perderán acceso inmediatamente."
            )
            dc1, dc2 = st.columns(2)
            with dc1:
                if st.button("Confirmar", type="primary", key=f"yes_deact_{cid}",
                             use_container_width=True):
                    try:
                        deactivate_catalog(cid)
                        st.session_state.pop(confirm_key, None)
                        st.success("Catálogo desactivado.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
            with dc2:
                if st.button("Cancelar", key=f"no_deact_{cid}", use_container_width=True):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()


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
