"""
views/history_view.py
Vista de historial de cargas — muestra el log de auditoría.
Admins ven todas las cargas; Publicadores solo las suyas.
"""
from __future__ import annotations

import base64
import os
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from services.db_writer import get_audit_log
from utils.error_messages import user_facing_error


def _extract_operation_id(zip_path: object) -> str:
    if zip_path is None or str(zip_path) == "None":
        return "—"
    file_name = os.path.basename(str(zip_path))
    match = re.search(r"_([a-f0-9]{12})_.*\.zip$", file_name, flags=re.IGNORECASE)
    return match.group(1).lower() if match else "—"


def _skeleton_html(n: int = 4) -> str:
    widths = ["long", "medium", "short", "long", "medium"]
    lines = "".join(
        f'<div class="skel-line {widths[i % len(widths)]}"></div>'
        for i in range(n)
    )
    return f'<div style="padding:8px 0">{lines}</div>'


def _logo_b64() -> str:
    logo = Path(__file__).parent.parent / "assets" / "logo.png"
    return base64.b64encode(logo.read_bytes()).decode() if logo.exists() else ""


def _nav_icon_b64(filename: str) -> str:
    path = Path(__file__).parent.parent / "assets" / "icono" / filename
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else ""


def render_history_view() -> None:
    _inject_css()

    user     = st.session_state.user_info or {}
    is_admin = user.get("rol") == "Admin"
    username = user.get("username", "")

    # ── Sidebar ──────────────────────────────────────────────────────
    with st.sidebar:
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
            <div class="adm-brand-subtitle">Historial de Cargas</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Volver al inicio", use_container_width=True, key="hist_brand_home", help="Volver al inicio"):
            st.session_state.current_view = "upload"
            st.session_state.current_step = "upload"
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

        st.divider()
        _portal_b64 = _nav_icon_b64("portal.png")
        _portal_html = (
            f'<img src="data:image/png;base64,{_portal_b64}" width="22" height="22" style="object-fit:contain;flex-shrink:0;">'
            if _portal_b64 else '<span style="width:22px;display:inline-block;"></span>'
        )
        st.markdown(
            f'<div class="adm-nav-visual adm-nav-portal">{_portal_html}'
            f'<span class="adm-nav-label">← Volver al portal</span></div>',
            unsafe_allow_html=True,
        )
        if st.button("← Volver al portal", use_container_width=True, key="hist_back_sidebar"):
            st.session_state.current_view = "upload"
            st.rerun()

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
        if st.button("Cerrar sesión", use_container_width=True, key="hist_logout"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

    render_history_content(user, is_admin, username)


def render_history_content(user: dict, is_admin: bool, username: str) -> None:
    # ── Encabezado ───────────────────────────────────────────────────
    st.markdown("""
    <div style="padding:8px 0 20px;">
        <h2 style="font-size:20px; font-weight:600; margin:0; color:var(--text-color);">
            Historial de cargas
        </h2>
        <p style="font-size:13px; color:#6B7280; margin-top:4px;">
            Registro de auditoría de todas las cargas realizadas al sistema.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── Filtros ──────────────────────────────────────────────────────
    with st.expander("Filtros", expanded=True):
        f1, f2, f3, f4 = st.columns([2, 2, 2, 2])

        with f1:
            fecha_desde = st.date_input(
                "Desde",
                value=date.today() - timedelta(days=30),
                key="hist_fecha_desde",
            )
        with f2:
            fecha_hasta = st.date_input(
                "Hasta",
                value=date.today(),
                key="hist_fecha_hasta",
            )
        with f3:
            estado_filter = st.selectbox(
                "Estado",
                options=["Todos", "Exito", "Fallo"],
                key="hist_estado",
            )
        with f4:
            if is_admin:
                usuario_filter = st.text_input(
                    "Usuario (Admin)",
                    placeholder="Todos los usuarios",
                    key="hist_usuario",
                )
            else:
                st.markdown(f"""
                <div style="padding-top:26px; font-size:12px; color:#6B7280;">
                    Mostrando tus cargas como <b>{username}</b>
                </div>
                """, unsafe_allow_html=True)
                usuario_filter = username

    # ── Carga de datos ───────────────────────────────────────────────
    ph = st.empty()
    ph.markdown(_skeleton_html(6), unsafe_allow_html=True)
    try:
        rows = get_audit_log(
            username_filter=None if (is_admin and not usuario_filter) else (usuario_filter or username),
            fecha_desde=str(fecha_desde),
            fecha_hasta=str(fecha_hasta),
            estado=None if estado_filter == "Todos" else estado_filter,
        )
        ph.empty()
    except Exception as exc:
        ph.empty()
        st.error(user_facing_error(exc, context="audit"))
        return

    if not rows:
        st.info("No hay registros para los filtros seleccionados.")
        return

    df = pd.DataFrame(rows)

    # ── Métricas resumen ─────────────────────────────────────────────
    total    = len(df)
    exitosas = int((df["estado_carga"] == "Exito").sum())
    fallidas = int((df["estado_carga"] == "Fallo").sum())
    filas_ok = int(df.loc[df["estado_carga"] == "Exito", "filas_procesadas"].sum())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total cargas",     f"{total:,}")
    m2.metric("Exitosas",         f"{exitosas:,}")
    m3.metric("Fallidas",         f"{fallidas:,}")
    m4.metric("Filas procesadas", f"{filas_ok:,}")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    st.markdown("**Tendencias**")
    ana1, ana2, ana3 = st.tabs(["Por día", "Por catálogo", "Por usuario"])

    with ana1:
        daily = df.copy()
        daily["fecha"] = pd.to_datetime(daily["timestamp_carga"]).dt.date
        daily_summary = (
            daily.groupby("fecha")
            .agg(cargas=("id", "count"), filas=("filas_procesadas", "sum"))
            .sort_index()
        )
        st.bar_chart(daily_summary["cargas"], use_container_width=True)
        st.dataframe(daily_summary.reset_index(), use_container_width=True, hide_index=True)

    with ana2:
        by_catalog = (
            df.groupby("id_catalogo")
            .agg(
                cargas=("id", "count"),
                filas=("filas_procesadas", "sum"),
                fallos=("estado_carga", lambda s: int((s == "Fallo").sum())),
            )
            .sort_values(["cargas", "filas"], ascending=False)
            .head(15)
        )
        st.bar_chart(by_catalog["cargas"], use_container_width=True)
        st.dataframe(by_catalog.reset_index(), use_container_width=True, hide_index=True)

    with ana3:
        by_user = (
            df.groupby("usuario_ad")
            .agg(
                cargas=("id", "count"),
                filas=("filas_procesadas", "sum"),
                exitosas=("estado_carga", lambda s: int((s == "Exito").sum())),
            )
            .sort_values(["cargas", "filas"], ascending=False)
            .head(15)
        )
        st.bar_chart(by_user["cargas"], use_container_width=True)
        st.dataframe(by_user.reset_index(), use_container_width=True, hide_index=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Tabla ────────────────────────────────────────────────────────
    base_cols = [
        "timestamp_carga",
        "usuario_ad",
        "project_id",
        "id_catalogo",
        "nombre_archivo_original",
        "filas_procesadas",
        "estrategia_usada",
        "destino",
        "estado_carga",
    ]
    display_df = df[base_cols].copy()

    display_df.rename(columns={
        "timestamp_carga":         "Fecha/Hora",
        "usuario_ad":              "Usuario",
        "project_id":              "Proyecto",
        "id_catalogo":             "Catalogo",
        "nombre_archivo_original": "Archivo",
        "filas_procesadas":        "Filas",
        "estrategia_usada":        "Estrategia",
        "destino":                 "Destino",
        "estado_carga":            "Estado",
    }, inplace=True)

    display_df["Fecha/Hora"] = pd.to_datetime(display_df["Fecha/Hora"]).dt.strftime("%Y-%m-%d %H:%M")
    if "operation_id" in df.columns:
        display_df["Operacion"] = df["operation_id"].fillna("—").astype(str)
    elif "ruta_zip_auditoria" in df.columns:
        display_df["Operacion"] = df["ruta_zip_auditoria"].apply(_extract_operation_id)
    else:
        display_df["Operacion"] = "—"

    col_config = {
        "Fecha/Hora": st.column_config.TextColumn("Fecha/Hora", width="medium"),
        "Operacion":  st.column_config.TextColumn("Operacion",  width="small"),
        "Usuario":    st.column_config.TextColumn("Usuario",    width="small"),
        "Proyecto":   st.column_config.TextColumn("Proyecto",   width="small"),
        "Catalogo":   st.column_config.TextColumn("Catalogo",   width="medium"),
        "Archivo":    st.column_config.TextColumn("Archivo",    width="large"),
        "Filas":      st.column_config.NumberColumn("Filas",    width="small", format="%d"),
        "Estrategia": st.column_config.TextColumn("Estrategia", width="small"),
        "Destino":    st.column_config.TextColumn("Destino",    width="small"),
        "Estado":     st.column_config.TextColumn("Estado",     width="small"),
    }

    if is_admin and "ruta_zip_auditoria" in df.columns:
        display_df["Archivo ZIP"] = df["ruta_zip_auditoria"].apply(
            lambda p: os.path.basename(str(p)) if pd.notna(p) and str(p) != "None" else "—"
        )
        display_df["Ruta ZIP"] = df["ruta_zip_auditoria"].apply(
            lambda p: str(p) if pd.notna(p) and str(p) != "None" else "—"
        )
        col_config["Archivo ZIP"] = st.column_config.TextColumn("Archivo ZIP", width="large")
        col_config["Ruta ZIP"]    = st.column_config.TextColumn("Ruta ZIP",    width="large")

    st.dataframe(
        display_df,
        use_container_width=True,
        height=440,
        column_config=col_config,
        hide_index=True,
    )

    # ── Exportar CSV ─────────────────────────────────────────────────
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    csv_bytes = display_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="Exportar historial (.csv)",
        data=csv_bytes,
        file_name=f"historial_cargas_{date.today()}.csv",
        mime="text/csv",
    )


def _inject_css() -> None:
    st.markdown("""
    <style>
        #MainMenu, footer, header { visibility: hidden; }
        .block-container { padding-top: 24px !important; padding-bottom: 24px !important; }

        section[data-testid="stSidebar"] {
            background: #1C2F6E !important;
            border-right: none;
            padding-top: 20px;
        }
        section[data-testid="stSidebar"] .block-container { padding-top: 12px !important; }
        section[data-testid="stSidebar"] * { color: #E8ECF8 !important; }
        section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.12) !important; }

        /* ── Brand ── */
        section[data-testid="stSidebar"] .sidebar-brand { padding: 8px 8px 4px; border-radius: 12px; }
        section[data-testid="stSidebar"] .adm-brand-row { display:flex; align-items:center; gap:12px; margin-bottom:6px; }
        section[data-testid="stSidebar"] .adm-brand-logo-img { flex-shrink:0; }
        section[data-testid="stSidebar"] .adm-brand-text { min-width:0; }
        section[data-testid="stSidebar"] .adm-brand-subtitle { font-size:12px; color:#DCE4FF; margin-left:52px; }

        /* Overlay invisible sobre el brand */
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

        /* ── Nav visual ── */
        section[data-testid="stSidebar"] .adm-nav-visual {
            display: flex; align-items: center; gap: 11px;
            height: 44px; padding: 0 16px; border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.22);
            color: rgba(220,228,255,0.85); font-size: 14px; font-weight: 500;
            cursor: pointer; box-sizing: border-box;
        }
        section[data-testid="stSidebar"] .adm-nav-label { flex:1; white-space:nowrap; overflow:hidden; }
        section[data-testid="stSidebar"] .adm-nav-logout {
            border-color: rgba(230,80,80,0.5) !important;
            color: rgba(255,160,160,0.9) !important;
        }
        section[data-testid="stSidebar"] div:has(.adm-nav-visual) + div {
            margin-top: -44px !important; height: 44px !important;
            position: relative !important; z-index: 2 !important; overflow: hidden !important;
        }
        section[data-testid="stSidebar"] div:has(.adm-nav-visual) + div [data-testid="stButton"],
        section[data-testid="stSidebar"] div:has(.adm-nav-visual) + div button {
            width: 100% !important; height: 44px !important; min-height: 0 !important;
            opacity: 0 !important; cursor: pointer !important; margin: 0 !important; padding: 0 !important;
        }

        /* ── Colapsado: solo iconos ── */
        [data-testid="stSidebar"][aria-expanded="false"] {
            min-width: 74px !important; width: 74px !important;
            transform: none !important; margin-left: 0 !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] > div { overflow:hidden !important; width:74px !important; }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-brand-text,
        [data-testid="stSidebar"][aria-expanded="false"] .adm-brand-subtitle,
        [data-testid="stSidebar"][aria-expanded="false"] hr,
        [data-testid="stSidebar"][aria-expanded="false"] .adm-nav-label,
        [data-testid="stSidebar"][aria-expanded="false"] .mn-user-card { display: none !important; }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-nav-visual {
            justify-content: center !important; padding: 0 !important; gap: 0 !important; height: 50px !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-nav-visual img { width:28px !important; height:28px !important; }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-brand-row { justify-content:center !important; gap:0 !important; margin-bottom:0 !important; }
        [data-testid="stSidebar"][aria-expanded="false"] .adm-brand-logo-img { width:46px !important; height:46px !important; display:block !important; margin:0 auto !important; }
        [data-testid="stSidebar"][aria-expanded="false"] .sidebar-brand { padding: 36px 4px 4px !important; }
        [data-testid="stSidebar"][aria-expanded="false"] div:has(.adm-nav-visual) + div { height:50px !important; margin-top:-50px !important; }
        [data-testid="stSidebar"][aria-expanded="false"] div:has(.adm-nav-visual) + div button { height:50px !important; }
        section[data-testid="stSidebar"]::before {
            content: '';
            display: block;
            height: 4px;
            background: linear-gradient(90deg, #F5A800, #E52422);
            position: absolute;
            top: 0; left: 0; right: 0;
        }

        div[data-testid="metric-container"] {
            background: white;
            border: 1px solid #D1D9F0;
            border-radius: 10px;
            padding: 12px 16px;
        }
        .stDataFrame {
            border-radius: 10px;
            border: 1px solid #D1D9F0;
            overflow: hidden;
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
            border-radius: 4px; height: 13px; margin-bottom: 10px;
        }
        .skel-line.long   { width: 88%; }
        .skel-line.medium { width: 60%; }
        .skel-line.short  { width: 35%; }
    </style>
    """, unsafe_allow_html=True)
