"""
dg_validators/engine.py
Motor de validación de datos (sin dependencias pesadas).
Recibe un dataframe y un schema JSON y retorna un ValidationResult.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ------------------------------------------------------------------
# Tipos de dato soportados
# ------------------------------------------------------------------
TYPE_MAP = {
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
}


# ------------------------------------------------------------------
# Estructuras de resultado
# ------------------------------------------------------------------
@dataclass
class ValidationError:
    fila: int
    columna: str
    valor: Any
    regla: str
    detalle: str
    archivo: str = ""
    hoja: str = ""
    fila_origen: int = 0


@dataclass
class ValidationResult:
    success: bool
    errors: List[ValidationError] = field(default_factory=list)
    rows_checked: int = 0
    cols_checked: int = 0

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_dataframe(self) -> pd.DataFrame:
        if not self.errors:
            return pd.DataFrame(columns=["Archivo", "Hoja", "Fila", "Columna", "Valor", "Regla", "Detalle"])
        return pd.DataFrame(
            [
                {
                    "Archivo": e.archivo,
                    "Hoja": e.hoja,
                    "Fila": e.fila,
                    "Fila origen": e.fila_origen if e.fila_origen else "",
                    "Columna": e.columna,
                    "Valor": str(e.valor),
                    "Regla": e.regla,
                    "Detalle": e.detalle,
                }
                for e in self.errors
            ]
        )


# ------------------------------------------------------------------
# Función principal
# ------------------------------------------------------------------
def validate_dataframe(df: pd.DataFrame, schema_config: Dict[str, Any]) -> ValidationResult:
    """
    Valida un DataFrame contra el schema del catálogo.

    Args:
        df:            DataFrame cargado en memoria (pandas)
        schema_config: Dict con clave "columnas" y sus reglas

    Returns:
        ValidationResult con success=True si pasa todo, o lista de errores.
    """
    result = ValidationResult(
        success=False,
        rows_checked=len(df),
        cols_checked=len(df.columns),
    )

    # ------------------------------------------------------------------
    # Paso 1: Validación estructural (columnas esperadas)
    # ------------------------------------------------------------------
    expected_cols = [c["nombre"] for c in schema_config.get("columnas", [])]
    actual_cols = list(df.columns)

    missing_cols = set(expected_cols) - set(actual_cols)
    extra_cols = set(actual_cols) - set(expected_cols)

    if missing_cols:
        for col in sorted(missing_cols):
            result.errors.append(
                ValidationError(
                    fila=0,
                    columna=col,
                    valor="—",
                    regla="columna_requerida",
                    detalle=f"La columna '{col}' no existe en el archivo.",
                )
            )

    if extra_cols:
        for col in sorted(extra_cols):
            result.errors.append(
                ValidationError(
                    fila=0,
                    columna=col,
                    valor="—",
                    regla="columna_no_esperada",
                    detalle=f"La columna '{col}' no está en el schema del catálogo.",
                )
            )

    if result.errors:
        return result

    # ------------------------------------------------------------------
    # Regla de calidad: archivo no vacío (al menos 1 fila de datos)
    # ------------------------------------------------------------------
    if df.empty:
        result.errors.append(
            ValidationError(
                fila=0,
                columna="general",
                valor="—",
                regla="archivo_vacio",
                detalle="El archivo no contiene filas de datos.",
            )
        )
        return result

    # ------------------------------------------------------------------
    # Paso 2: Validación de tipos y reglas (pandas)
    # ------------------------------------------------------------------
    result.errors.extend(_validate_ingestion_rule(df, schema_config))

    try:
        columnas_def = schema_config.get("columnas", [])
        for col_def in columnas_def:
            col_name = col_def["nombre"]
            tipo_str = col_def.get("tipo", "str")
            nullable = bool(col_def.get("nullable", False))
            reglas = col_def.get("reglas", [])

            series = df[col_name]
            coerced, bad_type_mask, type_detail = _coerce_series(series, tipo_str)

            for idx, raw_value in series[bad_type_mask].items():
                result.errors.append(
                    ValidationError(
                        fila=int(idx) + 2,
                        columna=col_name,
                        valor=raw_value,
                        regla="tipo_de_dato",
                        detalle=type_detail,
                    )
                )

            if not nullable:
                null_mask = series.isna()
                for idx, raw_value in series[null_mask].items():
                    result.errors.append(
                        ValidationError(
                            fila=int(idx) + 2,
                            columna=col_name,
                            valor=raw_value,
                            regla="no_nulo",
                            detalle=f"La columna '{col_name}' tiene un valor nulo que no está permitido.",
                        )
                    )

            for err in _apply_rules(
                series_original=series,
                series_coerced=coerced,
                col_name=col_name,
                reglas=reglas,
            ):
                result.errors.append(err)

        result.success = len(result.errors) == 0

    except Exception as exc:
        result.errors.append(
            ValidationError(
                fila=0,
                columna="general",
                valor="—",
                regla="error_inesperado",
                detalle="No se pudo completar la validación por un error interno del esquema. Contacta al administrador.",
            )
        )

    return result


def _validate_ingestion_rule(
    df: pd.DataFrame,
    schema_config: Dict[str, Any],
) -> List[ValidationError]:
    rule = schema_config.get("regla_ingesta") or {}
    if not isinstance(rule, dict):
        return []

    mode = str(rule.get("modo") or "sin_regla").strip().lower()
    if mode == "sin_regla":
        return []

    if mode not in {"evitar_duplicados", "reemplazar_por_campo"}:
        return [
            ValidationError(
                fila=0,
                columna="general",
                valor=mode,
                regla="regla_ingesta",
                detalle="La regla de ingesta configurada no es valida.",
            )
        ]

    reference_col = str(rule.get("campo_referencia") or "").strip()
    if not reference_col:
        return [
            ValidationError(
                fila=0,
                columna="general",
                valor="--",
                regla="regla_ingesta",
                detalle="La regla de ingesta requiere un campo de control.",
            )
        ]

    if reference_col not in df.columns:
        return [
            ValidationError(
                fila=0,
                columna=reference_col,
                valor="--",
                regla="regla_ingesta",
                detalle=f"El campo de control '{reference_col}' no existe en el archivo.",
            )
        ]

    series = df[reference_col]
    as_text = series.astype("string").str.strip()
    non_blank = series[series.notna() & as_text.fillna("").ne("")]
    values = non_blank.drop_duplicates().tolist()

    if not values:
        return [
            ValidationError(
                fila=0,
                columna=reference_col,
                valor="--",
                regla="regla_ingesta",
                detalle=f"El campo de control '{reference_col}' no contiene valores.",
            )
        ]

    if bool(rule.get("valor_unico_en_archivo", True)) and len(values) != 1:
        return [
            ValidationError(
                fila=0,
                columna=reference_col,
                valor=len(values),
                regla="regla_ingesta",
                detalle=(
                    f"El campo de control '{reference_col}' debe tener un solo "
                    f"valor en el archivo y se encontraron {len(values)}."
                ),
            )
        ]

    return []


def _coerce_series(series: pd.Series, tipo_str: str) -> Tuple[pd.Series, pd.Series, str]:
    tipo_str = (tipo_str or "str").lower().strip()

    if tipo_str == "int":
        coerced = pd.to_numeric(series, errors="coerce")
        bad_type_mask = series.notna() & coerced.isna()
        return coerced.astype("Int64"), bad_type_mask, "El valor no puede convertirse a entero."

    if tipo_str == "float":
        coerced = pd.to_numeric(series, errors="coerce")
        bad_type_mask = series.notna() & coerced.isna()
        return coerced.astype("Float64"), bad_type_mask, "El valor no puede convertirse a número."

    if tipo_str == "bool":
        coerced, bad_type_mask = _coerce_bool(series)
        return coerced, bad_type_mask, "El valor no puede convertirse a booleano (true/false, 1/0)."

    coerced = series.astype("string")
    return coerced, pd.Series(False, index=series.index), "—"


def _coerce_bool(series: pd.Series) -> Tuple[pd.Series, pd.Series]:
    true_values = {"true", "t", "1", "yes", "y", "si", "sí"}
    false_values = {"false", "f", "0", "no", "n"}

    def parse_one(value: Any) -> Optional[bool]:
        if pd.isna(value):
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not pd.isna(value):
            if value == 1:
                return True
            if value == 0:
                return False
        text = str(value).strip().lower()
        if text in true_values:
            return True
        if text in false_values:
            return False
        return None

    parsed = series.map(parse_one)
    bad_type_mask = series.notna() & parsed.isna()
    return parsed.astype("boolean"), bad_type_mask


def _apply_rules(
    series_original: pd.Series,
    series_coerced: pd.Series,
    col_name: str,
    reglas: List[Dict[str, Any]],
) -> List[ValidationError]:
    errors: List[ValidationError] = []
    reglas = reglas or []

    domain_values = []
    for regla in reglas:
        tipo = (regla.get("tipo") or "").lower().strip()
        if tipo != "isin":
            continue
        allowed = regla.get("valor", [])
        if isinstance(allowed, list):
            domain_values.extend(allowed)
        else:
            domain_values.append(allowed)

    if domain_values:
        allowed_set = set(domain_values)
        mask = series_original.notna() & (~series_original.isin(allowed_set))
        for idx, raw_value in series_original[mask].items():
            errors.append(
                ValidationError(
                    fila=int(idx) + 2,
                    columna=col_name,
                    valor=raw_value,
                    regla="dominio",
                    detalle=f"El valor '{raw_value}' no está dentro de los valores permitidos para '{col_name}'.",
                )
            )

    for regla in reglas:
        tipo = (regla.get("tipo") or "").lower().strip()
        if not tipo:
            continue

        if tipo == "isin":
            continue

        if tipo in ("gte", "lte"):
            limit = regla.get("valor")
            numeric = pd.to_numeric(series_coerced, errors="coerce")

            if tipo == "gte":
                mask = numeric.notna() & (numeric < limit)
                rule_name = "valor_mínimo"
                template = f"El valor '{{value}}' en '{col_name}' es menor al mínimo permitido ({limit})."
            else:
                mask = numeric.notna() & (numeric > limit)
                rule_name = "valor_máximo"
                template = f"El valor '{{value}}' en '{col_name}' es mayor al máximo permitido ({limit})."

            for idx, raw_value in series_original[mask].items():
                errors.append(
                    ValidationError(
                        fila=int(idx) + 2,
                        columna=col_name,
                        valor=raw_value,
                        regla=rule_name,
                        detalle=template.format(value=raw_value),
                    )
                )
            continue

        if tipo == "min_length":
            min_len = int(regla.get("valor", 0))
            text = series_original.astype("string")
            mask = text.notna() & (text.str.len() < min_len)
            for idx, raw_value in series_original[mask].items():
                errors.append(
                    ValidationError(
                        fila=int(idx) + 2,
                        columna=col_name,
                        valor=raw_value,
                        regla="longitud_mínima",
                        detalle=f"El valor '{raw_value}' en '{col_name}' no cumple la longitud mínima ({min_len}).",
                    )
                )
            continue

        if tipo == "str_length":
            min_l = int(regla.get("min", 0))
            max_l = int(regla.get("max", 9999))
            text = series_original.astype("string")
            lens = text.str.len()
            mask = text.notna() & (~lens.between(min_l, max_l))
            for idx, raw_value in series_original[mask].items():
                errors.append(
                    ValidationError(
                        fila=int(idx) + 2,
                        columna=col_name,
                        valor=raw_value,
                        regla="longitud",
                        detalle=f"El valor '{raw_value}' en '{col_name}' no cumple longitud entre {min_l} y {max_l}.",
                    )
                )
            continue

        if tipo == "regex":
            pattern = str(regla.get("valor", "")).strip()
            if not pattern:
                continue
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                errors.append(
                    ValidationError(
                        fila=0,
                        columna=col_name,
                        valor=pattern,
                        regla="regex_invalido",
                        detalle=f"El patrón regex configurado para '{col_name}' es inválido: {exc}.",
                    )
                )
                continue

            text = series_original.astype("string")
            mask = text.notna() & (~text.str.fullmatch(compiled, na=False))
            for idx, raw_value in series_original[mask].items():
                errors.append(
                    ValidationError(
                        fila=int(idx) + 2,
                        columna=col_name,
                        valor=raw_value,
                        regla="regex",
                        detalle=f"El valor '{raw_value}' en '{col_name}' no cumple el patrón requerido.",
                    )
                )
            continue

    return errors
