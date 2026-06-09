import pandas as pd
import pytest

from services.db_writer import _get_ingestion_rule, _get_reference_values, _normalize_df_for_load


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


def test_get_ingestion_rule_reads_reference_field():
    schema = {
        "regla_ingesta": {
            "modo": "evitar_duplicados",
            "campo_referencia": "fecha_proceso",
            "valor_unico_en_archivo": True,
        }
    }

    rule = _get_ingestion_rule(schema)

    assert rule["modo"] == "evitar_duplicados"
    assert rule["campo_referencia"] == "fecha_proceso"


def test_get_reference_values_requires_single_value_when_configured():
    df = pd.DataFrame({"fecha_proceso": ["20240601", "20240602"]})
    rule = {
        "modo": "reemplazar_por_campo",
        "campo_referencia": "fecha_proceso",
        "valor_unico_en_archivo": True,
    }

    with pytest.raises(ValueError, match="un solo valor"):
        _get_reference_values(df, rule)


def test_get_reference_values_allows_multiple_values_when_configured():
    df = pd.DataFrame({"fecha_proceso": ["20240601", "20240602", "20240601"]})
    rule = {
        "modo": "reemplazar_por_campo",
        "campo_referencia": "fecha_proceso",
        "valor_unico_en_archivo": False,
    }

    field, values = _get_reference_values(df, rule)

    assert field == "fecha_proceso"
    assert values == ["20240601", "20240602"]
