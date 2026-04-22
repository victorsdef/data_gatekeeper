"""
views/login_view.py
Pantalla de login integrada con Active Directory.
"""
import streamlit as st
from auth.ldap_auth import authenticate_user
from config.settings import DEMO_MODE


def render_login() -> None:
    """Renderiza la pantalla de login."""
    _inject_login_css()

    # Centrar el formulario con columnas
    col_left, col_center, col_right = st.columns([1, 1.2, 1])

    with col_center:
        st.markdown("<div style='height: 60px'></div>", unsafe_allow_html=True)

        # Logo / encabezado
        st.markdown("""
        <div style="text-align:center; margin-bottom: 32px;">
            <div style="
                display:inline-flex; align-items:center; justify-content:center;
                width:64px; height:64px; border-radius:16px;
                background:linear-gradient(135deg, #534AB7, #7F77DD);
                margin-bottom:16px;
            ">
                <span style="font-size:28px;">🛡</span>
            </div>
            <h1 style="font-size:24px; font-weight:600; color:#1A1A2E; margin:0;">Data Gatekeeper</h1>
            <p style="font-size:13px; color:#6B7280; margin-top:4px;">
                Portal de ingesta y validación de catálogos
            </p>
        </div>
        """, unsafe_allow_html=True)

        # Formulario
        with st.form("login_form", clear_on_submit=False):
            st.markdown("<p style='font-size:13px; color:#374151; margin-bottom:4px;'>Usuario</p>",
                        unsafe_allow_html=True)
            username = st.text_input(
                label="usuario",
                placeholder="Ej: vcastro",
                label_visibility="collapsed",
            )

            st.markdown("<p style='font-size:13px; color:#374151; margin:8px 0 4px;'>Contraseña</p>",
                        unsafe_allow_html=True)
            password = st.text_input(
                label="contrasena",
                type="password",
                placeholder="••••••••",
                label_visibility="collapsed",
            )

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            submitted = st.form_submit_button(
                "Ingresar",
                use_container_width=True,
                type="primary",
            )

        # Lógica de autenticación
        if submitted:
            if not username or not password:
                st.error("Ingresa usuario y contraseña.")
            else:
                with st.spinner("Validando credenciales..."):
                    user_info = authenticate_user(username, password)

                if user_info:
                    st.session_state.authenticated = True
                    st.session_state.user_info = user_info
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas. Verifica tu usuario y contraseña.")

        # Aviso modo demo
        if DEMO_MODE:
            st.markdown("""
            <div style="
                margin-top:20px; padding:10px 14px;
                background:#FEF3C7; border:1px solid #FCD34D;
                border-radius:8px; font-size:12px; color:#92400E;
            ">
                <strong>Modo Demo activo.</strong>
                Usa <code>vcastro / demo123</code> o <code>admin / admin123</code>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("""
        <p style="text-align:center; font-size:11px; color:#9CA3AF; margin-top:24px;">
            Banco del Austro · Analítica y Gestión de Datos
        </p>
        """, unsafe_allow_html=True)


def _inject_login_css() -> None:
    st.markdown("""
    <style>
        .stApp { background-color: #F4F5F9; }
        #MainMenu, footer, header { visibility: hidden; }
        .block-container { padding-top: 0 !important; }
        div[data-testid="stForm"] {
            background: white;
            padding: 28px 32px 24px;
            border-radius: 14px;
            border: 1px solid #E5E7EB;
            box-shadow: 0 4px 24px rgba(0,0,0,0.06);
        }
        div[data-testid="stTextInput"] input {
            border-radius: 8px !important;
            border: 1px solid #D1D5DB !important;
            padding: 10px 12px !important;
            font-size: 14px !important;
        }
        div[data-testid="stTextInput"] input:focus {
            border-color: #534AB7 !important;
            box-shadow: 0 0 0 2px rgba(83, 74, 183, 0.15) !important;
        }
        div[data-testid="stFormSubmitButton"] button {
            background: #534AB7 !important;
            border: none !important;
            border-radius: 8px !important;
            font-weight: 500 !important;
            font-size: 14px !important;
            padding: 10px !important;
            transition: background 0.2s !important;
        }
        div[data-testid="stFormSubmitButton"] button:hover {
            background: #3C3489 !important;
        }
    </style>
    """, unsafe_allow_html=True)
