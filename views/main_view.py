"""
views/main_view.py
Vista principal del portal: sidebar + flujo de carga en 4 pasos.
"""
from __future__ import annotations
import streamlit as st
import pandas as pd
from typing import Optional

from config.catalogs import (
    get_proyectos_list,
    get_catalogs_by_project,
    get_catalog_by_id,
)
from dg_validators.engine import validate_dataframe, ValidationResult
from utils.file_handler import read_uploaded_file, get_file_stats, detect_file_delimiter, get_excel_sheets
from utils.report_builder import build_error_report
from utils.db_writer import execute_load


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
def render_main_app() -> None:
    _inject_main_css()
    _render_sidebar()
    _render_main_content()


# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------
def _render_sidebar() -> None:
    user = st.session_state.user_info or {}

    with st.sidebar:
        # Logo + título
        st.markdown("""
        <div style="display:flex; align-items:center; gap:10px; padding:4px 0 20px;">
            <div style="
                width:36px; height:36px; border-radius:10px;
                background:linear-gradient(135deg,#534AB7,#7F77DD);
                display:flex; align-items:center; justify-content:center;
                font-size:18px; flex-shrink:0;
            ">🛡</div>
            <div>
                <div style="font-weight:600; font-size:14px; color:var(--text-color);">Data Gatekeeper</div>
                <div style="font-size:11px; color:#9CA3AF;">v1.0</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.divider()

        # Info usuario
        rol_color = "#7C3AED" if user.get("rol") == "Admin" else "#0F6E56"
        rol_bg    = "#EDE9FE" if user.get("rol") == "Admin" else "#E1F5EE"
        st.markdown(f"""
        <div style="padding: 12px; background:var(--secondary-background-color);
                    border-radius:10px; margin-bottom:16px;">
            <div style="font-weight:500; font-size:13px; color:var(--text-color);">
                {user.get('nombre', 'Usuario')}
            </div>
            <div style="font-size:11px; color:#9CA3AF; margin-top:2px;">
                {user.get('email', '')}
            </div>
            <span style="
                display:inline-block; margin-top:8px;
                background:{rol_bg}; color:{rol_color};
                font-size:11px; font-weight:500;
                padding:2px 10px; border-radius:20px;
            ">{user.get('rol', 'Publicador')}</span>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("**Seleccionar destino**")

        # Selector de proyecto
        try:
            proyectos = get_proyectos_list()
        except Exception as exc:
            st.error(f"No se pudo cargar la lista de proyectos: {exc}")
            return

        if not proyectos:
            st.warning("No hay proyectos disponibles (revisa DEMO_MODE y la conexión a SingleStore).")
            return
        proyecto_nombres = [p["nombre"] for p in proyectos]
        proyecto_ids     = [p["id"]     for p in proyectos]

        proyecto_idx = st.selectbox(
            "Proyecto",
            options=range(len(proyecto_nombres)),
            format_func=lambda i: proyecto_nombres[i],
            key="proyecto_idx",
        )
        selected_project_id = proyecto_ids[proyecto_idx]

        # Selector de catálogo
        catalogs = get_catalogs_by_project(selected_project_id)
        if catalogs:
            cat_nombres = [c["nombre"]     for c in catalogs]
            cat_ids     = [c["catalog_id"] for c in catalogs]

            cat_idx = st.selectbox(
                "Catálogo",
                options=range(len(cat_nombres)),
                format_func=lambda i: cat_nombres[i],
                key="cat_idx",
            )
            selected_catalog = catalogs[cat_idx]

            # Metadata del catálogo seleccionado
            st.markdown(f"""
            <div style="
                margin-top:12px; padding:10px 12px;
                background:var(--secondary-background-color);
                border-radius:8px; font-size:12px; color:#6B7280;
            ">
                <div><b>Tabla destino:</b> {selected_catalog['tabla_destino']}</div>
                <div style="margin-top:4px;"><b>Estrategia:</b>
                    <span style="
                        background:#E0E7FF; color:#3730A3;
                        padding:1px 8px; border-radius:20px; font-size:11px;
                    ">{selected_catalog['estrategia'].upper()}</span>
                </div>
                <div style="margin-top:4px;"><b>Destino:</b>
                    <span style="
                        background:#D1FAE5; color:#065F46;
                        padding:1px 8px; border-radius:20px; font-size:11px;
                    ">{selected_catalog['destino'].upper()}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Columnas esperadas
            with st.expander("Ver columnas esperadas", expanded=False):
                schema_cols = selected_catalog.get("schema", {}).get("columnas", [])
                for col in schema_cols:
                    reglas_str = ", ".join(r["tipo"] for r in col.get("reglas", []))
                    st.markdown(f"""
                    <div style="font-size:12px; padding:3px 0; display:flex;
                                justify-content:space-between; align-items:center;">
                        <code style="color:#534AB7;">{col['nombre']}</code>
                        <span style="color:#9CA3AF;">{col['tipo']}</span>
                    </div>
                    {f'<div style="font-size:11px;color:#6B7280;padding-bottom:4px;">{reglas_str}</div>' if reglas_str else ''}
                    """, unsafe_allow_html=True)

            st.session_state.selected_project_id = selected_project_id
            st.session_state.selected_catalog    = selected_catalog
        else:
            st.warning("No hay catálogos en este proyecto.")

        st.divider()

        # Botón logout
        if st.button("Cerrar sesión", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# ------------------------------------------------------------------
# Contenido principal
# ------------------------------------------------------------------
def _render_main_content() -> None:
    catalog = st.session_state.get("selected_catalog")

    # Header
    st.markdown("""
    <div style="padding: 8px 0 24px;">
        <h2 style="font-size:20px; font-weight:600; margin:0; color:var(--text-color);">
            Subir catálogo
        </h2>
        <p style="font-size:13px; color:#6B7280; margin-top:4px;">
            Sube tu archivo, revisa la previsualización y ejecuta la validación antes de cargar.
        </p>
    </div>
    """, unsafe_allow_html=True)

    if not catalog:
        st.info("Selecciona un proyecto y catálogo en el panel izquierdo.")
        return

    # Paso indicator
    step = st.session_state.get("current_step", "upload")
    _render_step_indicator(step)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    if step == "upload":
        _render_upload_step(catalog)
    elif step == "validate":
        _render_validate_step(catalog)
    elif step == "result":
        _render_result_step(catalog)


def _render_step_indicator(current: str) -> None:
    steps = [
        ("upload",   "1", "Subir archivo"),
        ("validate", "2", "Validar"),
        ("result",   "3", "Resultado"),
    ]
    html = '<div style="display:flex; gap:0; margin-bottom:8px; align-items:center;">'
    for i, (key, num, label) in enumerate(steps):
        is_active = current == key
        is_done   = (
            (key == "upload"   and current in ("validate", "result")) or
            (key == "validate" and current == "result")
        )
        if is_active:
            circle_bg, circle_fg, text_col = "#534AB7", "white", "#534AB7"
            fw = "600"
        elif is_done:
            circle_bg, circle_fg, text_col = "#1D9E75", "white", "#1D9E75"
            fw = "500"
        else:
            circle_bg, circle_fg, text_col = "#E5E7EB", "#9CA3AF", "#9CA3AF"
            fw = "400"

        html += f"""
        <div style="display:flex; align-items:center; gap:8px;">
            <div style="
                width:28px; height:28px; border-radius:50%;
                background:{circle_bg}; color:{circle_fg};
                display:flex; align-items:center; justify-content:center;
                font-size:13px; font-weight:600; flex-shrink:0;
            ">{num if not is_done else '✓'}</div>
            <span style="font-size:13px; font-weight:{fw}; color:{text_col};">{label}</span>
        </div>
        """
        if i < len(steps) - 1:
            line_color = "#1D9E75" if is_done else "#E5E7EB"
            html += f'<div style="flex:1; height:1px; background:{line_color}; margin:0 12px;"></div>'

    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


# ------------------------------------------------------------------
# Paso 1: Upload
# ------------------------------------------------------------------
_DELIM_LABEL = {
    ",":  "Coma (,)",
    ";":  "Punto y coma (;)",
    "|":  "Pipe (|)",
    "\t": "Tabulador",
}
_DELIM_TIP = {
    ",":  "El más común en archivos generados fuera de Excel. Ej: `Juan,García,25`",
    ";":  "Común en Excel exportado en español. Ej: `Juan;García;25`",
    "|":  "Usado en algunos sistemas bancarios. Ej: `Juan|García|25`",
    "\t": "El archivo usa un espacio grande (Tab) entre cada dato.",
}
_ENC_LABEL = {
    "utf-8":      "UTF-8",
    "latin-1":    "Latin-1",
    "iso-8859-1": "ISO-8859-1",
    "cp1252":     "Windows-1252",
}
_ENC_TIP = {
    "utf-8":      "Estándar moderno. Funciona con la mayoría de archivos actuales.",
    "latin-1":    "Pruébalo si la ñ o las tildes aparecen con caracteres raros.",
    "iso-8859-1": "Similar a Latin-1, para archivos de sistemas europeos.",
    "cp1252":     "Para archivos generados por sistemas Windows o bancarios antiguos.",
}


def _render_upload_step(catalog: dict) -> None:
    col1, col2 = st.columns([3, 2])

    with col1:
        st.markdown("#### Subir archivo")

        uploaded_file = st.file_uploader(
            label="Arrastra tu archivo aquí o haz clic para seleccionar",
            type=["csv", "txt", "xlsx", "xls"],
            key="file_uploader",
            help="Máximo 50 MB. Formatos: CSV, TXT, Excel.",
        )

        if uploaded_file:
            file_bytes = uploaded_file.read()
            ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
            is_excel = ext in ("xlsx", "xls")

            # Detectar formato y mostrarlo al usuario
            if not is_excel:
                enc = st.session_state.get("encoding", "utf-8")
                detected = detect_file_delimiter(file_bytes, enc)
                selected = st.session_state.get("delimiter", ",")
                # Si el usuario no ha forzado nada, usar el detectado
                active_delim = selected if selected != "," else detected

                st.success(
                    f"Archivo detectado con separador: **{_DELIM_LABEL.get(detected, detected)}**  "
                    f"— el sistema lo leerá automáticamente."
                )

                with st.expander("¿Las columnas no se ven bien? Ajustar lectura"):
                    st.caption(
                        "Cambia estas opciones solo si la previsualización muestra "
                        "las columnas mezcladas o los caracteres con símbolos raros."
                    )
                    col_d, col_e = st.columns(2)
                    with col_d:
                        st.selectbox(
                            "Separador",
                            options=list(_DELIM_LABEL.keys()),
                            format_func=lambda x: _DELIM_LABEL[x],
                            key="delimiter",
                        )
                        cur_delim = st.session_state.get("delimiter", ",")
                        st.caption(_DELIM_TIP[cur_delim])
                        if cur_delim != "," and cur_delim != detected:
                            st.warning(
                                f"Detectamos **{_DELIM_LABEL[detected]}** en tu archivo. "
                                f"Cambiaste a **{_DELIM_LABEL[cur_delim]}** — úsalo solo si "
                                f"la previsualización sigue mostrando las columnas mezcladas."
                            )
                    with col_e:
                        st.selectbox(
                            "Codificación",
                            options=list(_ENC_LABEL.keys()),
                            format_func=lambda x: _ENC_LABEL[x],
                            key="encoding",
                        )
                        cur_enc = st.session_state.get("encoding", "utf-8")
                        st.caption(_ENC_TIP[cur_enc])
            else:
                sheets = get_excel_sheets(file_bytes)
                if len(sheets) <= 1:
                    st.success(
                        f"Archivo Excel detectado — hoja **{sheets[0] if sheets else 'Sheet1'}** "
                        f"se leerá automáticamente."
                    )
                else:
                    st.success(
                        f"Archivo Excel con **{len(sheets)} hojas** detectado — "
                        f"se leerá la primera hoja por defecto."
                    )
                    with st.expander("¿Quieres leer otra hoja? Seleccionar"):
                        st.caption(
                            "El archivo tiene más de una hoja. "
                            "Elige cuál contiene los datos que quieres cargar."
                        )
                        st.selectbox(
                            "Hoja a leer",
                            options=sheets,
                            key="sheet_name",
                            help="Selecciona la hoja que contiene los datos del catálogo.",
                        )

            df, error = read_uploaded_file(
                file_bytes=file_bytes,
                filename=uploaded_file.name,
                delimiter=st.session_state.get("delimiter", ","),
                encoding=st.session_state.get("encoding", "utf-8"),
            )

            if error:
                st.error(f"No se pudo leer el archivo: {error}")
                return

            if df is None or df.empty:
                st.error(
                    "El archivo está vacío o el separador es incorrecto. "
                    "Abre 'Ajustar lectura' y prueba con otro separador."
                )
                st.session_state.uploaded_df = None
                return

            stats = get_file_stats(df, file_bytes)
            st.session_state.uploaded_df    = df
            st.session_state.uploaded_bytes = file_bytes
            st.session_state.uploaded_name  = uploaded_file.name

            c1, c2, c3 = st.columns(3)
            c1.metric("Filas",    f"{stats['filas']:,}")
            c2.metric("Columnas", stats["columnas"])
            c3.metric("Tamaño",   stats["size_str"])

            if stats["null_count"] > 0:
                st.warning(f"{stats['null_count']} celdas vacías detectadas en el archivo.")

    with col2:
        if st.session_state.get("uploaded_df") is not None:
            st.markdown("#### Previsualización")
            st.dataframe(
                st.session_state.uploaded_df.head(20),
                use_container_width=True,
                height=320,
            )
            st.caption(f"Primeras {min(20, len(st.session_state.uploaded_df))} filas")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    if st.session_state.get("uploaded_df") is not None:
        if st.button("Continuar → Validar datos", type="primary", key="btn_to_validate"):
            st.session_state.current_step       = "validate"
            st.session_state.validation_result  = None
            st.rerun()


# ------------------------------------------------------------------
# Paso 2: Validación
# ------------------------------------------------------------------
def _render_validate_step(catalog: dict) -> None:
    df: Optional[pd.DataFrame] = st.session_state.get("uploaded_df")
    if df is None:
        st.warning("No hay archivo cargado. Vuelve al paso anterior.")
        if st.button("← Volver", key="btn_back_upload"):
            st.session_state.current_step = "upload"
            st.rerun()
        return

    schema_config = catalog.get("schema", {})
    result: Optional[ValidationResult] = st.session_state.get("validation_result")

    col1, col2 = st.columns([2, 3])

    with col1:
        st.markdown("#### Archivo a validar")
        st.markdown(f"""
        <div style="padding:14px; background:var(--secondary-background-color);
                    border-radius:10px; font-size:13px;">
            <div><b>Archivo:</b> {st.session_state.get('uploaded_name', '—')}</div>
            <div style="margin-top:6px;"><b>Filas:</b> {len(df):,}</div>
            <div style="margin-top:6px;"><b>Catálogo:</b> {catalog['nombre']}</div>
            <div style="margin-top:6px;"><b>Tabla:</b> <code>{catalog['tabla_destino']}</code></div>
            <div style="margin-top:6px;"><b>Estrategia:</b>
                <span style="
                    background:#E0E7FF; color:#3730A3;
                    padding:1px 8px; border-radius:20px; font-size:11px;
                ">{catalog['estrategia'].upper()}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        if st.button("Ejecutar validación", type="primary", use_container_width=True, key="btn_validate"):
            with st.spinner("Validando datos en memoria..."):
                result = validate_dataframe(df, schema_config)
                st.session_state.validation_result = result
            st.rerun()

        if st.button("← Volver al archivo", use_container_width=True, key="btn_back_v"):
            st.session_state.current_step = "upload"
            st.session_state.validation_result = None
            st.rerun()

    with col2:
        if result is not None:
            _render_validation_results(result, df, catalog)


def _render_validation_results(result: ValidationResult, df: pd.DataFrame, catalog: dict) -> None:
    if result.success:
        st.markdown("""
        <div style="
            padding:20px 24px; background:#ECFDF5;
            border:1px solid #6EE7B7; border-radius:12px;
            display:flex; align-items:center; gap:16px;
        ">
            <div style="font-size:32px;"></div>
            <div>
                <div style="font-weight:600; font-size:15px; color:#065F46;">
                    Validación exitosa
                </div>
                <div style="font-size:13px; color:#047857; margin-top:2px;">
                    Todas las filas pasaron el 100% de las validaciones.
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        c1.metric("Filas validadas", f"{result.rows_checked:,}")
        c2.metric("Errores encontrados", "0")

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        if st.button("Confirmar carga →", type="primary", use_container_width=True, key="btn_confirm"):
            st.session_state.current_step = "result"
            st.rerun()
    else:
        st.markdown(f"""
        <div style="
            padding:20px 24px; background:#FEF2F2;
            border:1px solid #FCA5A5; border-radius:12px;
            display:flex; align-items:center; gap:16px;
        ">
            <div style="font-size:32px;"></div>
            <div>
                <div style="font-weight:600; font-size:15px; color:#7F1D1D;">
                    Validación fallida — {result.error_count} error(es) encontrado(s)
                </div>
                <div style="font-size:13px; color:#B91C1C; margin-top:2px;">
                    Corrige el archivo y vuelve a intentarlo. No se cargó nada en la BD.
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.markdown("**Consola de errores**")

        errors_df = result.to_dataframe()
        st.dataframe(
            errors_df,
            use_container_width=True,
            height=280,
            column_config={
                "Fila":    st.column_config.NumberColumn("Fila", width="small"),
                "Columna": st.column_config.TextColumn("Columna", width="medium"),
                "Valor":   st.column_config.TextColumn("Valor encontrado"),
                "Regla":   st.column_config.TextColumn("Regla"),
                "Detalle": st.column_config.TextColumn("Detalle"),
            }
        )

        # Botón exportar errores
        user = st.session_state.get("user_info") or {}
        xlsx_bytes = build_error_report(
            result=result,
            catalog=catalog,
            filename=st.session_state.get("uploaded_name", "archivo"),
            username=user.get("username", "—"),
        )
        st.download_button(
            label="Descargar reporte de errores (.xlsx)",
            data=xlsx_bytes,
            file_name="reporte_errores_validacion.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ------------------------------------------------------------------
# Paso 3: Resultado / Confirmación
# ------------------------------------------------------------------
def _render_result_step(catalog: dict) -> None:
    from datetime import datetime

    df: Optional[pd.DataFrame] = st.session_state.get("uploaded_df")
    filename   = st.session_state.get("uploaded_name", "archivo")
    file_bytes = st.session_state.get("uploaded_bytes") or b""
    user       = st.session_state.user_info or {}
    project_id = st.session_state.get("selected_project_id", "")

    if not st.session_state.get("carga_ejecutada"):
        with st.spinner(f"Ejecutando {catalog['estrategia'].upper()} en {catalog['tabla_destino']}..."):
            load_result = execute_load(
                df=df,
                file_bytes=file_bytes,
                filename=filename,
                catalog=catalog,
                username=user.get("username", "—"),
                project_id=project_id,
            )
        st.session_state.carga_ejecutada = True
        st.session_state.load_result     = load_result
        st.rerun()

    load_result = st.session_state.get("load_result", {})
    success     = load_result.get("success", False)
    is_demo     = load_result.get("demo", False)

    if not success:
        st.markdown(f"""
        <div style="
            padding:28px 32px; background:#FEF2F2;
            border:1px solid #FCA5A5; border-radius:14px;
            text-align:center; margin-bottom:24px;
        ">
            <div style="font-size:48px; margin-bottom:12px;">❌</div>
            <div style="font-weight:600; font-size:18px; color:#7F1D1D;">
                Error al insertar en la base de datos
            </div>
            <div style="font-size:13px; color:#B91C1C; margin-top:8px;">
                {load_result.get("error", "Error desconocido")}
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("← Volver e intentar de nuevo", key="btn_back_error"):
            for key in ["carga_ejecutada", "load_result"]:
                st.session_state.pop(key, None)
            st.session_state.current_step = "upload"
            st.rerun()
        return

    demo_note = " (modo demo — BD no impactada)" if is_demo else ""
    st.markdown(f"""
    <div style="
        padding:28px 32px; background:#F0FDF4;
        border:1px solid #86EFAC; border-radius:14px;
        text-align:center; margin-bottom:24px;
    ">
        <div style="font-size:48px; margin-bottom:12px;">🎉</div>
        <div style="font-weight:600; font-size:18px; color:#14532D;">
            Carga completada exitosamente{demo_note}
        </div>
        <div style="font-size:13px; color:#166534; margin-top:8px;">
            {f'Los datos fueron insertados en <code>{catalog["tabla_destino"]}</code>.' if not is_demo
             else 'Validación OK. En producción los datos se insertarán en <code>' + catalog["tabla_destino"] + '</code>.'}
            {' El archivo original fue guardado en almacenamiento auditado.' if load_result.get("zip_path") else ''}
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Filas cargadas", f"{load_result.get('rows', 0):,}")
    col2.metric("Tabla destino",  catalog["tabla_destino"])
    col3.metric("Estrategia",     catalog["estrategia"].upper())
    col4.metric("Estado",         "Éxito ✓")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
    st.markdown("**Registro de auditoría**")
    st.json({
        "timestamp":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "usuario_ad":       user.get("username", "—"),
        "project_id":       project_id,
        "id_catalogo":      catalog["catalog_id"],
        "nombre_archivo":   filename,
        "filas_procesadas": load_result.get("rows", 0),
        "estrategia_usada": catalog["estrategia"],
        "destino":          catalog["destino"],
        "tabla_destino":    catalog["tabla_destino"],
        "estado_carga":     "Éxito",
        "zip_auditoria":    load_result.get("zip_path") or "—",
        "modo_demo":        is_demo,
    }, expanded=True)

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
    if st.button("Nueva carga", type="primary", key="btn_nueva_carga"):
        for key in ["uploaded_df", "uploaded_bytes", "uploaded_name",
                    "validation_result", "carga_ejecutada", "load_result"]:
            st.session_state.pop(key, None)
        st.session_state.current_step = "upload"
        st.rerun()


# ------------------------------------------------------------------
# CSS principal
# ------------------------------------------------------------------
def _inject_main_css() -> None:
    st.markdown("""
    <style>
        #MainMenu, footer, header { visibility: hidden; }
        .block-container { padding-top: 24px !important; padding-bottom: 24px !important; }
        section[data-testid="stSidebar"] {
            background: white;
            border-right: 1px solid #E5E7EB;
            padding-top: 20px;
        }
        section[data-testid="stSidebar"] .block-container {
            padding-top: 12px !important;
        }
        div[data-testid="metric-container"] {
            background: white;
            border: 1px solid #E5E7EB;
            border-radius: 10px;
            padding: 12px 16px;
        }
        div[data-testid="stFileUploader"] {
            border: 2px dashed #C4B5FD !important;
            border-radius: 12px !important;
            background: #FAFAFF !important;
            padding: 16px !important;
        }
        div[data-testid="stFileUploader"]:hover {
            border-color: #534AB7 !important;
            background: #F5F3FF !important;
        }
        .stDataFrame {
            border-radius: 10px;
            border: 1px solid #E5E7EB;
            overflow: hidden;
        }
        div[data-testid="stExpander"] {
            border: 1px solid #E5E7EB !important;
            border-radius: 8px !important;
        }
    </style>
    """, unsafe_allow_html=True)
