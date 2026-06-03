import pandas as pd
import pytest

from services.db_writer import _normalize_df_for_load


def test_normalize_df_for_load_casts_configured_int_from_text():
    df = pd.DataFrame({"CODIGO": ["123", "456"], "DESCRIPCION": ["A", "B"]})
    schema = {
        "columnas": [
            {"nombre": "CODIGO", "tipo": "int", "nullable": False},
            {"nombre": "DESCRIPCION", "tipo": "str", "nullable": False},
        ]
    }

    normalized = _normalize_df_for_load(df, schema)

    assert list(normalized["CODIGO"]) == [123, 456]
    assert list(normalized["DESCRIPCION"]) == ["A", "B"]


def test_normalize_df_for_load_rejects_invalid_configured_int():
    df = pd.DataFrame({"CODIGO": ["123ABC"]})
    schema = {"columnas": [{"nombre": "CODIGO", "tipo": "int", "nullable": False}]}

    with pytest.raises(ValueError, match="CODIGO"):
        _normalize_df_for_load(df, schema)
