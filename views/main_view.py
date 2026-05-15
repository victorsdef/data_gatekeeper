"""
views/main_view.py
Vista principal del portal: sidebar + flujo de carga en 4 pasos.
"""
from __future__ import annotations
import streamlit as st
import pandas as pd
from typing import Optional
import base64
import io
import zipfile
from pathlib import Path

from dg_validators.engine import validate_dataframe, ValidationResult
from utils.file_handler import read_uploaded_file, get_file_stats, detect_file_delimiter, get_excel_sheets
from utils.report_builder import build_error_report
from utils.db_writer import execute_load
from config.catalogs import get_proyectos_list, get_catalogs_by_project
from config.settings import MAX_FILE_SIZE_MB


def _skeleton_html(n: int = 4, dark: bool = False) -> str:
    css = "skel-line-dark" if dark else "skel-line"
    widths = ["long", "medium", "short", "long", "medium"]
    lines = "".join(
        f'<div class="{css} {widths[i % len(widths)]}"></div>'
        for i in range(n)
    )
    return f'<div style="padding:6px 0">{lines}</div>'


def _logo_b64() -> str:
    logo = Path(__file__).parent.parent / "assets" / "logo.png"
    return base64.b64encode(logo.read_bytes()).decode() if logo.exists() else ""


def _build_audit_payload(files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    if not files:
        return b"", "archivo"
    if len(files) == 1:
        return files[0][1], files[0][0]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_name, file_bytes in files:
            zf.writestr(file_name, file_bytes)
    return buffer.getvalue(), "archivos_originales.zip"


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
            <div style="font-size:10px;color:#7A8EC0;margin-left:48px;">Portal de Ingesta · v1.0</div>
        </div>
        """, unsafe_allow_html=True)

        st.divider()

        # Info usuario
        is_admin  = user.get("rol") == "Admin"
        rol_color = "#F5A800" if is_admin else "#6EE7B7"
        rol_bg    = "rgba(245,168,0,0.18)" if is_admin else "rgba(110,231,183,0.15)"
        st.markdown(f"""
        <div style="padding:12px; background:rgba(255,255,255,0.07);
                    border-radius:10px; margin-bottom:16px;
                    border:1px solid rgba(255,255,255,0.10);">
            <div style="font-weight:600; font-size:13px; color:white;">
                {user.get('nombre', 'Usuario')}
            </div>
            <div style="font-size:11px; color:#A8B4D8; margin-top:2px;">
                {user.get('email', '')}
            </div>
            <span style="
                display:inline-block; margin-top:8px;
                background:{rol_bg}; color:{rol_color};
                font-size:11px; font-weight:600;
                padding:2px 10px; border-radius:20px;
                border:1px solid {rol_color}40;
            ">{user.get('rol', 'Publicador')}</span>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("**Seleccionar catálogo**")

        # Proyectos desde catalogos_config
        ph1 = st.empty()
        ph1.markdown(_skeleton_html(3, dark=True), unsafe_allow_html=True)
        try:
            proyectos = get_proyectos_list()
            ph1.empty()
        except Exception as e:
            ph1.empty()
            st.error(f"Sin conexión a la base de datos: {e}")
            _render_sidebar_footer(user)
            return

        if not proyectos:
            st.info("No hay catálogos configurados. Contacta al administrador.")
            _render_sidebar_footer(user)
            return

        proy_options = {p["id"]: p["nombre"] for p in proyectos}
        proy_sel_id = st.selectbox(
            "Proyecto",
            options=list(proy_options.keys()),
            format_func=lambda x: proy_options[x],
            key="sidebar_proyecto_sel",
        )

        # Catálogos del proyecto filtrados por permisos del usuario
        ph2 = st.empty()
        ph2.markdown(_skeleton_html(3, dark=True), unsafe_allow_html=True)
        try:
            catalogs = get_catalogs_by_project(
                proy_sel_id,
                username=user.get("username", ""),
                rol=user.get("rol", "Publicador"),
            )
            ph2.empty()
        except Exception as e:
            ph2.empty()
            st.error(f"Error al cargar catálogos: {e}")
            _render_sidebar_footer(user)
            return

        if not catalogs:
            st.warning(f"No hay catálogos en '{proy_options[proy_sel_id]}'.")
            _render_sidebar_footer(user)
            return

        cat_options = {c["catalog_id"]: c["nombre"] for c in catalogs}
        cat_sel_id = st.selectbox(
            "Catálogo",
            options=list(cat_options.keys()),
            format_func=lambda x: cat_options[x],
            key="sidebar_catalog_sel",
        )

        selected_cat = next((c for c in catalogs if c["catalog_id"] == cat_sel_id), None)
        if not selected_cat:
            return

        # Resetear flujo si el catálogo cambió
        if st.session_state.get("sidebar_catalog_key") != cat_sel_id:
            for k in ["uploaded_df", "uploaded_audit_bytes", "uploaded_audit_name", "uploaded_name",
                      "validation_result", "carga_ejecutada", "load_result"]:
                st.session_state.pop(k, None)
            st.session_state.current_step      = "upload"
            st.session_state.sidebar_catalog_key = cat_sel_id

        estrategia = selected_cat["estrategia"]
        destino    = selected_cat["destino"]
        schema     = selected_cat.get("schema", {})

        # Info del catálogo seleccionado (solo lectura)
        st.markdown(f"""
        <div style="margin-top:12px; padding:10px 12px;
                    background:rgba(255,255,255,0.07);
                    border:1px solid rgba(255,255,255,0.10);
                    border-radius:8px; font-size:12px; color:#A8B4D8;">
            <div style="color:white; font-weight:500; margin-bottom:6px;">
                <code style="color:#F5A800; background:rgba(245,168,0,0.12);
                    padding:2px 6px; border-radius:4px; font-size:11px;">
                    {selected_cat['base_datos']}.{selected_cat['tabla_destino']}
                </code>
            </div>
            <div>
                <span style="background:rgba(245,168,0,0.18); color:#F5A800;
                    padding:1px 8px; border-radius:20px; font-size:11px;
                    border:1px solid rgba(245,168,0,0.3);
                    margin-right:6px;">{estrategia.upper()}</span>
                <span style="background:rgba(110,231,183,0.15); color:#6EE7B7;
                    padding:1px 8px; border-radius:20px; font-size:11px;
                    border:1px solid rgba(110,231,183,0.3);">{destino.upper()}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Columnas esperadas
        with st.expander("Ver columnas esperadas", expanded=False):
            cols = schema.get("columnas", [])
            if cols:
                for col in cols:
                    nullable_tag = (
                        "<span style='color:#6EE7B7; font-size:10px;'>nullable</span>"
                        if col.get("nullable") else ""
                    )
                    st.markdown(
                        f"<div style='font-size:12px; padding:4px 6px; display:flex;"
                        f"justify-content:space-between; align-items:center;"
                        f"border-bottom:1px solid rgba(255,255,255,0.06);'>"
                        f"<span style='color:#F5A800; font-family:monospace; font-size:11px;'>{col['nombre']}</span>"
                        f"<span style='color:#A8B4D8; font-size:10px;'>{col['tipo']} {nullable_tag}</span></div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("Sin esquema configurado.")

        st.session_state.selected_catalog = {
            "catalog_id":    cat_sel_id,
            "nombre":        selected_cat["nombre"],
            "base_datos":    selected_cat["base_datos"],
            "tabla_destino": selected_cat["tabla_destino"],
            "estrategia":    estrategia,
            "destino":       destino,
            "schema":        schema,
        }
        st.session_state.selected_project_id = proy_sel_id

        _render_sidebar_footer(user)


def _render_sidebar_footer(user: dict) -> None:
    st.divider()
    if user.get("rol") == "Admin":
        if st.button("Administrar catálogos", use_container_width=True, key="btn_admin"):
            st.session_state.current_view = "admin"
            st.rerun()
    if st.button("Historial de cargas", use_container_width=True, key="btn_history"):
        st.session_state.current_view = "history"
        st.rerun()
    if st.button("Cerrar sesión", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# ------------------------------------------------------------------
# Contenido principal
# ------------------------------------------------------------------
def _render_main_content() -> None:
    catalog = st.session_state.get("selected_catalog")

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
        st.markdown("#### Subir archivos")

        uploaded_files = st.file_uploader(
            label="Arrastra uno o más archivos aquí o haz clic para seleccionar",
            type=["csv", "txt", "xlsx", "xls"],
            key="file_uploader",
            help="Máximo 50 MB por archivo. Todos deben tener el mismo formato y columnas.",
            accept_multiple_files=True,
        )

        if uploaded_files:
            first_bytes = uploaded_files[0].read()
            uploaded_files[0].seek(0)
            ext = uploaded_files[0].name.rsplit(".", 1)[-1].lower()
            is_excel = ext in ("xlsx", "xls")

            oversized = []
            max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
            for uf in uploaded_files:
                uf.seek(0, 2)
                file_size = uf.tell()
                uf.seek(0)
                if file_size > max_bytes:
                    oversized.append(f"{uf.name} ({file_size / 1024 / 1024:.1f} MB)")

            if oversized:
                st.error(
                    "Estos archivos exceden el límite permitido de "
                    f"{MAX_FILE_SIZE_MB} MB: {', '.join(oversized)}"
                )
                st.session_state.uploaded_df = None
                return

            if not is_excel:
                enc = st.session_state.get("encoding", "utf-8")
                detected = detect_file_delimiter(first_bytes, enc)

                st.success(
                    f"Separador detectado: **{_DELIM_LABEL.get(detected, detected)}**  "
                    f"— se aplicará a todos los archivos."
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
                sheets = get_excel_sheets(first_bytes)
                if len(sheets) <= 1:
                    st.success(
                        f"Excel detectado — hoja **{sheets[0] if sheets else 'Sheet1'}** "
                        f"se leerá en todos los archivos."
                    )
                else:
                    with st.expander("¿Quieres leer otra hoja? Seleccionar"):
                        st.caption("Se aplicará la misma hoja a todos los archivos Excel.")
                        st.selectbox(
                            "Hoja a leer",
                            options=sheets,
                            key="sheet_name",
                            help="Selecciona la hoja que contiene los datos del catálogo.",
                        )

            dfs = []
            original_files = []
            errores_lectura = []
            selected_sheet_name = st.session_state.get("sheet_name") if is_excel else None

            for uf in uploaded_files:
                fb = uf.read()
                original_files.append((uf.name, fb))
                df_i, err_i = read_uploaded_file(
                    file_bytes=fb,
                    filename=uf.name,
                    delimiter=st.session_state.get("delimiter", ","),
                    encoding=st.session_state.get("encoding", "utf-8"),
                    sheet_name=selected_sheet_name,
                )
                if err_i:
                    errores_lectura.append(f"**{uf.name}**: {err_i}")
                elif df_i is None or df_i.empty:
                    errores_lectura.append(f"**{uf.name}**: archivo vacío o separador incorrecto.")
                else:
                    dfs.append((uf.name, df_i, len(fb)))

            if errores_lectura:
                for msg in errores_lectura:
                    st.error(msg)
                st.session_state.uploaded_df = None
                return

            if len(dfs) > 1:
                st.markdown("**Archivos cargados**")
                for fname, df_i, size_i in dfs:
                    size_str = f"{size_i/1024:.1f} KB" if size_i < 1024*1024 else f"{size_i/1024/1024:.1f} MB"
                    st.markdown(
                        f"<div style='font-size:12px; padding:4px 8px; background:var(--secondary-background-color);"
                        f"border-radius:6px; margin-bottom:4px;'>"
                        f"<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' style='vertical-align:middle;margin-right:4px;'><path d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'/><polyline points='14 2 14 8 20 8'/></svg><b>{fname}</b> — {len(df_i):,} filas · {size_str}</div>",
                        unsafe_allow_html=True,
                    )

            combined_df   = pd.concat([d for _, d, _ in dfs], ignore_index=True)
            combined_name = " + ".join(fname for fname, _, _ in dfs)
            total_bytes   = sum(len(file_bytes) for _, file_bytes in original_files)
            audit_bytes, audit_name = _build_audit_payload(original_files)

            stats = get_file_stats(combined_df, b"x" * total_bytes)
            st.session_state.uploaded_df          = combined_df
            st.session_state.uploaded_audit_bytes = audit_bytes
            st.session_state.uploaded_audit_name  = audit_name
            st.session_state.uploaded_name        = combined_name

            c1, c2, c3 = st.columns(3)
            c1.metric("Filas totales", f"{stats['filas']:,}")
            c2.metric("Columnas",      stats["columnas"])
            c3.metric("Archivos",      len(dfs))

            if stats["null_count"] > 0:
                st.warning(f"{stats['null_count']} celdas vacías detectadas en el conjunto combinado.")

    with col2:
        if st.session_state.get("uploaded_df") is not None:
            st.markdown("#### Previsualización")
            st.dataframe(
                st.session_state.uploaded_df.head(20),
                use_container_width=True,
                height=320,
            )
            st.caption(f"Primeras {min(20, len(st.session_state.uploaded_df))} filas del total combinado")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    if st.session_state.get("uploaded_df") is not None:
        if st.button("Continuar → Validar datos", type="primary", key="btn_to_validate"):
            st.session_state.current_step      = "validate"
            st.session_state.validation_result = None
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
            st.session_state.current_step      = "upload"
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
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#065F46" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><circle cx="12" cy="12" r="10"/><polyline points="9 12 11 14 15 10"/></svg>
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
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#7F1D1D" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
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
# Paso 3: Resultado
# ------------------------------------------------------------------
def _render_result_step(catalog: dict) -> None:
    from datetime import datetime

    df: Optional[pd.DataFrame] = st.session_state.get("uploaded_df")
    filename   = st.session_state.get("uploaded_audit_name") or st.session_state.get("uploaded_name", "archivo")
    file_bytes = st.session_state.get("uploaded_audit_bytes") or b""
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

    st.markdown(f"""
    <div style="
        padding:28px 32px; background:#F0FDF4;
        border:1px solid #86EFAC; border-radius:14px;
        text-align:center; margin-bottom:24px;
    ">
        <div style="margin-bottom:12px;"><svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#14532D" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="9 12 11 14 15 10"/></svg></div>
        <div style="font-weight:600; font-size:18px; color:#14532D;">
            Carga completada exitosamente
        </div>
        <div style="font-size:13px; color:#166534; margin-top:8px;">
            Los datos fueron insertados en <code>{catalog["tabla_destino"]}</code>.
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
        "estado_carga":     "Exito",
        "zip_auditoria":    load_result.get("zip_path") or "—",
    }, expanded=True)

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
    if st.button("Nueva carga", type="primary", key="btn_nueva_carga"):
        for key in ["uploaded_df", "uploaded_audit_bytes", "uploaded_audit_name", "uploaded_name",
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

        /* Sidebar */
        section[data-testid="stSidebar"] {
            background: #1C2F6E !important;
            border-right: none;
            padding-top: 20px;
        }
        section[data-testid="stSidebar"] .block-container {
            padding-top: 12px !important;
        }
        section[data-testid="stSidebar"] * {
            color: #E8ECF8 !important;
        }
        section[data-testid="stSidebar"] .stSelectbox label,
        section[data-testid="stSidebar"] .stTextInput label {
            color: #A8B4D8 !important;
            font-size: 12px !important;
        }
        section[data-testid="stSidebar"] input {
            background: #243580 !important;
            border: 1px solid #3A4E8C !important;
            color: white !important;
            border-radius: 8px !important;
        }
        section[data-testid="stSidebar"] input::placeholder {
            color: #7A8EC0 !important;
        }
        section[data-testid="stSidebar"] [data-baseweb="select"] > div {
            background: #243580 !important;
            border: 1px solid #3A4E8C !important;
            border-radius: 8px !important;
            color: white !important;
        }
        section[data-testid="stSidebar"] hr {
            border-color: rgba(255,255,255,0.12) !important;
        }

        /* Expander dentro del sidebar */
        section[data-testid="stSidebar"] div[data-testid="stExpander"] {
            background: rgba(255,255,255,0.06) !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
            border-radius: 8px !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stExpander"] summary {
            color: #A8B4D8 !important;
            font-size: 12px !important;
        }

        @keyframes skel-shimmer {
            0%   { background-position: -600px 0; }
            100% { background-position:  600px 0; }
        }
        .skel-line {
            background: linear-gradient(90deg, #EAECF4 25%, #D8DCF0 50%, #EAECF4 75%);
            background-size: 1200px 100%;
            animation: skel-shimmer 1.5s ease infinite;
            border-radius: 4px; height: 13px; margin-bottom: 10px;
        }
        .skel-line-dark {
            background: linear-gradient(90deg, #2A3F7A 25%, #364EA0 50%, #2A3F7A 75%);
            background-size: 1200px 100%;
            animation: skel-shimmer 1.5s ease infinite;
            border-radius: 4px; height: 12px; margin-bottom: 8px;
        }
        .skel-line.long,   .skel-line-dark.long   { width: 88%; }
        .skel-line.medium, .skel-line-dark.medium  { width: 60%; }
        .skel-line.short,  .skel-line-dark.short   { width: 35%; }

        /* Botones en sidebar */
        section[data-testid="stSidebar"] button[kind="primary"],
        section[data-testid="stSidebar"] button[kind="secondary"] {
            background: rgba(255,255,255,0.10) !important;
            border: 1px solid rgba(255,255,255,0.20) !important;
            color: white !important;
            border-radius: 8px !important;
            font-weight: 500 !important;
        }
        section[data-testid="stSidebar"] button[kind="primary"]:hover,
        section[data-testid="stSidebar"] button[kind="secondary"]:hover {
            background: rgba(255,255,255,0.18) !important;
        }

        /* Botón Administrar catálogos — acento amarillo */
        section[data-testid="stSidebar"] button[kind="primary"] {
            border-color: rgba(245,168,0,0.5) !important;
            color: #F5A800 !important;
        }

        /* Línea amarilla top del sidebar */
        section[data-testid="stSidebar"]::before {
            content: '';
            display: block;
            height: 4px;
            background: linear-gradient(90deg, #F5A800, #E52422);
            position: absolute;
            top: 0; left: 0; right: 0;
        }

        /* Métricas */
        div[data-testid="metric-container"] {
            background: white;
            border: 1px solid #D1D9F0;
            border-radius: 10px;
            padding: 12px 16px;
        }

        /* File uploader */
        div[data-testid="stFileUploader"] {
            border: 2px dashed #A8B4D8 !important;
            border-radius: 12px !important;
            background: #F7F9FF !important;
            padding: 16px !important;
        }
        div[data-testid="stFileUploader"]:hover {
            border-color: #1C2F6E !important;
            background: #EEF1F8 !important;
        }

        /* Tabla */
        .stDataFrame {
            border-radius: 10px;
            border: 1px solid #D1D9F0;
            overflow: hidden;
        }

        /* Expander */
        div[data-testid="stExpander"] {
            border: 1px solid #D1D9F0 !important;
            border-radius: 8px !important;
        }

        /* Botón primario */
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
