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
            half_width_map = {
                "small": "260px",
                "medium": "360px",
                "large": "460px",
            }
            modal_width = width_map.get(width, "720px")
            modal_half_width = half_width_map.get(width, "360px")
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
                            top: 6vh !important;
                            left: max(4vw, calc(50% - {modal_half_width})) !important;
                            transform: none !important;
                            z-index: 999990 !important;
                            width: min({modal_width}, 92vw) !important;
                            min-width: 0 !important;
                            height: auto !important;
                            max-height: 88vh !important;
                            overflow-y: auto !important;
                            overflow-x: hidden !important;
                            background: #FFFFFF !important;
                            border-radius: 18px !important;
                            box-shadow: 0 26px 80px rgba(15, 23, 42, 0.24) !important;
                            border: 1px solid rgba(28, 47, 110, 0.12) !important;
                            padding: 18px 20px 16px !important;
                            box-sizing: border-box !important;
                        }}
                        div[data-testid="stVerticalBlock"]:has(
                            > div:first-child .compat-modal-anchor
                        ) > div,
                        div[data-testid="stVerticalBlock"]:has(
                            > div:first-child .compat-modal-anchor
                        ) [data-testid="stHorizontalBlock"] {{
                            width: 100% !important;
                            min-width: 0 !important;
                            max-width: 100% !important;
                            box-sizing: border-box !important;
                        }}
                        div[data-testid="stVerticalBlock"]:has(
                            > div:first-child .compat-modal-anchor
                        ) [data-testid="stElementContainer"],
                        div[data-testid="stVerticalBlock"]:has(
                            > div:first-child .compat-modal-anchor
                        ) [data-testid="stMarkdownContainer"],
                        div[data-testid="stVerticalBlock"]:has(
                            > div:first-child .compat-modal-anchor
                        ) .element-container {{
                            width: 100% !important;
                            min-width: 0 !important;
                            max-width: 100% !important;
                            box-sizing: border-box !important;
                        }}
                        div[data-testid="stVerticalBlock"]:has(
                            > div:first-child .compat-modal-anchor
                        ) [data-testid="column"] {{
                            min-width: 0 !important;
                            max-width: 100% !important;
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
