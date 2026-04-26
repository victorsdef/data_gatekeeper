"""
views/admin_catalogs_view.py
Panel de administración de catálogos — solo accesible para rol Admin.
Permite explorar bases de datos, seleccionar tablas, revisar su esquema
y registrar el catálogo directamente en catalogos_config.
"""
from __future__ import annotations
import base64
import json
import re
from pathlib import Path
import streamlit as st

def _logo_b64() -> str:
    logo = Path(__file__).parent.parent / "assets" / "logo.png"
    return base64.b64encode(logo.read_bytes()).decode() if logo.exists() else ""

from utils.db_admin import (
    get_all_databases,
    get_tables_from_db,
    get_mapped_tables,
    describe_table,
    build_schema_json,
    get_all_projects,
    get_active_catalogs,
    save_catalog_config,
    deactivate_catalog,
)

_ESTRATEGIAS = ["overwrite", "append", "reproceso"]
_DESTINOS    = ["singlestore", "hive"]
_TIPOS       = ["str", "int", "float", "bool"]


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
        if st.button("← Volver a carga de archivos", use_container_width=True):
            st.session_state.current_view = "upload"
            st.rerun()

    st.markdown("""
    <div style="padding: 8px 0 24px;">
        <h2 style="font-size:20px; font-weight:600; margin:0; color:var(--text-color);">
            Administración de catálogos
        </h2>
        <p style="font-size:13px; color:#6B7280; margin-top:4px;">
            Explora la base de datos, selecciona una tabla y regístrala como catálogo de ingesta.
        </p>
    </div>
    """, unsafe_allow_html=True)

    tab_crear, tab_listar = st.tabs(["Registrar catálogo", "Catálogos activos"])

    with tab_crear:
        _render_crear_tab()

    with tab_listar:
        _render_listar_tab()


# ------------------------------------------------------------------
# Tab 1: Crear / registrar catálogo
# ------------------------------------------------------------------
def _render_crear_tab() -> None:
    col_izq, col_der = st.columns([1, 1], gap="large")

    with col_izq:
        st.markdown("##### 1. Seleccionar tabla origen")

        # Selector de base de datos
        try:
            databases = get_all_databases()
        except Exception as e:
            st.error(f"No se pudo conectar a SingleStore: {e}")
            return

        if not databases:
            st.warning("No hay bases de datos disponibles (verifica permisos del usuario).")
            return

        db_sel = st.selectbox("Base de datos", options=databases, key="admin_db_sel")

        # Selector de tabla — excluye las ya registradas en catalogos_config
        try:
            all_tables    = get_tables_from_db(db_sel)
            mapped_tables = get_mapped_tables()
        except Exception as e:
            st.error(f"Error al listar tablas de `{db_sel}`: {e}")
            return

        available = [t for t in all_tables if (db_sel, t) not in mapped_tables]
        already   = [t for t in all_tables if (db_sel, t) in mapped_tables]

        if already:
            st.caption(f"{len(already)} tabla(s) ya registrada(s): {', '.join(already)}")

        if not available:
            st.success(f"Todas las tablas de `{db_sel}` ya están registradas como catálogos.")
            return

        tbl_sel = st.selectbox("Tabla", options=available, key="admin_tbl_sel")

        # Botón para cargar el esquema
        if st.button("Consultar esquema", type="primary", use_container_width=True, key="btn_describe"):
            try:
                rows = describe_table(db_sel, tbl_sel)
                st.session_state.admin_describe_rows = rows
                st.session_state.admin_schema_json   = build_schema_json(rows)
                st.session_state.admin_catalog_id    = f"{db_sel}__{tbl_sel}".lower()
            except Exception as e:
                st.error(f"Error al consultar esquema: {e}")
                return

        # Mostrar columnas detectadas
        describe_rows = st.session_state.get("admin_describe_rows")
        if describe_rows:
            st.markdown("**Columnas detectadas**")
            schema = st.session_state.get("admin_schema_json", {})
            columnas = schema.get("columnas", [])

            for i, col in enumerate(columnas):
                c1, c2, c3 = st.columns([3, 2, 2])
                with c1:
                    st.markdown(
                        f"<div style='padding:6px 0; font-size:13px;'>"
                        f"<code style='color:#534AB7'>{col['nombre']}</code></div>",
                        unsafe_allow_html=True,
                    )
                with c2:
                    nuevo_tipo = st.selectbox(
                        "Tipo",
                        options=_TIPOS,
                        index=_TIPOS.index(col["tipo"]) if col["tipo"] in _TIPOS else 0,
                        key=f"admin_tipo_{i}",
                        label_visibility="collapsed",
                    )
                    columnas[i]["tipo"] = nuevo_tipo
                with c3:
                    nullable = st.checkbox(
                        "Nullable",
                        value=col["nullable"],
                        key=f"admin_null_{i}",
                    )
                    columnas[i]["nullable"] = nullable

            schema["columnas"] = columnas
            st.session_state.admin_schema_json = schema

    with col_der:
        if not st.session_state.get("admin_describe_rows"):
            st.info("Selecciona una base de datos y tabla, luego haz clic en **Consultar esquema**.")
            return

        st.markdown("##### 2. Configurar catálogo")

        # Proyectos
        try:
            projects = get_all_projects()
        except Exception as e:
            st.error(f"Error al cargar proyectos: {e}")
            return

        if not projects:
            st.warning("No hay proyectos registrados. Crea al menos uno en la BD.")
            return

        proj_nombres = [p["nombre"] for p in projects]
        proj_ids     = [p["id"]     for p in projects]
        proj_idx = st.selectbox(
            "Proyecto",
            options=range(len(proj_nombres)),
            format_func=lambda i: proj_nombres[i],
            key="admin_proj_idx",
        )
        selected_project_id = proj_ids[proj_idx]

        catalog_id = st.text_input(
            "ID del catálogo",
            value=st.session_state.get("admin_catalog_id", ""),
            key="admin_catalog_id_input",
            help="Identificador único. Se auto-genera como base_datos__tabla, pero puedes cambiarlo.",
        )

        nombre = st.text_input(
            "Nombre del catálogo",
            value=st.session_state.get("admin_tbl_sel", "").replace("_", " ").title(),
            key="admin_nombre",
        )

        descripcion = st.text_area(
            "Descripción (opcional)",
            key="admin_descripcion",
            height=68,
        )

        c1, c2 = st.columns(2)
        with c1:
            estrategia = st.selectbox("Estrategia de ingesta", options=_ESTRATEGIAS, key="admin_estrategia")
        with c2:
            destino = st.selectbox("Destino", options=_DESTINOS, key="admin_destino")

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # Preview del schema_json
        with st.expander("Ver schema_json generado"):
            st.json(st.session_state.get("admin_schema_json", {}))

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # Validaciones antes de guardar
        errores = []
        if not catalog_id.strip():
            errores.append("El ID del catálogo no puede estar vacío.")
        if not nombre.strip():
            errores.append("El nombre del catálogo no puede estar vacío.")
        if not re.match(r"^[\w]+$", catalog_id.strip()):
            errores.append("El ID solo puede contener letras, números y guiones bajos.")

        if errores:
            for e in errores:
                st.warning(e)

        if st.button("Guardar catálogo", type="primary", use_container_width=True,
                     key="btn_guardar_catalog", disabled=bool(errores)):
            try:
                db_sel  = st.session_state.get("admin_db_sel", "")
                tbl_sel = st.session_state.get("admin_tbl_sel", "")
                save_catalog_config(
                    catalog_id    = catalog_id.strip(),
                    project_id    = selected_project_id,
                    nombre        = nombre.strip(),
                    descripcion   = descripcion.strip(),
                    base_datos    = db_sel,
                    tabla_destino = tbl_sel,
                    destino       = destino,
                    estrategia    = estrategia,
                    schema_json   = st.session_state.get("admin_schema_json", {}),
                )
                st.success(f"Catálogo **{nombre.strip()}** registrado correctamente.")
                # Limpiar estado del formulario
                for key in ["admin_describe_rows", "admin_schema_json", "admin_catalog_id"]:
                    st.session_state.pop(key, None)
                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar: {e}")


# ------------------------------------------------------------------
# Tab 2: Listar catálogos activos
# ------------------------------------------------------------------
def _render_listar_tab() -> None:
    st.markdown("##### Catálogos registrados")

    try:
        catalogs = get_active_catalogs()
    except Exception as e:
        st.error(f"Error al cargar catálogos: {e}")
        return

    if not catalogs:
        st.info("No hay catálogos activos registrados.")
        return

    st.markdown(
        f"<div style='font-size:13px; color:#6B7280; margin-bottom:12px;'>"
        f"{len(catalogs)} catálogo(s) activo(s)</div>",
        unsafe_allow_html=True,
    )

    for cat in catalogs:
        with st.container():
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"""
                <div style="
                    padding:12px 16px;
                    background:var(--secondary-background-color);
                    border-radius:10px;
                    margin-bottom:8px;
                    border-left: 3px solid #534AB7;
                ">
                    <div style="font-weight:600; font-size:14px;">{cat['nombre']}</div>
                    <div style="font-size:12px; color:#6B7280; margin-top:4px;">
                        <span style="margin-right:16px;">📁 <b>Proyecto:</b> {cat['proyecto']}</span>
                        <span style="margin-right:16px;">🗄 <b>BD:</b> {cat['base_datos']}</span>
                        <span><b>Tabla:</b> <code>{cat['tabla_destino']}</code></span>
                    </div>
                    <div style="font-size:12px; color:#6B7280; margin-top:4px;">
                        <span style="
                            background:#E0E7FF; color:#3730A3;
                            padding:1px 8px; border-radius:20px; font-size:11px;
                            margin-right:8px;
                        ">{cat['estrategia'].upper()}</span>
                        <span style="
                            background:#D1FAE5; color:#065F46;
                            padding:1px 8px; border-radius:20px; font-size:11px;
                        ">{cat['destino'].upper()}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
                if st.button("Desactivar", key=f"deact_{cat['catalog_id']}", type="secondary"):
                    st.session_state[f"confirm_deact_{cat['catalog_id']}"] = True

            # Confirmación antes de desactivar
            if st.session_state.get(f"confirm_deact_{cat['catalog_id']}"):
                st.warning(
                    f"¿Seguro que quieres desactivar **{cat['nombre']}**? "
                    f"Los usuarios no podrán subir archivos a este catálogo."
                )
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Confirmar", key=f"yes_deact_{cat['catalog_id']}", type="primary"):
                        try:
                            deactivate_catalog(cat["catalog_id"])
                            st.session_state.pop(f"confirm_deact_{cat['catalog_id']}", None)
                            st.success(f"Catálogo **{cat['nombre']}** desactivado.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
                with c2:
                    if st.button("Cancelar", key=f"no_deact_{cat['catalog_id']}"):
                        st.session_state.pop(f"confirm_deact_{cat['catalog_id']}", None)
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

        div[data-testid="stTabs"] button {
            font-size: 14px;
            font-weight: 500;
            color: #1C2F6E !important;
        }
        div[data-testid="stTabs"] button[aria-selected="true"] {
            color: #1C2F6E !important;
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
    </style>
    """, unsafe_allow_html=True)
