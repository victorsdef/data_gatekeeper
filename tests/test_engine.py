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
