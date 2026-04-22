"""
views/login_view.py
Pantalla de login integrada con Active Directory.
"""
import streamlit as st
from auth.ldap_auth import authenticate_user
from config.settings import DEMO_MODE


def render_login() -> None:
    _inject_login_css()

    _, center, _ = st.columns([1, 1, 1])

    with center:
        st.markdown("<div style='height:60px'></div>", unsafe_allow_html=True)

        # Icono de candado
        st.markdown("""
        <div style="text-align:center; margin-bottom:20px;">
            <div style="
                display:inline-flex; align-items:center; justify-content:center;
                width:64px; height:64px; border-radius:50%;
                background:#111827;
                margin-bottom:18px;
                box-shadow: 0 4px 14px rgba(0,0,0,0.18);
            ">
                <span style="font-size:26px; filter:grayscale(1) brightness(10);">🔒</span>
            </div>
            <h1 style="font-size:22px; font-weight:700; color:#111827; margin:0;">
                Bienvenido de nuevo
            </h1>
            <p style="font-size:13px; color:#6B7280; margin:6px 0 0;">
                Ingresa tus credenciales corporativas para continuar
            </p>
        </div>
        """, unsafe_allow_html=True)

        # Tarjeta — el form con CSS
        with st.form("login_form", clear_on_submit=False):
            st.markdown("<p class='field-label'>Usuario</p>", unsafe_allow_html=True)
            username = st.text_input(
                label="usuario",
                placeholder="Ej: vcastro",
                label_visibility="collapsed",
            )

            st.markdown("<p class='field-label' style='margin-top:14px;'>Contrasena</p>",
                        unsafe_allow_html=True)
            password = st.text_input(
                label="contrasena",
                type="password",
                placeholder="••••••••",
                label_visibility="collapsed",
            )

            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            submitted = st.form_submit_button(
                "Iniciar sesión",
                use_container_width=True,
                type="primary",
            )

        if submitted:
            if not username or not password:
                st.error("Ingresa tu usuario y contraseña.")
            else:
                with st.spinner("Verificando credenciales..."):
                    user_info = authenticate_user(username, password)
                if user_info:
                    st.session_state.authenticated = True
                    st.session_state.user_info = user_info
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos.")

        if DEMO_MODE:
            st.markdown("""
            <div class="demo-hint">
                <span style="font-size:14px;">💡</span>
                <div>
                    <strong>Modo Demo</strong><br>
                    <code>vcastro / demo123</code> &nbsp;·&nbsp; <code>admin / admin123</code>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("""
        <p style="text-align:center; font-size:11px; color:#D1D5DB; margin-top:24px;">
            Banco del Austro · Solo para uso interno · v1.0
        </p>
        """, unsafe_allow_html=True)


def _inject_login_css() -> None:
    st.markdown("""
    <style>
        .stApp { background: #F3F4F6; }
        #MainMenu, footer, header { visibility: hidden; }
        .block-container { padding-top: 0 !important; padding-bottom: 0 !important; }

        /* Tarjeta = el form */
        div[data-testid="stForm"] {
            background: white !important;
            border-radius: 16px !important;
            padding: 28px 28px 20px !important;
            box-shadow: 0 2px 16px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04) !important;
            border: none !important;
        }

        /* Labels */
        .field-label {
            font-size: 13px;
            font-weight: 600;
            color: #111827;
            margin: 0 0 6px 0;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .field-icon { font-size: 13px; }

        /* Inputs — base */
        div[data-testid="stTextInput"] {
            position: relative !important;
        }
        div[data-testid="stTextInput"] input {
            border-radius: 10px !important;
            border: 1.5px solid #E5E7EB !important;
            padding: 11px 14px 11px 38px !important;
            font-size: 14px !important;
            background: #F9FAFB !important;
            color: #111827 !important;
            transition: all 0.15s !important;
        }
        div[data-testid="stTextInput"] input:focus {
            border-color: #111827 !important;
            background: white !important;
            box-shadow: 0 0 0 3px rgba(17,24,39,0.08) !important;
        }
        div[data-testid="stTextInput"] input::placeholder {
            color: #9CA3AF !important;
        }

        /* Icono de usuario (campo con placeholder "Ej: vcastro") */
        input[placeholder="Ej: vcastro"] {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%239CA3AF' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2'/%3E%3Ccircle cx='12' cy='7' r='4'/%3E%3C/svg%3E") !important;
            background-repeat: no-repeat !important;
            background-position: 13px center !important;
        }

        /* Icono de candado (campo password) */
        input[placeholder="••••••••"] {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%239CA3AF' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='11' width='18' height='11' rx='2' ry='2'/%3E%3Cpath d='M7 11V7a5 5 0 0 1 10 0v4'/%3E%3C/svg%3E") !important;
            background-repeat: no-repeat !important;
            background-position: 13px center !important;
            padding-right: 42px !important;
        }

        /* Ojo de contrasena — reposicionado dentro del input */
        button[data-testid="stTextInputPasswordToggle"] {
            display: flex !important;
            position: absolute !important;
            right: 12px !important;
            bottom: 10px !important;
            background: transparent !important;
            border: none !important;
            padding: 0 !important;
            color: #9CA3AF !important;
            cursor: pointer !important;
            z-index: 10 !important;
        }
        button[data-testid="stTextInputPasswordToggle"]:hover {
            color: #374151 !important;
        }

        /* Botón negro */
        div[data-testid="stFormSubmitButton"] button {
            background: #111827 !important;
            border: none !important;
            border-radius: 10px !important;
            font-weight: 600 !important;
            font-size: 14px !important;
            padding: 12px !important;
            color: white !important;
            letter-spacing: 0.2px !important;
            transition: background 0.15s !important;
        }
        div[data-testid="stFormSubmitButton"] button:hover {
            background: #1F2937 !important;
        }

        /* Demo hint */
        .demo-hint {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            margin-top: 12px;
            padding: 11px 14px;
            background: #F9FAFB;
            border: 1px solid #E5E7EB;
            border-radius: 10px;
            font-size: 12px;
            color: #374151;
        }
    </style>
    """, unsafe_allow_html=True)
