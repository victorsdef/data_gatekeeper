"""
app.py
Entry point del portal Data Gatekeeper.
Ejecutar con: streamlit run app.py
"""
import sys
import os
from pathlib import Path

# Asegurar que el directorio raíz esté en el path
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

# ------------------------------------------------------------------
# Configuración de página — debe ir ANTES de cualquier otro st.*
# ------------------------------------------------------------------
st.set_page_config(
    page_title="Data Gatekeeper · Banco del Austro",
    page_icon="🛡",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ------------------------------------------------------------------
# Imports propios (después de set_page_config)
# ------------------------------------------------------------------
from views.login_view import render_login
from views.main_view  import render_main_app


# ------------------------------------------------------------------
# Estado de sesión inicial
# ------------------------------------------------------------------
def _init_session() -> None:
    defaults = {
        "authenticated":     False,
        "user_info":         None,
        "selected_project_id": None,
        "selected_catalog":  None,
        "uploaded_df":       None,
        "uploaded_bytes":    None,
        "uploaded_name":     None,
        "validation_result": None,
        "current_step":      "upload",
        "carga_ejecutada":   False,
        "delimiter":         ",",
        "encoding":          "utf-8",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main() -> None:
    _init_session()

    if not st.session_state.authenticated:
        render_login()
    else:
        render_main_app()


if __name__ == "__main__":
    main()
