import pandas as pd

from dg_validators.engine import validate_dataframe


def test_validate_dataframe_accepts_valid_rows():
    df = pd.DataFrame(
        {
            "codigo": ["1", "2"],
            "estado": ["A", "I"],
            "monto": ["10.5", "0"],
        }
    )
    schema = {
        "columnas": [
            {"nombre": "codigo", "tipo": "int", "nullable": False},
            {
                "nombre": "estado",
                "tipo": "str",
                "nullable": False,
                "reglas": [{"tipo": "isin", "valor": ["A", "I"]}],
            },
            {
                "nombre": "monto",
                "tipo": "float",
                "nullable": False,
                "reglas": [{"tipo": "gte", "valor": 0}],
            },
        ]
    }

    result = validate_dataframe(df, schema)

    assert result.success is True
    assert result.error_count == 0


def test_validate_dataframe_reports_structure_and_business_errors():
    schema = {
        "columnas": [
            {"nombre": "codigo", "tipo": "int", "nullable": False},
            {"nombre": "estado", "tipo": "str", "nullable": False},
        ]
    }

    result = validate_dataframe(pd.DataFrame({"codigo": ["x"], "extra": ["1"]}), schema)

    assert result.success is False
    assert {error.regla for error in result.errors} == {
        "columna_requerida",
        "columna_no_esperada",
    }


def test_validate_dataframe_reports_regex_errors():
    df = pd.DataFrame({"codigo": ["ABC-123", "malo"]})
    schema = {
        "columnas": [
            {
                "nombre": "codigo",
                "tipo": "str",
                "nullable": False,
                "reglas": [{"tipo": "regex", "valor": r"^[A-Z]{3}-\d{3}$"}],
            }
        ]
    }

    result = validate_dataframe(df, schema)

    assert result.success is False
    assert result.error_count == 1
    assert result.errors[0].regla == "regex"


def test_validate_dataframe_reports_invalid_regex_definition():
    df = pd.DataFrame({"codigo": ["ABC-123"]})
    schema = {
        "columnas": [
            {
                "nombre": "codigo",
                "tipo": "str",
                "nullable": False,
                "reglas": [{"tipo": "regex", "valor": "["}],
            }
        ]
    }

    result = validate_dataframe(df, schema)

    assert result.success is False
    assert result.error_count == 1
    assert result.errors[0].regla == "regex_invalido"


def test_validate_dataframe_merges_multiple_domain_rules():
    df = pd.DataFrame({"regional": ["HOLA", "AUSTRO"]})
    schema = {
        "columnas": [
            {
                "nombre": "regional",
                "tipo": "str",
                "nullable": False,
                "reglas": [
                    {"tipo": "isin", "valor": ["AUSTRO", "COSTA 1"]},
                    {"tipo": "isin", "valor": ["HOLA"]},
                ],
            }
        ]
    }

    result = validate_dataframe(df, schema)

    assert result.success is True
    assert result.error_count == 0


def test_validate_dataframe_accepts_valid_ingestion_rule():
    df = pd.DataFrame({"periodo": ["202607", "202607"], "codigo": ["1", "2"]})
    schema = {
        "columnas": [
            {"nombre": "periodo", "tipo": "str", "nullable": False},
            {"nombre": "codigo", "tipo": "int", "nullable": False},
        ],
        "regla_ingesta": {
            "modo": "evitar_duplicados",
            "campo_referencia": "periodo",
            "valor_unico_en_archivo": True,
        },
    }

    result = validate_dataframe(df, schema)

    assert result.success is True
    assert result.error_count == 0


def test_validate_dataframe_reports_multiple_ingestion_values():
    df = pd.DataFrame({"periodo": ["202606", "202607"], "codigo": ["1", "2"]})
    schema = {
        "columnas": [
            {"nombre": "periodo", "tipo": "str", "nullable": False},
            {"nombre": "codigo", "tipo": "int", "nullable": False},
        ],
        "regla_ingesta": {
            "modo": "reemplazar_por_campo",
            "campo_referencia": "periodo",
            "valor_unico_en_archivo": True,
        },
    }

    result = validate_dataframe(df, schema)

    assert result.success is False
    assert result.error_count == 1
    assert result.errors[0].regla == "regla_ingesta"
    assert result.errors[0].columna == "periodo"
