from __future__ import annotations

from typing import Callable

import streamlit as st


def dialog(title: str, width: str = "small") -> Callable:
    """Compatibilidad entre versiones de Streamlit para ventanas modales."""
    dialog_func = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)
    if dialog_func:
        return dialog_func(title, width=width)

    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            st.markdown(f"### {title}")
            with st.container():
                return func(*args, **kwargs)

        return wrapper

    return decorator
