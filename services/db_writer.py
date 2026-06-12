"""
services/db_writer.py
Escritura en BD (SingleStore / Hive), cold storage y auditoría.
"""
from __future__ import annotations

import io
import hashlib
import json
import os
import uuid
import zipfile
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

from config import settings
from utils.error_messages import user_facing_error
from utils.logging_utils import get_logger
from services.notifier import notify_load_event


def _setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, os.getenv(name, default))


def _is_user_actionable_load_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return any(
        token in text
        for token in (
            "regla de ingesta",
            "no se cargo el archivo para evitar duplicados",
            "columna referencial",
            "debe tener un solo valor",
            "valores invalidos detectados",
            "no se pudo convertir la columna",
        )
    )


AUDIT_STORAGE_PATH = _setting("AUDIT_STORAGE_PATH", "/app/audit_storage")
AUDIT_STORAGE_READONLY_AFTER_WRITE = str(
    _setting("AUDIT_STORAGE_READONLY_AFTER_WRITE", "true")
).lower() == "true"
AUDIT_STORE_FAILED_FILES = str(
    _setting("AUDIT_STORE_FAILED_FILES", "false")
).lower() == "true"
AUDIT_STORAGE_DIR_MODE = int(str(_setting("AUDIT_STORAGE_DIR_MODE", "750")), 8)
AUDIT_STORAGE_FILE_MODE = int(str(_setting("AUDIT_STORAGE_FILE_MODE", "440")), 8)
SS_HOST = _setting("SS_HOST")
SS_PORT = int(_setting("SS_PORT", 3306))
SS_USER = _setting("SS_USER")
SS_PASSWORD = _setting("SS_PASSWORD")
SS_DATABASE = _setting("SS_DATABASE")
TBL_LOG_AUDITORIA = _setting("TBL_LOG_AUDITORIA", "log_auditoria")
HIVE_ENABLED = str(_setting("HIVE_ENABLED", "true")).lower() == "true"

_BATCH_SIZE = 500
logger = get_logger(__name__)


# ------------------------------------------------------------------
# Función pública principal
# ------------------------------------------------------------------
def execute_load(
    df: pd.DataFrame,
    file_bytes: bytes,
    filename: str,
    catalog: Dict[str, Any],
    username: str,
    project_id: str = "",
    project_name: str = "",
) -> Dict[str, Any]:
    """
    Pipeline completo de carga:
      1. Escritura en BD según estrategia
      2. ZIP del archivo original en cold storage
      3. Log de auditoría en SingleStore

    Returns:
        success  : bool
        rows     : int
        zip_path : str | None
        error    : str | None
    """
    tabla      = catalog["tabla_destino"]
    base_datos = catalog["base_datos"]
    estrategia = catalog["estrategia"]
    destino    = catalog["destino"]
    catalog_id = catalog["catalog_id"]
    # Drop pandas unnamed/empty trailing columns (e.g. "Unnamed: 4" from CSV/Excel)
    df = df.loc[:, ~df.columns.str.match(r"^Unnamed[:\s]*\d*$", na=False)]
    df = df.loc[:, df.columns.str.strip() != ""]
    rows       = len(df)

    zip_path:  Optional[str] = None
    error_msg: Optional[str] = None
    technical_error: Optional[str] = None
    success = False
    operation_id = uuid.uuid4().hex[:12]

    logger.info(
        "Inicio de carga operation_id=%s catalog_id=%s project_id=%s destino=%s estrategia=%s rows=%s usuario=%s archivo=%s",
        operation_id,
        catalog_id,
        project_id,
        destino,
        estrategia,
        rows,
        username,
        filename,
    )

    try:
        schema_config = catalog.get("schema", {})
        df = _normalize_df_for_load(df, schema_config)
        rows = len(df)

        # 2. Escritura en BD
        if destino == "singlestore":
            _write_singlestore(df, base_datos, tabla, estrategia, schema_config)
        elif destino == "hive":
            if not HIVE_ENABLED:
                raise ValueError("Hive está deshabilitado en la configuración.")
            _write_hive(df, base_datos, tabla, estrategia, schema_config)
        else:
            raise ValueError(f"Destino desconocido: '{destino}'")

        # 3. Guardar ZIP exitoso y registrar log
        zip_path = _save_cold_storage(
            file_bytes,
            filename,
            catalog_id,
            catalog.get("nombre") or catalog_id,
            tabla,
            username,
            operation_id=operation_id,
            estado="exito",
            project_id=project_id,
            project_name=project_name,
        )
        _save_audit_log(
            operation_id=operation_id,
            username=username,
            project_id=project_id,
            catalog_id=catalog_id,
            filename=filename,
            rows=rows,
            estrategia=estrategia,
            destino=destino,
            estado="Exito",
            errores_json=None,
            zip_path=zip_path,
        )

        success = True
        logger.info(
            "Carga completada operation_id=%s catalog_id=%s rows=%s zip_path=%s",
            operation_id,
            catalog_id,
            rows,
            zip_path,
        )
        notify_load_event(
            "load_succeeded",
            {
                "operation_id": operation_id,
                "catalog_id": catalog_id,
                "project_id": project_id,
                "usuario": username,
                "destino": destino,
                "estrategia": estrategia,
                "rows": rows,
                "zip_path": zip_path,
            },
        )

    except Exception as exc:
        technical_error = str(exc)
        error_msg = technical_error if _is_user_actionable_load_error(exc) else user_facing_error(exc, context=destino)
        logger.exception(
            "Fallo la carga operation_id=%s catalog_id=%s hacia '%s.%s' usando estrategia '%s'.",
            operation_id,
            catalog_id,
            base_datos,
            tabla,
            estrategia,
        )
        if AUDIT_STORE_FAILED_FILES:
            zip_path = _save_cold_storage(
                file_bytes,
                filename,
                catalog_id,
                catalog.get("nombre") or catalog_id,
                tabla,
                username,
                operation_id=operation_id,
                estado="fallo",
                project_id=project_id,
                project_name=project_name,
            )
        try:
            _save_audit_log(
                operation_id=operation_id,
                username=username,
                project_id=project_id,
                catalog_id=catalog_id,
                filename=filename,
                rows=rows,
                estrategia=estrategia,
                destino=destino,
                estado="Fallo",
                errores_json={
                    "error": error_msg,
                    "technical_error": technical_error,
                },
                zip_path=zip_path,
            )
        except Exception:
            pass
        notify_load_event(
            "load_failed",
            {
                "operation_id": operation_id,
                "catalog_id": catalog_id,
                "project_id": project_id,
                "usuario": username,
                "destino": destino,
                "estrategia": estrategia,
                "rows": rows,
                "archivo": filename,
                "error": error_msg,
                "technical_error": technical_error,
                "zip_path": zip_path,
            },
        )

    return {
        "operation_id": operation_id,
        "success":  success,
        "rows":     rows,
        "zip_path": zip_path,
        "error":    error_msg,
        "technical_error": technical_error,
    }


# ------------------------------------------------------------------
# SingleStore
# ------------------------------------------------------------------
def _connect_ss():
    import singlestoredb as s2
    return s2.connect(
        host=SS_HOST, port=SS_PORT,
        user=SS_USER, password=SS_PASSWORD,
        database=SS_DATABASE,
    )


def _write_singlestore(
    df: pd.DataFrame,
    base_datos: str,
    tabla: str,
    estrategia: str,
    schema_config: Optional[Dict[str, Any]] = None,
) -> None:
    tabla_fq     = f"`{base_datos}`.`{tabla}`"
    cols         = list(df.columns)
    col_names    = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql   = f"INSERT INTO {tabla_fq} ({col_names}) VALUES ({placeholders})"
    rows_data    = _df_to_tuples(df)
    ingestion_rule = _get_ingestion_rule(schema_config)

    with _connect_ss() as conn:
        with conn.cursor() as cur:
            if _is_avoid_duplicates_rule(ingestion_rule):
                reference_col, reference_values = _get_reference_values(df, ingestion_rule)
                _ensure_no_existing_reference_values_ss(cur, tabla_fq, reference_col, reference_values)
                _batch_insert(cur, insert_sql, rows_data)

            elif _is_replace_by_field_rule(ingestion_rule):
                reference_col, reference_values = _get_reference_values(df, ingestion_rule)
                cur.execute("BEGIN")
                try:
                    _delete_reference_values_ss(cur, tabla_fq, reference_col, reference_values)
                    _batch_insert(cur, insert_sql, rows_data)
                    cur.execute("COMMIT")
                except Exception:
                    cur.execute("ROLLBACK")
                    raise

            elif estrategia == "append":
                # Sin transacción — append es acumulativo y tolera reintentos
                _batch_insert(cur, insert_sql, rows_data)

            elif estrategia == "overwrite":
                # Atómico: si el INSERT falla después del TRUNCATE, el ROLLBACK
                # protege la tabla — no queda vacía en producción
                cur.execute("BEGIN")
                try:
                    cur.execute(f"TRUNCATE TABLE {tabla_fq}")
                    _batch_insert(cur, insert_sql, rows_data)
                    cur.execute("COMMIT")
                except Exception:
                    cur.execute("ROLLBACK")
                    raise

            elif estrategia == "reproceso":
                # Atómico por fechas: borra solo las fechas del archivo y reinserta
                # Si falla, ROLLBACK deja los datos históricos intactos
                partition_col = _get_partition_col(df, schema_config)
                fechas = df[partition_col].dropna().unique().tolist()
                ph_fechas = ", ".join(["%s"] * len(fechas))
                cur.execute("BEGIN")
                try:
                    cur.execute(
                        f"DELETE FROM {tabla_fq} WHERE `{partition_col}` IN ({ph_fechas})",
                        fechas,
                    )
                    _batch_insert(cur, insert_sql, rows_data)
                    cur.execute("COMMIT")
                except Exception:
                    cur.execute("ROLLBACK")
                    raise

            else:
                raise ValueError(f"Estrategia no soportada en SingleStore: '{estrategia}'")


def _df_to_tuples(df: pd.DataFrame) -> list:
    return [
        tuple(None if (not isinstance(v, str) and pd.isna(v)) else v
              for v in row)
        for row in df.itertuples(index=False, name=None)
    ]


def _get_ingestion_rule(schema_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(schema_config, dict):
        return {"modo": "sin_regla"}
    rule = schema_config.get("regla_ingesta") or {}
    if not isinstance(rule, dict):
        return {"modo": "sin_regla"}
    mode = str(rule.get("modo") or "sin_regla").strip().lower()
    if mode not in {"sin_regla", "evitar_duplicados", "reemplazar_por_campo"}:
        mode = "sin_regla"
    return {
        "modo": mode,
        "campo_referencia": str(rule.get("campo_referencia") or "").strip(),
        "valor_unico_en_archivo": bool(rule.get("valor_unico_en_archivo", True)),
    }


def _is_avoid_duplicates_rule(rule: Dict[str, Any]) -> bool:
    return str(rule.get("modo") or "") == "evitar_duplicados"


def _is_replace_by_field_rule(rule: Dict[str, Any]) -> bool:
    return str(rule.get("modo") or "") == "reemplazar_por_campo"


def _get_reference_values(df: pd.DataFrame, rule: Dict[str, Any]) -> tuple[str, list]:
    reference_col = str(rule.get("campo_referencia") or "").strip()
    if not reference_col:
        raise ValueError("La regla de ingesta requiere un campo referencial.")
    if reference_col not in df.columns:
        raise ValueError(
            f"La columna referencial '{reference_col}' no existe en el archivo cargado."
        )

    series = df[reference_col]
    non_blank = series[~_blank_mask(series)]
    values = non_blank.drop_duplicates().tolist()
    if not values:
        raise ValueError(
            f"La columna referencial '{reference_col}' no tiene valores para aplicar la regla de ingesta."
        )
    if bool(rule.get("valor_unico_en_archivo", True)) and len(values) != 1:
        raise ValueError(
            f"La columna referencial '{reference_col}' debe tener un solo valor en el archivo. "
            f"Valores encontrados: {len(values)}."
        )
    return reference_col, values


def _quote_identifier(identifier: str) -> str:
    return f"`{str(identifier).replace('`', '``')}`"


def _ensure_no_existing_reference_values_ss(cur, tabla_fq: str, reference_col: str, values: list) -> None:
    placeholders = ", ".join(["%s"] * len(values))
    col_sql = _quote_identifier(reference_col)
    cur.execute(
        f"SELECT {col_sql}, COUNT(*) FROM {tabla_fq} "
        f"WHERE {col_sql} IN ({placeholders}) GROUP BY {col_sql} LIMIT 10",
        values,
    )
    existing = cur.fetchall()
    if existing:
        existing_values = ", ".join(str(row[0]) for row in existing)
        raise ValueError(
            f"No se cargo el archivo para evitar duplicados. "
            f"Ya existen registros con {reference_col}: {existing_values}."
        )


def _delete_reference_values_ss(cur, tabla_fq: str, reference_col: str, values: list) -> None:
    placeholders = ", ".join(["%s"] * len(values))
    col_sql = _quote_identifier(reference_col)
    cur.execute(
        f"DELETE FROM {tabla_fq} WHERE {col_sql} IN ({placeholders})",
        values,
    )


def _normalize_df_for_load(df: pd.DataFrame, schema_config: Dict[str, Any]) -> pd.DataFrame:
    """
    Aplica el tipo configurado por el catalogo antes de insertar.
    El tipo fisico de la tabla puede ser mas flexible, por ejemplo VARCHAR,
    pero la regla de negocio del catalogo puede exigir int/float/bool.
    """
    columnas = schema_config.get("columnas", []) if isinstance(schema_config, dict) else []
    if not columnas:
        return df

    normalized = df.copy()
    for col_def in columnas:
        col_name = str(col_def.get("nombre", "")).strip()
        if not col_name or col_name not in normalized.columns:
            continue

        tipo = str(col_def.get("tipo", "str") or "str").lower().strip()
        nullable = bool(col_def.get("nullable", True))
        series = normalized[col_name]

        if tipo == "int":
            normalized[col_name] = _cast_int_for_load(series, col_name, nullable)
        elif tipo == "float":
            normalized[col_name] = _cast_float_for_load(series, col_name, nullable)
        elif tipo == "bool":
            normalized[col_name] = _cast_bool_for_load(series, col_name, nullable)
        else:
            normalized[col_name] = series.where(~series.isna(), None)

    return normalized


def _blank_mask(series: pd.Series) -> pd.Series:
    text = series.astype("string")
    return series.isna() | text.str.strip().eq("").fillna(False)


def _raise_cast_error(col_name: str, tipo: str, bad_count: int) -> None:
    raise ValueError(
        f"No se pudo convertir la columna '{col_name}' a {tipo} para la carga. "
        f"Valores invalidos detectados: {bad_count}."
    )


def _cast_int_for_load(series: pd.Series, col_name: str, nullable: bool) -> pd.Series:
    blank = _blank_mask(series)
    if not nullable and blank.any():
        _raise_cast_error(col_name, "int", int(blank.sum()))

    numeric = pd.to_numeric(series.where(~blank), errors="coerce")
    bad = (~blank) & numeric.isna()
    decimal = numeric.notna() & ((numeric % 1) != 0)
    if bad.any() or decimal.any():
        _raise_cast_error(col_name, "int", int(bad.sum() + decimal.sum()))

    return numeric.astype("Int64")


def _cast_float_for_load(series: pd.Series, col_name: str, nullable: bool) -> pd.Series:
    blank = _blank_mask(series)
    if not nullable and blank.any():
        _raise_cast_error(col_name, "float", int(blank.sum()))

    numeric = pd.to_numeric(series.where(~blank), errors="coerce")
    bad = (~blank) & numeric.isna()
    if bad.any():
        _raise_cast_error(col_name, "float", int(bad.sum()))

    return numeric.astype("Float64")


def _cast_bool_for_load(series: pd.Series, col_name: str, nullable: bool) -> pd.Series:
    blank = _blank_mask(series)
    if not nullable and blank.any():
        _raise_cast_error(col_name, "bool", int(blank.sum()))

    true_values = {"true", "t", "1", "yes", "y", "si", "sí"}
    false_values = {"false", "f", "0", "no", "n"}

    def parse_one(value: Any) -> Optional[bool]:
        if pd.isna(value):
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in true_values:
            return True
        if text in false_values:
            return False
        return None

    parsed = series.where(~blank).map(parse_one)
    bad = (~blank) & parsed.isna()
    if bad.any():
        _raise_cast_error(col_name, "bool", int(bad.sum()))

    return parsed.astype("boolean")


def _batch_insert(cur, sql: str, rows: list) -> None:
    for i in range(0, len(rows), _BATCH_SIZE):
        cur.executemany(sql, rows[i: i + _BATCH_SIZE])


def _get_partition_col(df: pd.DataFrame, schema_config: Optional[Dict[str, Any]] = None) -> str:
    ingestion_rule = _get_ingestion_rule(schema_config)
    reference_col = str(ingestion_rule.get("campo_referencia") or "").strip()
    if reference_col:
        if reference_col not in df.columns:
            raise ValueError(
                f"La columna referencial '{reference_col}' no existe en el archivo cargado."
            )
        return reference_col

    candidates = ["fecha_proceso", "fecha", "fecha_carga", "date"]
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(
        f"No se encontró columna de fecha para reproceso. "
        f"Candidatos esperados: {candidates}. Columnas del archivo: {list(df.columns)}"
    )


# ------------------------------------------------------------------
# Hive
# ------------------------------------------------------------------
def _write_hive(
    df: pd.DataFrame,
    base_datos: str,
    tabla: str,
    estrategia: str,
    schema_config: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        from pyhive import hive as pyhive_conn
    except ImportError:
        raise ImportError(
            "La librería 'pyhive' no está instalada. "
            "Ejecuta: pip install pyhive thrift thrift-sasl"
        )

    hive_host = os.getenv("HIVE_HOST", "localhost")
    hive_port = int(os.getenv("HIVE_PORT", "10000"))
    hive_user = os.getenv("HIVE_USER", "hive")
    tabla_fq  = f"{base_datos}.{tabla}"
    ingestion_rule = _get_ingestion_rule(schema_config)

    conn = pyhive_conn.Connection(
        host=hive_host, port=hive_port,
        database=base_datos, username=hive_user,
    )
    try:
        cur = conn.cursor()

        if _is_avoid_duplicates_rule(ingestion_rule):
            reference_col, reference_values = _get_reference_values(df, ingestion_rule)
            _ensure_no_existing_reference_values_hive(cur, tabla_fq, reference_col, reference_values)
            _hive_insert(cur, df, tabla_fq, overwrite=False)

        elif _is_replace_by_field_rule(ingestion_rule):
            partition_col, values = _get_reference_values(df, ingestion_rule)
            for value in values:
                value_df = df[df[partition_col] == value]
                _hive_insert(
                    cur,
                    value_df,
                    tabla_fq,
                    overwrite=True,
                    partition={partition_col: str(value)},
                )

        elif estrategia == "overwrite":
            _hive_insert(cur, df, tabla_fq, overwrite=True)

        elif estrategia == "append":
            _hive_insert(cur, df, tabla_fq, overwrite=False)

        elif estrategia == "reproceso":
            partition_col = _get_partition_col(df, schema_config)
            for fecha in df[partition_col].dropna().unique():
                fecha_df = df[df[partition_col] == fecha]
                _hive_insert(
                    cur, fecha_df, tabla_fq,
                    overwrite=True,
                    partition={partition_col: str(fecha)},
                )

        else:
            raise ValueError(f"Estrategia no soportada en Hive: '{estrategia}'")

        conn.commit()
    finally:
        conn.close()


def _ensure_no_existing_reference_values_hive(cur, tabla_fq: str, reference_col: str, values: list) -> None:
    formatted_values = ", ".join(_hive_literal(v) for v in values)
    col_sql = f"`{str(reference_col).replace('`', '``')}`"
    cur.execute(
        f"SELECT {col_sql}, COUNT(*) FROM {tabla_fq} "
        f"WHERE {col_sql} IN ({formatted_values}) GROUP BY {col_sql} LIMIT 10"
    )
    existing = cur.fetchall()
    if existing:
        existing_values = ", ".join(str(row[0]) for row in existing)
        raise ValueError(
            f"No se cargo el archivo para evitar duplicados. "
            f"Ya existen registros con {reference_col}: {existing_values}."
        )


def _hive_literal(value: Any) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "NULL"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _hive_insert(
    cur,
    df: pd.DataFrame,
    tabla: str,
    overwrite: bool,
    partition: Optional[Dict[str, str]] = None,
) -> None:
    cols = list(df.columns)

    if partition:
        data_cols = [c for c in cols if c not in partition]
        parts = ", ".join(f"{k}='{v}'" for k, v in partition.items())
        partition_clause = f"PARTITION ({parts})"
    else:
        data_cols = cols
        partition_clause = ""

    def _fmt(v: Any) -> str:
        if isinstance(v, str):
            return "'" + v.replace("'", "''") + "'"
        try:
            if pd.isna(v):
                return "NULL"
        except (TypeError, ValueError):
            pass
        return str(v)

    col_select = ", ".join(f"`{c}`" for c in data_cols)
    all_rows   = list(df[data_cols].itertuples(index=False, name=None))

    # HiveQL: INSERT INTO/OVERWRITE TABLE t [PARTITION (...)]
    #         SELECT cols FROM (SELECT v1 AS col1, v2 AS col2
    #                           UNION ALL SELECT v1, v2 ...) _tmp
    # La primera fila lleva alias de columna; el resto no los necesita.
    first_batch = True
    for i in range(0, len(all_rows), 100):
        batch = all_rows[i: i + 100]

        first_vals = ", ".join(
            f"{_fmt(v)} AS `{c}`" for v, c in zip(batch[0], data_cols)
        )
        union_parts = [f"SELECT {first_vals}"]
        for row in batch[1:]:
            union_parts.append("SELECT " + ", ".join(_fmt(v) for v in row))

        op  = "OVERWRITE" if (overwrite and first_batch) else "INTO"
        sql = (
            f"INSERT {op} TABLE {tabla} {partition_clause} "
            f"SELECT {col_select} FROM ({' UNION ALL '.join(union_parts)}) _tmp"
        )
        cur.execute(sql)
        first_batch = False


# ------------------------------------------------------------------
# Cold storage
# ------------------------------------------------------------------
def save_validation_failure_file(
    file_bytes: bytes,
    filename: str,
    catalog: Dict[str, Any],
    username: str,
    project_id: str = "",
    project_name: str = "",
    error_count: int = 0,
) -> Optional[str]:
    """Guarda el archivo original cuando falla la validacion, si esta habilitado."""
    if not AUDIT_STORE_FAILED_FILES:
        return None
    operation_id = uuid.uuid4().hex[:12]
    zip_path = _save_cold_storage(
        file_bytes=file_bytes,
        filename=filename,
        catalog_id=str(catalog.get("catalog_id", "catalogo")),
        catalog_name=str(catalog.get("nombre") or catalog.get("catalog_id", "catalogo")),
        table_name=str(catalog.get("tabla_destino", "tabla")),
        username=username,
        operation_id=operation_id,
        estado="fallo",
        project_id=project_id,
        project_name=project_name,
    )
    logger.info(
        "Archivo con validacion fallida guardado operation_id=%s catalog_id=%s errores=%s zip_path=%s",
        operation_id,
        catalog.get("catalog_id"),
        error_count,
        zip_path,
    )
    return zip_path


def _save_cold_storage(
    file_bytes: bytes,
    filename: str,
    catalog_id: str,
    catalog_name: str,
    table_name: str,
    username: str,
    operation_id: str,
    estado: str = "exito",
    project_id: str = "",
    project_name: str = "",
) -> Optional[str]:
    """
    Guarda ZIP del archivo original.
    Ruta: {AUDIT_STORAGE_PATH}/{nombre_proyecto}/{catalogo}/{tabla}/{fecha_hora}_{estado}_{usuario}_{archivo}_{hash}.zip

    Estructura en disco:
      /data/gatekeeper/
        {proyecto}/
          {nombre_catalogo}/
            {nombre_tabla}/
              20260603_1430_exito_ba01006646_metadata.csv_a1b2c3d4e5f6.zip
    """
    if not file_bytes:
        return None
    try:
        timestamp  = datetime.now().strftime("%Y%m%d_%H%M")
        safe_project = _safe_path_part(project_name or project_id, default="sin_proyecto")
        safe_catalog = _safe_path_part(catalog_name or catalog_id, default="catalogo")
        safe_table = _safe_path_part(table_name, default="tabla")
        safe_user  = _safe_path_part(username, default="usuario")
        safe_name  = _safe_filename(filename)
        safe_estado = _safe_path_part(estado, default="exito")
        file_hash = hashlib.sha256(file_bytes).hexdigest()[:12]
        zip_name   = f"{timestamp}_{safe_estado}_{safe_user}_{safe_name}_{file_hash}.zip"
        target_dir = _safe_join(AUDIT_STORAGE_PATH, safe_project, safe_catalog, safe_table)

        os.makedirs(target_dir, exist_ok=True)
        _chmod_best_effort(target_dir, AUDIT_STORAGE_DIR_MODE)
        zip_path = _safe_join(target_dir, zip_name)
        manifest = {
            "operation_id": operation_id,
            "project_id": project_id,
            "project_name": project_name,
            "catalog_id": catalog_id,
            "catalog_name": catalog_name,
            "table_name": table_name,
            "username": username,
            "original_filename": filename,
            "stored_filename": safe_name,
            "estado": estado,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "sha256": file_hash,
            "size_bytes": len(file_bytes),
        }

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(safe_name, file_bytes)
            zf.writestr(
                "audit_manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )

        _protect_audit_file(zip_path)

        return zip_path

    except Exception as exc:
        logger.exception("Error guardando cold storage para catalogo '%s'.", catalog_id)
        return None


def _rename_cold_storage(zip_path: Optional[str], estado: str) -> Optional[str]:
    """
    Mantiene compatibilidad con nombres antiguos que incluian 'pendiente'.
    Los nombres nuevos ya incluyen estado; esto solo corrige ZIP antiguos.
    """
    if not zip_path or not os.path.exists(zip_path):
        return zip_path
    try:
        dir_name = os.path.dirname(zip_path)
        base_name = os.path.basename(zip_path)
        if "_pendiente_" not in base_name:
            return zip_path
        nuevo_name = base_name.replace("_pendiente_", f"_{estado}_", 1)
        nuevo_path = _safe_join(dir_name, nuevo_name)
        _chmod_best_effort(zip_path, 0o660)
        os.rename(zip_path, nuevo_path)
        _protect_audit_file(nuevo_path)
        return nuevo_path
    except Exception as exc:
        logger.warning("No se pudo renombrar el ZIP de auditoria '%s': %s", zip_path, exc)
        return zip_path


def _safe_path_part(value: str, default: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(value or ""))
    safe = safe.strip("._-")
    return safe[:180] or default


def _safe_filename(filename: str) -> str:
    base = os.path.basename(str(filename or "archivo"))
    return _safe_path_part(base, default="archivo")


def _safe_join(base: str, *parts: str) -> str:
    root = os.path.abspath(base)
    target = os.path.abspath(os.path.join(root, *parts))
    if os.path.commonpath([root, target]) != root:
        raise ValueError("Ruta de auditoria fuera del almacenamiento permitido.")
    return target


def _protect_audit_file(path: str) -> None:
    if AUDIT_STORAGE_READONLY_AFTER_WRITE:
        _chmod_best_effort(path, AUDIT_STORAGE_FILE_MODE)


def _chmod_best_effort(path: str, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except Exception as exc:
        logger.warning("No se pudieron aplicar permisos %s a '%s': %s", oct(mode), path, exc)


# ------------------------------------------------------------------
# Consulta de log de auditoría (para la vista de historial)
# ------------------------------------------------------------------
def get_audit_log(
    username_filter: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    estado: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """
    Retorna registros de log_auditoria como lista de dicts.
    username_filter=None → todos los usuarios (solo Admins deberían pasar None).
    """
    conditions = ["1=1"]
    params: list = []

    if username_filter:
        conditions.append("usuario_ad = %s")
        params.append(username_filter)
    if fecha_desde:
        conditions.append("DATE(timestamp_carga) >= %s")
        params.append(fecha_desde)
    if fecha_hasta:
        conditions.append("DATE(timestamp_carga) <= %s")
        params.append(fecha_hasta)
    if estado:
        conditions.append("estado_carga = %s")
        params.append(estado)

    where = " AND ".join(conditions)
    sql = f"""
        SELECT
            id, operation_id, timestamp_carga, usuario_ad, project_id, id_catalogo,
            nombre_archivo_original, filas_procesadas, estrategia_usada,
            destino, estado_carga, ruta_zip_auditoria
        FROM {TBL_LOG_AUDITORIA}
        WHERE {where}
        ORDER BY timestamp_carga DESC
        LIMIT {int(limit)}
    """

    with _connect_ss() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            logger.info(
                "Consulta de auditoria username_filter=%s estado=%s limit=%s resultados=%s",
                username_filter,
                estado,
                limit,
                len(rows),
            )
            return rows


# ------------------------------------------------------------------
# Log de auditoría
# ------------------------------------------------------------------
def _save_audit_log(
    operation_id: str,
    username: str,
    project_id: str,
    catalog_id: str,
    filename: str,
    rows: int,
    estrategia: str,
    destino: str,
    estado: str,
    errores_json: Optional[Dict],
    zip_path: Optional[str],
) -> None:
    sql = f"""
        INSERT INTO {TBL_LOG_AUDITORIA} (
            operation_id, usuario_ad, project_id, id_catalogo, nombre_archivo_original,
            filas_procesadas, estrategia_usada, destino, estado_carga,
            errores_json, ruta_zip_auditoria
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    errores_str = json.dumps(errores_json, ensure_ascii=False) if errores_json else None

    with _connect_ss() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                operation_id, username, project_id, catalog_id, filename,
                rows, estrategia, destino, estado,
                errores_str, zip_path,
            ))
