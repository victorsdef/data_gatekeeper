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


AUDIT_STORAGE_PATH = _setting("AUDIT_STORAGE_PATH", "/app/audit_storage")
AUDIT_STORAGE_READONLY_AFTER_WRITE = str(
    _setting("AUDIT_STORAGE_READONLY_AFTER_WRITE", "true")
).lower() == "true"
AUDIT_STORAGE_DIR_MODE = int(str(_setting("AUDIT_STORAGE_DIR_MODE", "750")), 8)
AUDIT_STORAGE_FILE_MODE = int(str(_setting("AUDIT_STORAGE_FILE_MODE", "440")), 8)
SS_HOST = _setting("SS_HOST")
SS_PORT = int(_setting("SS_PORT", 3306))
SS_USER = _setting("SS_USER")
SS_PASSWORD = _setting("SS_PASSWORD")
SS_DATABASE = _setting("SS_DATABASE")
TBL_LOG_AUDITORIA = _setting("TBL_LOG_AUDITORIA", "log_auditoria")

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
    # Drop pandas unnamed/empty trailing columns (e.g. "Unnamed: 4" from CSV/Excel)
    df = df.loc[:, ~df.columns.str.match(r"^Unnamed[:\s]*\d*$", na=False)]
    df = df.loc[:, df.columns.str.strip() != ""]

    tabla      = catalog["tabla_destino"]
    base_datos = catalog["base_datos"]
    estrategia = catalog["estrategia"]
    destino    = catalog["destino"]
    catalog_id = catalog["catalog_id"]
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

    # 1. Cold storage siempre — antes de tocar la BD para tener evidencia incluso en fallo
    zip_path = _save_cold_storage(
        file_bytes,
        filename,
        catalog_id,
        username,
        operation_id=operation_id,
        estado="pendiente",
    )

    try:
        # 2. Escritura en BD
        if destino == "singlestore":
            _write_singlestore(df, base_datos, tabla, estrategia)
        elif destino == "hive":
            _write_hive(df, base_datos, tabla, estrategia)
        else:
            raise ValueError(f"Destino desconocido: '{destino}'")

        # 3. Renombrar ZIP a "exito" y registrar log
        zip_path = _rename_cold_storage(zip_path, "exito")
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
        error_msg = user_facing_error(exc, context=destino)
        logger.exception(
            "Fallo la carga operation_id=%s catalog_id=%s hacia '%s.%s' usando estrategia '%s'.",
            operation_id,
            catalog_id,
            base_datos,
            tabla,
            estrategia,
        )
        zip_path = _rename_cold_storage(zip_path, "fallo")
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


def _write_singlestore(df: pd.DataFrame, base_datos: str, tabla: str, estrategia: str) -> None:
    tabla_fq     = f"`{base_datos}`.`{tabla}`"
    cols         = list(df.columns)
    col_names    = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql   = f"INSERT INTO {tabla_fq} ({col_names}) VALUES ({placeholders})"
    rows_data    = _df_to_tuples(df)

    with _connect_ss() as conn:
        with conn.cursor() as cur:
            if estrategia == "append":
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
                partition_col = _get_partition_col(df)
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


def _batch_insert(cur, sql: str, rows: list) -> None:
    for i in range(0, len(rows), _BATCH_SIZE):
        cur.executemany(sql, rows[i: i + _BATCH_SIZE])


def _get_partition_col(df: pd.DataFrame) -> str:
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
def _write_hive(df: pd.DataFrame, base_datos: str, tabla: str, estrategia: str) -> None:
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

    conn = pyhive_conn.Connection(
        host=hive_host, port=hive_port,
        database=base_datos, username=hive_user,
    )
    try:
        cur = conn.cursor()

        if estrategia == "overwrite":
            _hive_insert(cur, df, tabla_fq, overwrite=True)

        elif estrategia == "append":
            _hive_insert(cur, df, tabla_fq, overwrite=False)

        elif estrategia == "reproceso":
            partition_col = _get_partition_col(df)
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
def _save_cold_storage(
    file_bytes: bytes,
    filename: str,
    catalog_id: str,
    username: str,
    operation_id: str,
    estado: str = "exito",
) -> Optional[str]:
    """
    Guarda ZIP del archivo original.
    Ruta: {AUDIT_STORAGE_PATH}/{catalog_id}/{YYYYMMDD}/{timestamp}_{estado}_{user}_{operation_id}_{filename}.zip

    Estructura en disco:
      audit_storage/
        {catalog_id}/
          {YYYYMMDD}/
            20260516_143022_exito_vcastro_a1b2c3d4e5f6_roles.csv.zip
            20260516_143055_fallo_vcastro_f6e5d4c3b2a1_roles_malo.csv.zip
    """
    if not file_bytes:
        return None
    try:
        timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
        date_dir   = datetime.now().strftime("%Y%m%d")
        safe_catalog = _safe_path_part(catalog_id, default="catalogo")
        safe_user  = _safe_path_part(username, default="usuario")
        safe_name  = _safe_filename(filename)
        safe_operation = "".join(c for c in operation_id.lower() if c.isalnum())[:12] or "sinopid"
        zip_name   = f"{timestamp}_{estado}_{safe_user}_{safe_operation}_{safe_name}.zip"
        target_dir = _safe_join(AUDIT_STORAGE_PATH, safe_catalog, date_dir)

        os.makedirs(target_dir, exist_ok=True)
        _chmod_best_effort(target_dir, AUDIT_STORAGE_DIR_MODE)
        zip_path = _safe_join(target_dir, zip_name)
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        manifest = {
            "operation_id": operation_id,
            "catalog_id": catalog_id,
            "username": username,
            "original_filename": filename,
            "stored_filename": safe_name,
            "estado_inicial": estado,
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
    Renombra el ZIP de 'pendiente' a 'exito' o 'fallo' una vez conocido el resultado.
    """
    if not zip_path or not os.path.exists(zip_path):
        return zip_path
    try:
        dir_name = os.path.dirname(zip_path)
        base_name = os.path.basename(zip_path)
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
