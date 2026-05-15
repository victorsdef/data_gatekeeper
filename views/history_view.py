"""
views/history_view.py
Vista de historial de cargas — muestra el log de auditoría.
Admins ven todas las cargas; Publicadores solo las suyas.
"""
from __future__ import annotations

import base64
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.db_writer import get_audit_log


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


def render_history_view() -> None:
    _inject_css()

    user     = st.session_state.user_info or {}
    is_admin = user.get("rol") == "Admin"
    username = user.get("username", "")

    # ── Sidebar ──────────────────────────────────────────────────────
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
            <div style="font-size:10px;color:#7A8EC0;margin-left:48px;">Historial de Cargas</div>
        </div>
        """, unsafe_allow_html=True)
        st.divider()

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

        st.divider()
        if st.button("← Volver al portal", use_container_width=True, key="hist_back_sidebar"):
            st.session_state.current_view = "upload"
            st.rerun()
        if st.button("Cerrar sesión", use_container_width=True, key="hist_logout"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

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
        st.error(f"Error al consultar el historial: {exc}")
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

    # ── Tabla ────────────────────────────────────────────────────────
    display_df = df[[
        "timestamp_carga",
        "usuario_ad",
        "project_id",
        "id_catalogo",
        "nombre_archivo_original",
        "filas_procesadas",
        "estrategia_usada",
        "destino",
        "estado_carga",
    ]].copy()

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

    st.dataframe(
        display_df,
        use_container_width=True,
        height=440,
        column_config={
            "Fecha/Hora": st.column_config.TextColumn("Fecha/Hora", width="medium"),
            "Usuario":    st.column_config.TextColumn("Usuario",    width="small"),
            "Proyecto":   st.column_config.TextColumn("Proyecto",   width="small"),
            "Catalogo":   st.column_config.TextColumn("Catalogo",   width="medium"),
            "Archivo":    st.column_config.TextColumn("Archivo",    width="large"),
            "Filas":      st.column_config.NumberColumn("Filas",    width="small", format="%d"),
            "Estrategia": st.column_config.TextColumn("Estrategia", width="small"),
            "Destino":    st.column_config.TextColumn("Destino",    width="small"),
            "Estado":     st.column_config.TextColumn("Estado",     width="small"),
        },
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
        section[data-testid="stSidebar"] button[kind="primary"],
        section[data-testid="stSidebar"] button[kind="secondary"] {
            background: rgba(255,255,255,0.10) !important;
            border: 1px solid rgba(255,255,255,0.20) !important;
            color: white !important;
            border-radius: 8px !important;
            font-weight: 500 !important;
        }
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
