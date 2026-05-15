"""
views/login_view.py
Pantalla de login integrada con Active Directory.
"""
import base64
from pathlib import Path
import streamlit as st
from auth.ldap_auth import authenticate_user
from utils.user_service import get_or_register_user

def _logo_b64() -> str:
    logo = Path(__file__).parent.parent / "assets" / "logo.png"
    return base64.b64encode(logo.read_bytes()).decode() if logo.exists() else ""


def render_login() -> None:
    _inject_login_css()

    _, center, _ = st.columns([1, 1, 1])

    with center:
        st.markdown("<div style='height:48px'></div>", unsafe_allow_html=True)

        # Logo Banco del Austro
        b64 = _logo_b64()
        logo_html = f'<img src="data:image/png;base64,{b64}" width="72" style="margin-bottom:12px;">' if b64 else ""
        st.markdown(f"""
        <div style="text-align:center; margin-bottom:28px;">
            {logo_html}
            <div style="font-size:10px; font-weight:600; color:#6B7280; letter-spacing:2px; text-transform:uppercase; margin-bottom:8px;">banco del austro</div>
            <h2 style="font-size:18px; font-weight:700; color:#1C2F6E; margin:0;">
                Portal de Ingesta de Datos
            </h2>
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
                placeholder="Baxxxxxx",
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
                    try:
                        user_info = get_or_register_user(
                            user_info["username"],
                            user_info["nombre"],
                            user_info["email"],
                        )
                    except Exception:
                        pass  # Si la BD falla, seguimos con los datos del LDAP
                    st.session_state.authenticated = True
                    st.session_state.user_info = user_info
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos.")

        st.markdown("""
        <p style="text-align:center; font-size:11px; color:#D1D5DB; margin-top:24px;">
            Banco del Austro · Solo para uso interno · v1.0
        </p>
        """, unsafe_allow_html=True)


def _inject_login_css() -> None:
    st.markdown("""
    <style>
        .stApp { background: linear-gradient(135deg, #EEF1F8 0%, #F4F6FB 100%); }
        #MainMenu, footer, header { visibility: hidden; }
        .block-container { padding-top: 0 !important; padding-bottom: 0 !important; }

        /* Tarjeta */
        div[data-testid="stForm"] {
            background: white !important;
            border-radius: 16px !important;
            padding: 28px 28px 20px !important;
            box-shadow: 0 4px 24px rgba(28,47,110,0.10), 0 0 0 1px rgba(28,47,110,0.06) !important;
            border: none !important;
        }

        /* Labels */
        .field-label {
            font-size: 13px;
            font-weight: 600;
            color: #1C2F6E;
            margin: 0 0 6px 0;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        /* Inputs */
        div[data-testid="stTextInput"] { position: relative !important; }
        div[data-testid="stTextInput"] input {
            border-radius: 10px !important;
            border: 1.5px solid #D1D9F0 !important;
            padding: 11px 14px 11px 38px !important;
            font-size: 14px !important;
            background: #F7F9FF !important;
            color: #1C2F6E !important;
            transition: all 0.15s !important;
        }
        div[data-testid="stTextInput"] input:focus {
            border-color: #1C2F6E !important;
            background: white !important;
            box-shadow: 0 0 0 3px rgba(28,47,110,0.10) !important;
        }
        div[data-testid="stTextInput"] input::placeholder { color: #9CA3AF !important; }

        input[placeholder="Ej: vcastro"] {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%231C2F6E' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2'/%3E%3Ccircle cx='12' cy='7' r='4'/%3E%3C/svg%3E") !important;
            background-repeat: no-repeat !important;
            background-position: 13px center !important;
        }
        input[placeholder="••••••••"] {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%231C2F6E' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='11' width='18' height='11' rx='2' ry='2'/%3E%3Cpath d='M7 11V7a5 5 0 0 1 10 0v4'/%3E%3C/svg%3E") !important;
            background-repeat: no-repeat !important;
            background-position: 13px center !important;
            padding-right: 42px !important;
        }

        button[data-testid="stTextInputPasswordToggle"] {
            display: flex !important; position: absolute !important;
            right: 12px !important; bottom: 10px !important;
            background: transparent !important; border: none !important;
            padding: 0 !important; color: #9CA3AF !important;
            cursor: pointer !important; z-index: 10 !important;
        }
        button[data-testid="stTextInputPasswordToggle"]:hover { color: #1C2F6E !important; }

        /* Botón navy */
        div[data-testid="stFormSubmitButton"] button {
            background: #1C2F6E !important;
            border: none !important;
            border-radius: 10px !important;
            font-weight: 600 !important;
            font-size: 14px !important;
            padding: 12px !important;
            color: white !important;
            letter-spacing: 0.2px !important;
            transition: background 0.15s !important;
        }
        div[data-testid="stFormSubmitButton"] button:hover { background: #15245A !important; }

        /* Línea amarilla decorativa debajo del botón */
        div[data-testid="stFormSubmitButton"]::after {
            content: '';
            display: block;
            height: 3px;
            background: linear-gradient(90deg, #F5A800, #E52422);
            border-radius: 0 0 10px 10px;
            margin-top: -3px;
        }

    </style>
    """, unsafe_allow_html=True)
