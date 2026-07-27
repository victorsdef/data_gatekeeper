from __future__ import annotations

from contextlib import contextmanager
from typing import Callable

import streamlit as st


def dialog(title: str, width: str = "small") -> Callable:
    """Compatibilidad entre versiones de Streamlit para ventanas modales."""
    dialog_func = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)
    if dialog_func:
        return dialog_func(title, width=width)

    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            width_map = {
                "small": "520px",
                "medium": "720px",
                "large": "920px",
            }
            modal_width = width_map.get(width, "720px")
            with st.container():
                st.markdown(
                    f"""
                    <style>
                        .stApp:has(.compat-modal-anchor)::before {{
                            content: "";
                            position: fixed;
                            inset: 0;
                            z-index: 999980;
                            background: rgba(15, 23, 42, 0.34);
                        }}
                        div[data-testid="stVerticalBlock"]:has(
                            > div:first-child .compat-modal-anchor
                        ) {{
                            position: fixed !important;
                            top: 50% !important;
                            left: 50% !important;
                            transform: translate(-50%, -50%) !important;
                            z-index: 999990 !important;
                            width: min({modal_width}, 92vw) !important;
                            height: auto !important;
                            max-height: 88vh !important;
                            overflow-y: auto !important;
                            background: #FFFFFF !important;
                            border-radius: 18px !important;
                            box-shadow: 0 26px 80px rgba(15, 23, 42, 0.24) !important;
                            border: 1px solid rgba(28, 47, 110, 0.12) !important;
                            padding: 18px 20px 16px !important;
                            box-sizing: border-box !important;
                        }}
                        .compat-modal-anchor {{
                            display: none;
                        }}
                        .compat-modal-title {{
                            font-size: 16px;
                            font-weight: 800;
                            color: #1C2F6E;
                            margin: 0 0 12px 0;
                        }}
                    </style>
                    <div class="compat-modal-anchor"></div>
                    <div class="compat-modal-title">{title}</div>
                    """,
                    unsafe_allow_html=True,
                )
                return func(*args, **kwargs)

        return wrapper

    return decorator


@contextmanager
def popover(label: str, use_container_width: bool = False):
    """Compatibilidad entre versiones para popover/expander."""
    popover_func = getattr(st, "popover", None)
    if popover_func:
        with popover_func(label, use_container_width=use_container_width):
            yield
        return

    with st.expander(label, expanded=False):
        yield
