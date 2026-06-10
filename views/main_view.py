"""
views/main_view.py
Vista principal del portal: sidebar + flujo de carga en 4 pasos.
"""
from __future__ import annotations
import html
import os
import streamlit as st
import pandas as pd
from typing import Optional
import base64
import io
import zipfile
from pathlib import Path

from dg_validators.engine import validate_dataframe, ValidationResult
from storage.file_handler import read_uploaded_file, get_file_stats, detect_file_delimiter, get_excel_sheets
from reports.report_builder import build_error_report
from services.db_writer import execute_load, save_validation_failure_file
from utils.error_messages import user_facing_error
from config.catalogs import get_proyectos_list, get_catalogs_by_project
from config import settings

MAX_FILE_SIZE_MB = int(
    getattr(
        settings,
        "MAX_FILE_SIZE_MB",
        os.getenv("MAX_FILE_SIZE_MB", os.getenv("MAX_UPLOAD_SIZE_MB", 50)),
    )
)
MAX_ROWS_IN_MEMORY = int(
    getattr(
        settings,
        "MAX_ROWS_IN_MEMORY",
        os.getenv("MAX_ROWS_IN_MEMORY", 500000),
    )
)


def _missing_catalog_fields(catalog: dict | None) -> list[str]:
    if not catalog:
        return ["catalogo"]
    required = ["catalog_id", "nombre", "base_datos", "tabla_destino", "estrategia", "destino", "schema"]
    missing = []
    for field in required:
        value = catalog.get(field)
        if field == "schema":
            if not isinstance(value, dict) or not (value.get("columnas") or []):
                missing.append(field)
        elif value in (None, ""):
            missing.append(field)
    return missing


def _skeleton_html(n: int = 4, dark: bool = False) -> str:
    css = "skel-line-dark" if dark else "skel-line"
    widths = ["long", "medium", "short", "long", "medium"]
    lines = "".join(
        f'<div class="{css} {widths[i % len(widths)]}"></div>'
        for i in range(n)
    )
    return f'<div style="padding:6px 0">{lines}</div>'


def _preview_table_loading_html(rows: int = 8, cols: int = 4) -> str:
    header_cells = "".join("<th><div class='preview-skel-line short'></div></th>" for _ in range(cols))
    body_rows = []
    for row_idx in range(rows):
        cells = "".join(
            f"<td><div class='preview-skel-line {['long', 'medium', 'short'][col_idx % 3]}'></div></td>"
            for col_idx in range(cols)
        )
        body_rows.append(f"<tr>{cells}</tr>")

    return f"""
    <style>
      @keyframes previewShimmer {{
        0% {{ background-position: -500px 0; }}
        100% {{ background-position: 500px 0; }}
      }}
      .preview-loading-card {{
        border: 1px solid #D1D9F0;
        border-radius: 10px;
        background: #F8FAFF;
        overflow: hidden;
        min-height: 320px;
      }}
      .preview-loading-title {{
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 12px;
        color: #1C2F6E;
        font-size: 12px;
        font-weight: 700;
        border-bottom: 1px solid #E5E9F5;
      }}
      .preview-loading-dot {{
        width: 8px;
        height: 8px;
        border-radius: 999px;
        background: #F5A800;
        animation: previewPulse 0.9s ease-in-out infinite alternate;
      }}
      @keyframes previewPulse {{
        from {{ opacity: .35; transform: scale(.85); }}
        to {{ opacity: 1; transform: scale(1.15); }}
      }}
      .preview-skel-table {{
        width: 100%;
        border-collapse: collapse;
      }}
      .preview-skel-table th,
      .preview-skel-table td {{
        padding: 9px 10px;
        border-bottom: 1px solid #E5E9F5;
      }}
      .preview-skel-line {{
        height: 12px;
        border-radius: 999px;
        background: linear-gradient(90deg, #EAECF4 25%, #D8DCF0 50%, #EAECF4 75%);
        background-size: 1000px 100%;
        animation: previewShimmer 1.15s ease infinite;
      }}
      .preview-skel-line.long {{ width: 88%; }}
      .preview-skel-line.medium {{ width: 62%; }}
      .preview-skel-line.short {{ width: 38%; }}
    </style>
    <div class="preview-loading-card">
      <div class="preview-loading-title">
        <span class="preview-loading-dot"></span>
        Preparando previsualizacion de la tabla...
      </div>
      <table class="preview-skel-table">
        <thead><tr>{header_cells}</tr></thead>
        <tbody>{''.join(body_rows)}</tbody>
      </table>
    </div>
    """


def _logo_b64() -> str:
    logo = Path(__file__).parent.parent / "assets" / "logo.png"
    return base64.b64encode(logo.read_bytes()).decode() if logo.exists() else ""


def _nav_icon_b64(filename: str) -> str:
    path = Path(__file__).parent.parent / "assets" / "icono" / filename
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else ""


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


def _safe_download_name(value: str, fallback: str = "catalogo") -> str:
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(value or ""))
    safe = safe.strip("._-")
    return safe or fallback


def _rule_label(regla: dict) -> str:
    labels = {
        "isin": "Dominio",
        "gte": "Minimo",
        "lte": "Maximo",
        "min_length": "Longitud minima",
        "str_length": "Longitud",
        "regex": "Patron",
    }
    tipo = str(regla.get("tipo", "") or "").lower().strip()
    return labels.get(tipo, tipo or "Regla")


def _rule_detail(regla: dict) -> str:
    tipo = str(regla.get("tipo", "") or "").lower().strip()
    valor = regla.get("valor")

    if tipo == "isin":
        values = valor if isinstance(valor, list) else [valor]
        values_txt = ", ".join(str(v) for v in values if v not in (None, ""))
        return f"Valores permitidos: {values_txt or 'sin valores definidos'}"
    if tipo == "gte":
        return f"Valor minimo permitido: {valor}"
    if tipo == "lte":
        return f"Valor maximo permitido: {valor}"
    if tipo == "min_length":
        return f"Longitud minima requerida: {valor}"
    if tipo == "str_length":
        return f"Longitud permitida entre {regla.get('min')} y {regla.get('max')}"
    if tipo == "regex":
        return f"Debe cumplir el patron: {valor}"
    return str(regla or "Sin detalle")


def _rules_text(reglas: list) -> str:
    reglas = reglas or []
    if not reglas:
        return "Sin reglas adicionales"
    return "; ".join(_rule_detail(r) for r in reglas)


def _rules_chips_html(reglas: list) -> str:
    reglas = reglas or []
    if not reglas:
        return (
            '<span class="schema-rule-chip muted" '
            'title="No tiene reglas adicionales">Sin reglas</span>'
        )

    chips = []
    for regla in reglas:
        label = html.escape(_rule_label(regla))
        detail = html.escape(_rule_detail(regla), quote=True)
        chips.append(f'<span class="schema-rule-chip" title="{detail}">{label}</span>')
    return "".join(chips)


def _schema_rows(cols_schema: list) -> list[dict]:
    rows = []
    for col in cols_schema:
        rows.append(
            {
                "Columna": str(col.get("nombre", "")),
                "Tipo": str(col.get("tipo", "")),
                "Acepta vacios": "Si" if bool(col.get("nullable")) else "No",
                "Reglas": _rules_text(col.get("reglas") or []),
            }
        )
    return rows


def _schema_csv_bytes(cols_schema: list) -> bytes:
    return pd.DataFrame(_schema_rows(cols_schema)).to_csv(index=False).encode("utf-8-sig")


def _header_csv_bytes(cols_schema: list) -> bytes:
    header = [str(col.get("nombre", "")) for col in cols_schema]
    return pd.DataFrame(columns=header).to_csv(index=False).encode("utf-8-sig")


def _schema_table_html(cols_schema: list) -> str:
    rows_html = []
    for col in cols_schema:
        nombre = html.escape(str(col.get("nombre", "")))
        tipo = html.escape(str(col.get("tipo", "")))
        acepta_vacios = "Si" if bool(col.get("nullable")) else "No"
        reglas_html = _rules_chips_html(col.get("reglas") or [])
        rows_html.append(
            "<tr>"
            f"<td><code>{nombre}</code></td>"
            f"<td>{tipo}</td>"
            f"<td>{acepta_vacios}</td>"
            f"<td>{reglas_html}</td>"
            "</tr>"
        )

    return f"""
    <style>
      .schema-table-wrap {{
        max-height: 320px;
        overflow: auto;
        border: 1px solid #D1D9F0;
        border-radius: 10px;
        background: #F8FAFF;
      }}
      .schema-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
      }}
      .schema-table th {{
        position: sticky;
        top: 0;
        z-index: 1;
        background: #EEF2FF;
        color: #1C2F6E;
        font-weight: 700;
        text-align: left;
        padding: 8px 10px;
        border-bottom: 1px solid #D1D9F0;
      }}
      .schema-table td {{
        padding: 8px 10px;
        border-bottom: 1px solid #E5E9F5;
        color: #1C2F6E;
        vertical-align: top;
      }}
      .schema-table tr:last-child td {{
        border-bottom: 0;
      }}
      .schema-rule-chip {{
        display: inline-flex;
        margin: 0 4px 4px 0;
        padding: 2px 8px;
        border-radius: 999px;
        background: #FFF7ED;
        border: 1px solid #FDBA74;
        color: #9A3412;
        font-size: 11px;
        font-weight: 600;
        cursor: help;
      }}
      .schema-rule-chip.muted {{
        background: #F3F4F6;
        border-color: #E5E7EB;
        color: #6B7280;
      }}
    </style>
    <div class="schema-table-wrap">
      <table class="schema-table">
        <thead>
          <tr>
            <th>Columna</th>
            <th>Tipo</th>
            <th>Acepta vacios</th>
            <th>Reglas</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
    """


# ------------------------------------------------------------------
# Modal: historial de cargas
# ------------------------------------------------------------------
@st.experimental_dialog("Historial de cargas", width="large")
def _render_history_dialog() -> None:
    from views.history_view import render_history_content
    user     = st.session_state.user_info or {}
    is_admin = user.get("rol") == "Admin"
    username = user.get("username", "")
    render_history_content(user, is_admin, username)


# ------------------------------------------------------------------
# Modal: selector de catálogo
# ------------------------------------------------------------------
@st.experimental_dialog("Seleccionar catálogo", width="large")
def _render_catalog_selector_dialog() -> None:
    user = st.session_state.get("user_info") or {}

    try:
        proyectos = get_proyectos_list(
            username=user.get("username", ""),
            rol=user.get("rol", "Publicador"),
        )
    except Exception as e:
        st.error(user_facing_error(e, context="catalogs"))
        return

    if not proyectos and user.get("rol") != "Admin":
        st.info("No tienes proyectos o catalogos habilitados. Contacta al administrador.")
        return

    if not proyectos:
        st.info("No hay catálogos configurados. Contacta al administrador.")
        return

    proy_options = {p["id"]: p["nombre"] for p in proyectos}
    proy_sel_id = st.selectbox(
        "Proyecto",
        options=list(proy_options.keys()),
        format_func=lambda x: proy_options[x],
        key="dialog_proy",
    )

    try:
        catalogs = get_catalogs_by_project(
            proy_sel_id,
            username=user.get("username", ""),
            rol=user.get("rol", "Publicador"),
        )
    except Exception as e:
        st.error(user_facing_error(e, context="catalogs"))
        return

    if not catalogs:
        st.warning(f"No hay catálogos en '{proy_options[proy_sel_id]}'.")
        return

    cat_options = {c["catalog_id"]: c["nombre"] for c in catalogs}
    cat_sel_id = st.selectbox(
        "Catálogo",
        options=list(cat_options.keys()),
        format_func=lambda x: cat_options[x],
        key="dialog_cat",
    )
    selected_cat = next((c for c in catalogs if c["catalog_id"] == cat_sel_id), None)
    if not selected_cat:
        return

    schema     = selected_cat.get("schema", {})
    cols_schema = schema.get("columnas", [])
    estrategia = selected_cat["estrategia"]
    destino    = selected_cat["destino"]

    st.markdown(
        f"<div style='margin:10px 0 8px;'>"
        f"<code style='background:rgba(245,168,0,0.12);color:#B45309;"
        f"padding:3px 8px;border-radius:4px;font-size:13px;'>"
        f"{selected_cat['base_datos']}.{selected_cat['tabla_destino']}</code>"
        f"<span style='margin-left:8px;font-size:12px;color:#6B7280;'>{len(cols_schema)} columna(s)</span>"
        f"<span style='background:rgba(245,168,0,0.18);color:#F5A800;padding:1px 8px;"
        f"border-radius:20px;font-size:11px;border:1px solid rgba(245,168,0,0.3);"
        f"margin-left:10px;'>{estrategia.upper()}</span>"
        f"<span style='background:rgba(110,231,183,0.15);color:#6EE7B7;padding:1px 8px;"
        f"border-radius:20px;font-size:11px;border:1px solid rgba(110,231,183,0.3);"
        f"margin-left:4px;'>{destino.upper()}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if cols_schema:
        download_base = _safe_download_name(selected_cat.get("catalog_id") or selected_cat.get("nombre"))
        d1, d2 = st.columns(2)
        with d1:
            st.download_button(
                "Descargar cabecera (.csv)",
                data=_header_csv_bytes(cols_schema),
                file_name=f"{download_base}_cabecera.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"download_header_{cat_sel_id}",
            )
        with d2:
            st.download_button(
                "Descargar esquema (.csv)",
                data=_schema_csv_bytes(cols_schema),
                file_name=f"{download_base}_esquema.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"download_schema_{cat_sel_id}",
            )

        st.markdown(_schema_table_html(cols_schema), unsafe_allow_html=True)
    else:
        st.caption("Este catálogo no tiene esquema configurado.")

    st.divider()
    if st.button("Confirmar selección", type="primary", use_container_width=True, key="btn_dialog_confirmar"):
        prev_id = (st.session_state.get("selected_catalog") or {}).get("catalog_id")
        if prev_id != cat_sel_id:
            for k in ["uploaded_df", "uploaded_audit_bytes", "uploaded_audit_name",
                      "uploaded_name", "validation_result", "carga_ejecutada", "load_result"]:
                st.session_state.pop(k, None)
            st.session_state.current_step = "upload"
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
        st.session_state.selected_project_name = proy_options.get(proy_sel_id, proy_sel_id)
        st.rerun()


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
def render_main_app() -> None:
    _inject_main_css()
    _render_sidebar()
    _render_main_content()


def _go_to_post_login_home() -> None:
    st.session_state.current_view = "upload"
    st.session_state.current_step = "upload"
    for key in [
        "selected_project_id",
        "selected_project_name",
        "selected_catalog",
        "uploaded_df",
        "uploaded_bytes",
        "uploaded_audit_bytes",
        "uploaded_audit_name",
        "uploaded_name",
        "uploaded_preview_items",
        "uploaded_row_origins",
        "uploaded_validation_sources",
        "validation_result",
        "validation_failure_zip_path",
        "carga_ejecutada",
        "load_result",
    ]:
        st.session_state.pop(key, None)
    st.session_state.validation_result = None
    st.session_state.validation_dialog_open = False
    st.session_state.confirm_load_requested = False
    st.rerun()


# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------
def _render_sidebar() -> None:
    user = st.session_state.user_info or {}

    with st.sidebar:
        # Logo + título
        b64 = _logo_b64()
        logo_img = (
            f'<img class="adm-brand-logo-img" src="data:image/png;base64,{b64}" width="40">'
            if b64 else ""
        )
        st.markdown(f"""
        <div class="sidebar-brand">
            <div class="adm-brand-row">
                {logo_img}
                <div class="adm-brand-text">
                    <div style="font-size:11px;font-weight:500;color:#A8B4D8;letter-spacing:0.5px;">banco del</div>
                    <div style="font-size:22px;font-weight:800;color:white;letter-spacing:0;line-height:1.05;">Austro</div>
                </div>
            </div>
            <div class="adm-brand-subtitle">Portal de Ingesta · v1.0</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Volver al inicio", use_container_width=True, key="btn_brand_home", help="Volver al inicio"):
            _go_to_post_login_home()

        st.divider()

        # Info usuario
        is_admin  = user.get("rol") == "Admin"
        rol_color = "#F5A800" if is_admin else "#6EE7B7"
        rol_bg    = "rgba(245,168,0,0.18)" if is_admin else "rgba(110,231,183,0.15)"
        st.markdown(f"""
        <div class="mn-user-card" style="padding:12px; background:rgba(255,255,255,0.07);
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

        # Sección catálogo
        selected_cat = st.session_state.get("selected_catalog")

        if selected_cat:
            estrategia = selected_cat["estrategia"]
            destino    = selected_cat["destino"]
            schema     = selected_cat.get("schema", {})

            st.markdown(f"""
            <div class="mn-catalog-info" style="margin-top:4px; padding:10px 12px;
                        background:rgba(255,255,255,0.07);
                        border:1px solid rgba(255,255,255,0.10);
                        border-radius:8px; font-size:12px; color:#A8B4D8;">
                <div style="color:#A8B4D8; font-size:10px; text-transform:uppercase;
                            letter-spacing:0.5px; margin-bottom:6px;">Catálogo activo</div>
                <div style="color:white; font-weight:600; font-size:13px; margin-bottom:4px;">
                    {selected_cat['nombre']}
                </div>
                <div style="margin-bottom:6px;">
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

            with st.expander("Ver columnas esperadas", expanded=False):
                cols = schema.get("columnas", [])
                if cols:
                    for col in cols:
                        acepta_vacios = "Sí" if col.get("nullable") else "No"
                        acepta_color = "#6EE7B7" if col.get("nullable") else "#FCA5A5"
                        st.markdown(
                            f"<div style='font-size:12px; padding:4px 6px; display:flex;"
                            f"justify-content:space-between; align-items:center;"
                            f"border-bottom:1px solid rgba(255,255,255,0.06);'>"
                            f"<span style='color:#F5A800; font-family:monospace; font-size:11px;'>{col['nombre']}</span>"
                            f"<span style='color:#A8B4D8; font-size:10px; text-align:right;'>"
                            f"{col['tipo']} · Acepta vacíos: "
                            f"<b style='color:{acepta_color};'>{acepta_vacios}</b></span></div>",
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("Sin esquema configurado.")

            _cat_icon_b64 = _nav_icon_b64("registro.png")
            _cat_icon_html = (
                f'<img src="data:image/png;base64,{_cat_icon_b64}" width="22" height="22" style="object-fit:contain;flex-shrink:0;">'
                if _cat_icon_b64 else '<span style="width:22px;display:inline-block;"></span>'
            )
            st.markdown(
                f'<div class="adm-nav-visual">{_cat_icon_html}'
                f'<span class="adm-nav-label">Cambiar catálogo</span></div>',
                unsafe_allow_html=True,
            )
            if st.button("Cambiar catálogo", use_container_width=True, key="btn_cambiar_catalogo"):
                _render_catalog_selector_dialog()
        else:
            st.markdown(
                "<div class='mn-no-catalog' style='padding:12px; background:rgba(255,255,255,0.04);"
                "border:1px dashed rgba(255,255,255,0.15); border-radius:8px;"
                "font-size:12px; color:#7A8EC0; text-align:center; margin-bottom:12px;'>"
                "Selecciona un catálogo para comenzar la carga."
                "</div>",
                unsafe_allow_html=True,
            )
            _sel_icon_b64 = _nav_icon_b64("registro.png")
            _sel_icon_html = (
                f'<img src="data:image/png;base64,{_sel_icon_b64}" width="22" height="22" style="object-fit:contain;flex-shrink:0;">'
                if _sel_icon_b64 else '<span style="width:22px;display:inline-block;"></span>'
            )
            st.markdown(
                f'<div class="adm-nav-visual">{_sel_icon_html}'
                f'<span class="adm-nav-label">Seleccionar catálogo</span></div>',
                unsafe_allow_html=True,
            )
            if st.button("Seleccionar catálogo", use_container_width=True, key="btn_sel_catalogo"):
                _render_catalog_selector_dialog()

        _render_sidebar_footer(user)


def _render_sidebar_footer(user: dict) -> None:
    st.divider()
    if user.get("rol") == "Admin":
        _icon_b64 = _nav_icon_b64("activos.png")
        _icon_html = (
            f'<img src="data:image/png;base64,{_icon_b64}" width="22" height="22" style="object-fit:contain;flex-shrink:0;">'
            if _icon_b64 else '<span style="width:22px;display:inline-block;"></span>'
        )
        st.markdown(
            f'<div class="adm-nav-visual">{_icon_html}'
            f'<span class="adm-nav-label">Administrar catálogos</span></div>',
            unsafe_allow_html=True,
        )
        if st.button("Administrar catálogos", use_container_width=True, key="btn_admin"):
            st.session_state.current_view = "admin"
            st.rerun()

    _hist_b64 = _nav_icon_b64("resumen.png")
    _hist_html = (
        f'<img src="data:image/png;base64,{_hist_b64}" width="22" height="22" style="object-fit:contain;flex-shrink:0;">'
        if _hist_b64 else '<span style="width:22px;display:inline-block;"></span>'
    )
    st.markdown(
        f'<div class="adm-nav-visual">{_hist_html}'
        f'<span class="adm-nav-label">Historial de cargas</span></div>',
        unsafe_allow_html=True,
    )
    if st.button("Historial de cargas", use_container_width=True, key="btn_history"):
        _render_history_dialog()

    _logout_b64 = _nav_icon_b64("usuarios.png")
    _logout_html = (
        f'<img src="data:image/png;base64,{_logout_b64}" width="22" height="22" style="object-fit:contain;flex-shrink:0;">'
        if _logout_b64 else '<span style="width:22px;display:inline-block;"></span>'
    )
    st.markdown(
        f'<div class="adm-nav-visual adm-nav-logout">{_logout_html}'
        f'<span class="adm-nav-label">Cerrar sesión</span></div>',
        unsafe_allow_html=True,
    )
    if st.button("Cerrar sesión", use_container_width=True, key="btn_logout"):
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

    missing_fields = _missing_catalog_fields(catalog)
    if missing_fields:
        st.error(
            "El catálogo seleccionado no está completo para continuar. "
            f"Faltan estos campos obligatorios: {', '.join(missing_fields)}. "
            "Pide al administrador revisar la configuración."
        )
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


def _upload_ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _sheet_key(filename: str, index: int) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in filename)
    return f"sheet_names_{index}_{safe}"


def _file_option_key(prefix: str, filename: str, index: int) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in filename)
    return f"{prefix}_{index}_{safe}"


@st.cache_data(show_spinner=False, max_entries=30)
def _cached_get_excel_sheets(file_bytes: bytes) -> list:
    return get_excel_sheets(file_bytes)


@st.cache_data(show_spinner=False, max_entries=30)
def _cached_read_uploaded_file(
    file_bytes: bytes,
    filename: str,
    delimiter: str = ",",
    encoding: str = "utf-8",
    sheet_name: Optional[str] = None,
) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    return read_uploaded_file(
        file_bytes=file_bytes,
        filename=filename,
        delimiter=delimiter,
        encoding=encoding,
        sheet_name=sheet_name,
    )


def _friendly_preview_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "duplicate column names" in text or "column names found" in text:
        return (
            "No se pudo mostrar la previsualizacion porque la cabecera del archivo "
            "no se interpreto correctamente: hay columnas repetidas. Revisa el separador "
            "en 'Ajustar lectura' o valida que la primera fila tenga nombres de columnas unicos."
        )
    return (
        "No se pudo mostrar la previsualizacion del archivo. Revisa la lectura del archivo "
        "o intenta con otro separador/codificacion."
    )


def _row_origin_records(label: str, df: pd.DataFrame, file_name: str, sheet_name: str = "") -> list[dict]:
    return [
        {
            "origen": label,
            "archivo": file_name,
            "hoja": sheet_name,
            "fila_origen": idx + 2,
        }
        for idx in range(len(df))
    ]


def _apply_row_origins_to_errors(result: ValidationResult, row_origins: list[dict]) -> ValidationResult:
    for error in result.errors:
        source_index = error.fila - 2
        if 0 <= source_index < len(row_origins):
            origin = row_origins[source_index]
            error.archivo = origin.get("archivo", "")
            error.hoja = origin.get("hoja", "")
            error.fila_origen = int(origin.get("fila_origen") or 0)
    return result


def _source_from_label(label: str) -> tuple[str, str]:
    if label.endswith("]") and " [" in label:
        file_name, sheet_name = label.rsplit(" [", 1)
        return file_name, sheet_name[:-1]
    return label, ""


def _validation_sources_from_session(df: pd.DataFrame) -> list[dict]:
    sources = st.session_state.get("uploaded_validation_sources") or []
    if sources:
        return sources

    preview_items = st.session_state.get("uploaded_preview_items") or []
    derived = []
    start_index = 0
    for item in preview_items:
        if item.get("label") == "Conjunto combinado":
            continue
        item_df = item.get("df")
        if item_df is None:
            continue
        archivo, hoja = _source_from_label(str(item.get("label", "")))
        derived.append(
            {
                "label": item.get("label", archivo),
                "archivo": archivo,
                "hoja": hoja,
                "df": item_df,
                "start_index": start_index,
            }
        )
        start_index += len(item_df)

    return derived or [
        {
            "label": st.session_state.get("uploaded_name", "archivo"),
            "archivo": st.session_state.get("uploaded_name", "archivo"),
            "hoja": "",
            "df": df,
            "start_index": 0,
        }
    ]


def _validation_sources_summary_html(df: pd.DataFrame) -> str:
    sources = _validation_sources_from_session(df)
    if not sources:
        return html.escape(st.session_state.get("uploaded_name", "—"))

    items = []
    for idx, source in enumerate(sources, start=1):
        archivo = html.escape(str(source.get("archivo") or source.get("label") or "Archivo"))
        hoja = str(source.get("hoja") or "").strip()
        hoja_html = (
            f"<span style='color:#6B7280;'> · hoja </span>"
            f"<span style='color:#374151;font-weight:600;'>{html.escape(hoja)}</span>"
            if hoja else ""
        )
        rows = len(source.get("df")) if source.get("df") is not None else 0
        items.append(
            "<li style='margin:3px 0; display:flex; gap:8px; align-items:flex-start;'>"
            f"<span style='color:#6B7280; min-width:22px;'>{idx}.</span>"
            "<span style='min-width:0; word-break:break-word;'>"
            f"<code>{archivo}</code>{hoja_html}"
            f"<span style='color:#6B7280;'> · {rows:,} fila(s)</span>"
            "</span>"
            "</li>"
        )

    return (
        "<ol style='margin:6px 0 0; padding:0; list-style:none;'>"
        + "".join(items)
        + "</ol>"
    )


def _validate_sources(df: pd.DataFrame, schema_config: dict) -> ValidationResult:
    sources = _validation_sources_from_session(df)
    combined = ValidationResult(
        success=True,
        rows_checked=sum(len(source["df"]) for source in sources),
        cols_checked=len(df.columns),
    )

    for source in sources:
        source_result = validate_dataframe(source["df"], schema_config)
        start_index = int(source.get("start_index") or 0)
        for error in source_result.errors:
            source_row = error.fila
            error.archivo = str(source.get("archivo") or source.get("label") or "")
            error.hoja = str(source.get("hoja") or "")
            if source_row > 0:
                error.fila_origen = source_row
                error.fila = start_index + source_row
            combined.errors.append(error)

    combined.success = len(combined.errors) == 0
    return combined


def _execute_validation(df: pd.DataFrame, catalog: dict, schema_config: dict) -> ValidationResult:
    st.session_state.confirm_load_requested = False
    try:
        from config.catalogs import get_catalog_by_id
        project_id = st.session_state.get("selected_project_id") or ""
        fresh = get_catalog_by_id(project_id, catalog.get("catalog_id", ""))
        if fresh and fresh.get("schema"):
            schema_config = fresh["schema"]
            st.session_state.selected_catalog = {**catalog, "schema": schema_config}
    except Exception:
        pass  # usa el schema que ya está en memoria como fallback

    result = _validate_sources(df, schema_config)
    st.session_state.validation_result = result
    st.session_state.validation_failure_zip_path = None
    if not result.success:
        user = st.session_state.get("user_info") or {}
        st.session_state.validation_failure_zip_path = save_validation_failure_file(
            file_bytes=st.session_state.get("uploaded_audit_bytes") or b"",
            filename=(
                st.session_state.get("uploaded_audit_name")
                or st.session_state.get("uploaded_name", "archivo")
            ),
            catalog=catalog,
            username=user.get("username", "usuario"),
            project_id=st.session_state.get("selected_project_id", ""),
            project_name=st.session_state.get("selected_project_name", ""),
            error_count=result.error_count,
        )
    st.session_state.validation_dialog_open = True
    return result


@st.experimental_dialog("Resultado de validación", width="large")
def _render_validation_result_dialog(result: ValidationResult, df: pd.DataFrame, catalog: dict) -> None:
    _render_validation_results(result, df, catalog, in_dialog=True)


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

        if not uploaded_files:
            st.session_state.uploaded_df = None
            st.session_state.uploaded_preview_items = []
            st.session_state.uploaded_row_origins = []
            st.session_state.uploaded_validation_sources = []
        else:
            uploaded_payloads = []
            oversized = []
            max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
            for idx, uf in enumerate(uploaded_files):
                fb = uf.getvalue()
                file_size = len(fb)
                ext = _upload_ext(uf.name)
                uploaded_payloads.append(
                    {
                        "index": idx,
                        "name": uf.name,
                        "bytes": fb,
                        "size": file_size,
                        "ext": ext,
                        "is_excel": ext in ("xlsx", "xls"),
                    }
                )
                if file_size > max_bytes:
                    oversized.append(f"{uf.name} ({file_size / 1024 / 1024:.1f} MB)")

            if oversized:
                st.error(
                    "Estos archivos exceden el límite permitido de "
                    f"{MAX_FILE_SIZE_MB} MB: {', '.join(oversized)}"
                )
                st.session_state.uploaded_df = None
                return

            progress_slot = st.empty()
            load_progress = progress_slot.progress(
                5,
                text="Preparando archivos para lectura...",
            )

            text_payloads = [p for p in uploaded_payloads if not p["is_excel"]]
            excel_payloads = [p for p in uploaded_payloads if p["is_excel"]]

            if text_payloads:
                load_progress.progress(10, text="Detectando separador del archivo...")
                detected_by_file = {}
                for payload in text_payloads:
                    enc_key = _file_option_key("encoding", payload["name"], payload["index"])
                    enc = st.session_state.get(enc_key, st.session_state.get("encoding", "utf-8"))
                    detected_by_file[payload["name"]] = detect_file_delimiter(payload["bytes"], enc)

                if len(text_payloads) == 1:
                    only = text_payloads[0]
                    detected = detected_by_file[only["name"]]
                    st.success(
                        f"Separador detectado en **{only['name']}**: "
                        f"**{_DELIM_LABEL.get(detected, detected)}**."
                    )
                else:
                    st.success("Se detectó el separador de cada archivo CSV/TXT.")

                with st.expander("¿Las columnas no se ven bien? Ajustar lectura"):
                    st.caption(
                        "Cambia estas opciones solo si la previsualización muestra "
                        "las columnas mezcladas o los caracteres con símbolos raros. "
                        "La configuración se aplica por archivo."
                    )
                    for payload in text_payloads:
                        detected = detected_by_file[payload["name"]]
                        st.markdown(f"**{payload['name']}**")
                        col_d, col_e = st.columns(2)
                        delim_key = _file_option_key("delimiter", payload["name"], payload["index"])
                        enc_key = _file_option_key("encoding", payload["name"], payload["index"])
                        with col_d:
                            st.selectbox(
                                "Separador",
                                options=list(_DELIM_LABEL.keys()),
                                index=list(_DELIM_LABEL.keys()).index(
                                    st.session_state.get(delim_key, detected)
                                    if st.session_state.get(delim_key, detected) in _DELIM_LABEL
                                    else ","
                                ),
                                format_func=lambda x: _DELIM_LABEL[x],
                                key=delim_key,
                            )
                            cur_delim = st.session_state.get(delim_key, detected)
                            st.caption(_DELIM_TIP.get(cur_delim, "Separador personalizado."))
                            if cur_delim != detected:
                                st.warning(
                                    f"Detectamos **{_DELIM_LABEL.get(detected, detected)}**. "
                                    f"Seleccionaste **{_DELIM_LABEL.get(cur_delim, cur_delim)}**."
                                )
                        with col_e:
                            st.selectbox(
                                "Codificación",
                                options=list(_ENC_LABEL.keys()),
                                index=list(_ENC_LABEL.keys()).index(
                                    st.session_state.get(enc_key, st.session_state.get("encoding", "utf-8"))
                                    if st.session_state.get(enc_key, st.session_state.get("encoding", "utf-8")) in _ENC_LABEL
                                    else "utf-8"
                                ),
                                format_func=lambda x: _ENC_LABEL[x],
                                key=enc_key,
                            )
                            cur_enc = st.session_state.get(enc_key, "utf-8")
                            st.caption(_ENC_TIP[cur_enc])
                        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            excel_sheet_selection = {}
            if excel_payloads:
                with st.expander("Hojas de Excel a leer", expanded=True):
                    st.caption(
                        "Selecciona una o varias hojas por cada archivo Excel. "
                        "Si no cambias nada, se leerá la primera hoja de cada Excel."
                    )
                    total_excel = max(1, len(excel_payloads))
                    for excel_idx, payload in enumerate(excel_payloads, start=1):
                        load_progress.progress(
                            min(30, 12 + int(excel_idx / total_excel * 18)),
                            text=f"Leyendo hojas de Excel: {payload['name']}...",
                        )
                        sheets = _cached_get_excel_sheets(payload["bytes"])
                        if not sheets:
                            st.error(f"**{payload['name']}**: no se pudieron leer las hojas del Excel.")
                            excel_sheet_selection[payload["name"]] = []
                            continue
                        if len(sheets) == 1:
                            excel_sheet_selection[payload["name"]] = [sheets[0]]
                            st.caption(f"**{payload['name']}** — hoja única: `{sheets[0]}`")
                            continue

                        selected_sheets = st.multiselect(
                            f"{payload['name']}",
                            options=sheets,
                            default=[sheets[0]],
                            key=_sheet_key(payload["name"], payload["index"]),
                            help="Puedes seleccionar varias hojas si todas tienen la estructura del catálogo.",
                        )
                        excel_sheet_selection[payload["name"]] = selected_sheets

                    empty_selection = [
                        name for name, sheets in excel_sheet_selection.items()
                        if not sheets
                    ]
                    if empty_selection:
                        st.warning(
                            "Selecciona al menos una hoja para: "
                            + ", ".join(empty_selection)
                        )
                        progress_slot.empty()
                        st.session_state.uploaded_df = None
                        st.session_state.uploaded_preview_items = []
                        st.session_state.uploaded_row_origins = []
                        st.session_state.uploaded_validation_sources = []
                        return

            dfs = []
            row_origins = []
            validation_sources = []
            original_files = []
            errores_lectura = []

            total_reads = len(text_payloads) + sum(
                len(excel_sheet_selection.get(p["name"], [None]))
                for p in excel_payloads
            )
            total_reads = max(1, total_reads)
            read_done = 0

            for payload in uploaded_payloads:
                original_files.append((payload["name"], payload["bytes"]))
                if payload["is_excel"]:
                    sheets_to_read = excel_sheet_selection.get(payload["name"], [None])
                    for sheet_name in sheets_to_read:
                        display_name = f"{payload['name']} [{sheet_name}]" if sheet_name else payload["name"]
                        load_progress.progress(
                            min(85, 35 + int(read_done / total_reads * 45)),
                            text=f"Leyendo datos: {display_name}...",
                        )
                        df_i, err_i = _cached_read_uploaded_file(
                            file_bytes=payload["bytes"],
                            filename=payload["name"],
                            delimiter=st.session_state.get("delimiter", ","),
                            encoding=st.session_state.get("encoding", "utf-8"),
                            sheet_name=sheet_name,
                        )
                        read_done += 1
                        if err_i:
                            errores_lectura.append(f"**{display_name}**: {err_i}")
                        elif df_i is None or df_i.empty:
                            errores_lectura.append(f"**{display_name}**: hoja vacía o sin columnas.")
                        else:
                            dfs.append((display_name, df_i, payload["size"]))
                            row_origins.extend(_row_origin_records(display_name, df_i, payload["name"], str(sheet_name or "")))
                            validation_sources.append(
                                {
                                    "label": display_name,
                                    "archivo": payload["name"],
                                    "hoja": str(sheet_name or ""),
                                    "df": df_i,
                                    "start_index": 0,
                                }
                            )
                else:
                    delim_key = _file_option_key("delimiter", payload["name"], payload["index"])
                    enc_key = _file_option_key("encoding", payload["name"], payload["index"])
                    load_progress.progress(
                        min(85, 35 + int(read_done / total_reads * 45)),
                        text=f"Leyendo datos: {payload['name']}...",
                    )
                    df_i, err_i = _cached_read_uploaded_file(
                        file_bytes=payload["bytes"],
                        filename=payload["name"],
                        delimiter=st.session_state.get(delim_key, st.session_state.get("delimiter", ",")),
                        encoding=st.session_state.get(enc_key, st.session_state.get("encoding", "utf-8")),
                    )
                    read_done += 1
                    if err_i:
                        errores_lectura.append(f"**{payload['name']}**: {err_i}")
                    elif df_i is None or df_i.empty:
                        errores_lectura.append(f"**{payload['name']}**: archivo vacío o separador incorrecto.")
                    else:
                        dfs.append((payload["name"], df_i, payload["size"]))
                        row_origins.extend(_row_origin_records(payload["name"], df_i, payload["name"]))
                        validation_sources.append(
                            {
                                "label": payload["name"],
                                "archivo": payload["name"],
                                "hoja": "",
                                "df": df_i,
                                "start_index": 0,
                            }
                        )

            if errores_lectura:
                progress_slot.empty()
                for msg in errores_lectura:
                    st.error(msg)
                st.session_state.uploaded_df = None
                st.session_state.uploaded_preview_items = []
                st.session_state.uploaded_row_origins = []
                st.session_state.uploaded_validation_sources = []
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

            load_progress.progress(88, text="Combinando archivos y preparando previsualizacion...")
            combined_df   = pd.concat([d for _, d, _ in dfs], ignore_index=True)
            combined_name = " + ".join(fname for fname, _, _ in dfs)
            total_bytes   = sum(len(file_bytes) for _, file_bytes in original_files)
            audit_bytes, audit_name = _build_audit_payload(original_files)

            if len(combined_df) > MAX_ROWS_IN_MEMORY:
                progress_slot.empty()
                st.error(
                    "El archivo tiene demasiadas filas para procesarlo de una sola vez. "
                    f"Este portal puede revisar hasta {MAX_ROWS_IN_MEMORY:,} filas por carga. "
                    "Divide el archivo en partes más pequeñas o solicita apoyo al administrador."
                )
                st.session_state.uploaded_df = None
                return

            stats = get_file_stats(combined_df, b"x" * total_bytes)
            start_index = 0
            for source in validation_sources:
                source["start_index"] = start_index
                start_index += len(source["df"])

            st.session_state.uploaded_df          = combined_df
            st.session_state.uploaded_audit_bytes = audit_bytes
            st.session_state.uploaded_audit_name  = audit_name
            st.session_state.uploaded_name        = combined_name
            st.session_state.uploaded_row_origins = row_origins
            st.session_state.uploaded_validation_sources = validation_sources
            st.session_state.uploaded_preview_items = [
                {"label": "Conjunto combinado", "df": combined_df},
                *[
                    {"label": source["label"], "df": source["df"], "archivo": source["archivo"], "hoja": source["hoja"]}
                    for source in validation_sources
                ],
            ]
            load_progress.progress(100, text="Previsualizacion lista.")
            progress_slot.empty()

            c1, c2, c3 = st.columns(3)
            c1.metric("Filas totales", f"{stats['filas']:,}")
            c2.metric("Columnas",      stats["columnas"])
            c3.metric("Archivos",      len(dfs))

            if stats["null_count"] > 0:
                st.warning(f"{stats['null_count']} celdas vacías detectadas en el conjunto combinado.")

    with col2:
        if st.session_state.get("uploaded_df") is not None:
            st.markdown("#### Previsualización")
            preview_items = st.session_state.get("uploaded_preview_items") or [
                {"label": "Conjunto combinado", "df": st.session_state.uploaded_df}
            ]
            selected_preview = st.selectbox(
                "Vista",
                options=list(range(len(preview_items))),
                format_func=lambda idx: preview_items[idx]["label"],
                key="preview_item_idx",
                label_visibility="collapsed",
            )
            preview_df = preview_items[selected_preview]["df"]
            preview_placeholder = st.empty()
            preview_placeholder.markdown(
                _preview_table_loading_html(
                    rows=min(8, max(3, min(20, len(preview_df)))),
                    cols=min(5, max(1, len(preview_df.columns))),
                ),
                unsafe_allow_html=True,
            )
            st.session_state.preview_render_failed = False
            try:
                with preview_placeholder.container():
                    st.dataframe(
                        preview_df.head(20),
                        use_container_width=True,
                        height=320,
                    )
            except Exception as exc:
                preview_placeholder.empty()
                st.session_state.preview_render_failed = True
                st.error(_friendly_preview_error(exc))
                st.info(
                    "Sugerencia: abre 'Las columnas no se ven bien? Ajustar lectura' "
                    "y prueba Coma (,), Punto y coma (;), Pipe (|) o Tabulador."
                )
            else:
                st.caption(
                    f"Primeras {min(20, len(preview_df))} filas "
                    f"de {len(preview_df):,} en esta vista."
                )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    if st.session_state.get("uploaded_df") is not None and not st.session_state.get("preview_render_failed"):
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
    files_summary = _validation_sources_summary_html(df)

    st.markdown("#### Archivo a validar")
    st.markdown(f"""
    <div style="padding:16px 18px; background:var(--secondary-background-color);
                border-radius:10px; font-size:13px; max-width:860px; margin:0 auto;">
        <div><b>Archivos / hojas:</b>{files_summary}</div>
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

    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    mid_l, mid_c, mid_r = st.columns([1, 1.3, 1])
    with mid_c:
        if st.button("Ejecutar validación", type="primary", use_container_width=True, key="btn_validate"):
            progress = st.progress(0, text="Preparando validación...")
            progress.progress(30, text="Revisando estructura y columnas...")
            progress.progress(65, text="Aplicando reglas de calidad...")
            result = _execute_validation(df, catalog, schema_config)
            progress.progress(100, text="Validación completada.")
            st.rerun()

        if st.button("← Volver al archivo", use_container_width=True, key="btn_back_v"):
            st.session_state.current_step      = "upload"
            st.session_state.validation_result = None
            st.session_state.validation_dialog_open = False
            st.session_state.confirm_load_requested = False
            st.rerun()

    if result is not None:
        if st.session_state.get("validation_dialog_open", False):
            st.session_state.validation_dialog_open = False
            try:
                _render_validation_result_dialog(result, df, catalog)
            except Exception as exc:
                if "only one dialog is allowed" in str(exc).lower():
                    st.info(
                        "El resultado de la validación ya está abierto o se acaba de cerrar. "
                        "Si deseas revisarlo nuevamente, usa el botón de abajo."
                    )
                else:
                    raise
        else:
            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            view_l, view_c, view_r = st.columns([1, 1.3, 1])
            with view_c:
                if st.button("Ver resultado de validación", use_container_width=True, key="btn_show_validation_result"):
                    st.session_state.validation_dialog_open = True
                    st.rerun()


def _render_validation_results(result: ValidationResult, df: pd.DataFrame, catalog: dict, in_dialog: bool = False) -> None:
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
        if st.session_state.get("confirm_load_requested"):
            st.warning(
                "Confirma la carga solo si revisaste la previsualización y la validación. "
                f"Se insertarán {result.rows_checked:,} fila(s) en `{catalog['tabla_destino']}` "
                f"con estrategia `{catalog['estrategia'].upper()}` y se guardará evidencia auditada."
            )
            c_yes, c_no = st.columns(2)
            with c_yes:
                if st.button("Sí, confirmar carga", type="primary", use_container_width=True, key="btn_confirm_yes"):
                    st.session_state.confirm_load_requested = False
                    st.session_state.validation_dialog_open = False
                    st.session_state.current_step = "result"
                    st.rerun()
            with c_no:
                if st.button("Cancelar", use_container_width=True, key="btn_confirm_no"):
                    st.session_state.confirm_load_requested = False
                    st.session_state.validation_dialog_open = True
                    st.rerun()
        else:
            if st.button("Confirmar carga →", type="primary", use_container_width=True, key="btn_confirm"):
                st.session_state.confirm_load_requested = True
                st.session_state.validation_dialog_open = True
                st.rerun()
        if in_dialog and st.button("Cerrar", use_container_width=True, key="btn_close_validation_ok"):
            st.session_state.confirm_load_requested = False
            st.session_state.validation_dialog_open = False
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
        if "Archivo" in errors_df.columns and not errors_df.empty:
            group_items = []
            for (archivo, hoja), group_df in errors_df.groupby(["Archivo", "Hoja"], dropna=False, sort=False):
                hoja_label = f" · hoja {hoja}" if str(hoja or "").strip() else ""
                label = f"{archivo or 'Sin archivo'}{hoja_label} — {len(group_df)} error(es)"
                group_items.append(
                    {
                        "label": label,
                        "archivo": archivo,
                        "hoja": hoja,
                        "df": group_df,
                    }
                )

            options = ["Todos los errores", *[item["label"] for item in group_items]]
            selected_group = st.selectbox(
                "Ver errores de",
                options=options,
                key="validation_error_group",
            )

            if selected_group == "Todos los errores":
                visible_errors = errors_df.copy()
                table_caption = f"{len(visible_errors)} error(es) en todos los archivos."
            else:
                selected_item = next(item for item in group_items if item["label"] == selected_group)
                visible_errors = selected_item["df"].drop(columns=["Archivo", "Hoja"], errors="ignore")
                table_caption = selected_item["label"]

            st.caption(table_caption)
            st.dataframe(
                visible_errors,
                use_container_width=True,
                height=min(360, 72 + len(visible_errors) * 36),
                column_config={
                    "Archivo":     st.column_config.TextColumn("Archivo", width="medium"),
                    "Hoja":        st.column_config.TextColumn("Hoja", width="small"),
                    "Fila origen": st.column_config.NumberColumn("Fila archivo", width="small"),
                    "Fila":        st.column_config.NumberColumn("Fila combinada", width="small"),
                    "Columna":     st.column_config.TextColumn("Columna", width="medium"),
                    "Valor":       st.column_config.TextColumn("Valor encontrado"),
                    "Regla":       st.column_config.TextColumn("Regla"),
                    "Detalle":     st.column_config.TextColumn("Detalle"),
                },
            )
        else:
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
                },
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
        if in_dialog and st.button("Cerrar", use_container_width=True, key="btn_close_validation_error"):
            st.session_state.validation_dialog_open = False
            st.rerun()
        validation_zip_path = st.session_state.get("validation_failure_zip_path")
        if validation_zip_path:
            st.info(f"Archivo fallido guardado en auditoría: `{validation_zip_path}`")


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
                project_name=st.session_state.get("selected_project_name", ""),
            )
        st.session_state.carga_ejecutada = True
        st.session_state.load_result     = load_result
        st.rerun()

    load_result = st.session_state.get("load_result", {})
    success     = load_result.get("success", False)

    if not success:
        op_id = html.escape(str(load_result.get("operation_id", "—")))
        error_text = html.escape(str(load_result.get("error") or "Ocurrió un error inesperado durante la carga."))
        st.markdown(f"""
        <div style="
            padding:28px 32px; background:#FEF2F2;
            border:1px solid #FCA5A5; border-radius:14px;
            text-align:center; margin-bottom:24px;
        ">
            <div style="font-size:48px; margin-bottom:12px;">❌</div>
            <div style="font-weight:600; font-size:18px; color:#7F1D1D;">
                No se pudo completar la carga
            </div>
            <div style="font-size:13px; color:#B91C1C; margin-top:8px;">
                {error_text}
            </div>
            <div style="font-size:12px; color:#7F1D1D; margin-top:10px;">
                Operación: <code>{op_id}</code>
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
    audit_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    zip_path = load_result.get("zip_path") or "No disponible"
    st.markdown("**Resumen de auditoría**")
    st.markdown(f"""
    <div style="
        padding:18px 20px;
        background:var(--secondary-background-color);
        border:1px solid #E5E7EB;
        border-radius:12px;
        margin-bottom:8px;
    ">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px;">
            <div style="font-size:15px;font-weight:700;color:#1C2F6E;">Registro guardado</div>
            <span style="
                background:#DCFCE7;
                color:#166534;
                border:1px solid #86EFAC;
                padding:2px 10px;
                border-radius:999px;
                font-size:11px;
                font-weight:600;
            ">Éxito</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(2,minmax(220px,1fr));gap:12px 24px;">
            <div>
                <div style="font-size:11px;color:#6B7280;text-transform:uppercase;letter-spacing:.04em;">Operación</div>
                <div style="font-size:13px;color:#111827;font-weight:600;"><code>{load_result.get("operation_id", "—")}</code></div>
            </div>
            <div>
                <div style="font-size:11px;color:#6B7280;text-transform:uppercase;letter-spacing:.04em;">Fecha</div>
                <div style="font-size:13px;color:#111827;">{audit_timestamp}</div>
            </div>
            <div>
                <div style="font-size:11px;color:#6B7280;text-transform:uppercase;letter-spacing:.04em;">Usuario</div>
                <div style="font-size:13px;color:#111827;">{user.get("username", "—")}</div>
            </div>
            <div>
                <div style="font-size:11px;color:#6B7280;text-transform:uppercase;letter-spacing:.04em;">Catálogo</div>
                <div style="font-size:13px;color:#111827;">{catalog["nombre"]}</div>
            </div>
            <div style="grid-column:1 / -1;">
                <div style="font-size:11px;color:#6B7280;text-transform:uppercase;letter-spacing:.04em;">Archivo</div>
                <div style="font-size:13px;color:#111827;word-break:break-word;">{filename}</div>
            </div>
            <div style="grid-column:1 / -1;">
                <div style="font-size:11px;color:#6B7280;text-transform:uppercase;letter-spacing:.04em;">Ruta auditada</div>
                <div style="font-size:12px;color:#374151;word-break:break-word;"><code>{zip_path}</code></div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

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
        /* ── Sidebar brand ── */
        section[data-testid="stSidebar"] .sidebar-brand {
            display: block;
            padding: 8px 8px 4px;
            border-radius: 12px;
        }
        section[data-testid="stSidebar"] .adm-brand-row {
            display: flex; align-items: center; gap: 12px; margin-bottom: 6px;
        }
        section[data-testid="stSidebar"] .adm-brand-logo-img { flex-shrink: 0; }
        section[data-testid="stSidebar"] .adm-brand-text { min-width: 0; }
        section[data-testid="stSidebar"] .adm-brand-subtitle {
            font-size: 12px; color: #DCE4FF; margin-left: 52px;
        }

        /* Invisible overlay sobre el brand */
        section[data-testid="stSidebar"] div:has(> div > .sidebar-brand) + div,
        section[data-testid="stSidebar"] div:has(.sidebar-brand) + div {
            margin-top: -88px !important;
            height: 88px !important;
            position: relative !important;
            z-index: 5 !important;
            overflow: hidden !important;
            margin-bottom: 0 !important;
        }
        section[data-testid="stSidebar"] div:has(.sidebar-brand) + div [data-testid="stButton"],
        section[data-testid="stSidebar"] div:has(.sidebar-brand) + div button {
            width: 100% !important;
            height: 88px !important;
            min-height: 0 !important;
            opacity: 0 !important;
            background: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
            cursor: pointer !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        section[data-testid="stSidebar"] div:has(.sidebar-brand) + div button:hover {
            opacity: 1 !important;
            background: rgba(255,255,255,0.07) !important;
            border-radius: 12px !important;
        }

        /* ── Nav visual (botones con ícono) ── */
        section[data-testid="stSidebar"] .adm-nav-visual {
            display: flex;
            align-items: center;
            gap: 11px;
            height: 44px;
            padding: 0 16px;
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.22);
            color: rgba(220,228,255,0.85);
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s, border-color 0.2s;
            box-sizing: border-box;
        }
        section[data-testid="stSidebar"] .adm-nav-label {
            flex: 1; white-space: nowrap; overflow: hidden;
        }
        section[data-testid="stSidebar"] .adm-nav-logout {
            border-color: rgba(230,80,80,0.5) !important;
            color: rgba(255,160,160,0.9) !important;
        }

        /* Overlay invisible sobre cada nav visual */
        section[data-testid="stSidebar"] div:has(.adm-nav-visual) + div {
            margin-top: -44px !important;
            height: 44px !important;
            position: relative !important;
            z-index: 2 !important;
            overflow: hidden !important;
        }
        section[data-testid="stSidebar"] div:has(.adm-nav-visual) + div [data-testid="stButton"],
        section[data-testid="stSidebar"] div:has(.adm-nav-visual) + div button {
            width: 100% !important;
            height: 44px !important;
            min-height: 0 !important;
            opacity: 0 !important;
            cursor: pointer !important;
            margin: 0 !important;
            padding: 0 !important;
        }

        /* ── Sidebar colapsado: solo iconos ── */
        [data-testid="stSidebar"][aria-expanded="false"] {
            min-width: 74px !important;
            width: 74px !important;
            transform: none !important;
            margin-left: 0 !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] > div {
            overflow: hidden !important;
            width: 74px !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-brand-text,
        [data-testid="stSidebar"][aria-expanded="false"] .adm-brand-subtitle,
        [data-testid="stSidebar"][aria-expanded="false"] hr,
        [data-testid="stSidebar"][aria-expanded="false"] .adm-nav-label { display: none !important; }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-nav-visual {
            justify-content: center !important;
            padding: 0 !important;
            gap: 0 !important;
            height: 50px !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-nav-visual img {
            width: 28px !important;
            height: 28px !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-brand-row {
            justify-content: center !important;
            gap: 0 !important;
            margin-bottom: 0 !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-brand-logo-img {
            width: 46px !important;
            height: 46px !important;
            display: block !important;
            margin: 0 auto !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] .sidebar-brand {
            padding: 36px 4px 4px !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] div:has(.adm-nav-visual) + div {
            height: 50px !important;
            margin-top: -50px !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] div:has(.adm-nav-visual) + div button {
            height: 50px !important;
        }
        /* Ocultar tarjeta usuario, info catálogo y mensaje cuando está colapsado */
        [data-testid="stSidebar"][aria-expanded="false"] .mn-user-card,
        [data-testid="stSidebar"][aria-expanded="false"] .mn-catalog-info,
        [data-testid="stSidebar"][aria-expanded="false"] .mn-no-catalog,
        [data-testid="stSidebar"][aria-expanded="false"] [data-testid="stExpander"] {
            display: none !important;
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

        /* Botón Seleccionar catálogo — mismo estilo que los demás */
        section[data-testid="stSidebar"] button[kind="primary"] {
            background: rgba(255,255,255,0.10) !important;
            border: 1px solid rgba(255,255,255,0.20) !important;
            color: white !important;
            font-weight: 500 !important;
            border-radius: 8px !important;
        }
        section[data-testid="stSidebar"] button[kind="primary"]:hover {
            background: rgba(255,255,255,0.18) !important;
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

        section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"] {
            background: #8F3A3A !important;
            border: 1px solid rgba(255,255,255,0.24) !important;
            color: white !important;
            font-weight: 600 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"]:hover {
            background: #7A3030 !important;
            border-color: rgba(255,255,255,0.34) !important;
        }
    </style>
    """, unsafe_allow_html=True)
