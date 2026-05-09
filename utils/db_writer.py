"""
utils/db_writer.py
Escritura en BD (SingleStore / Hive), cold storage y auditoría.
En DEMO_MODE omite la escritura en BD y el log; solo ejecuta cold storage.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

from config.settings import (
    DEMO_MODE,
    AUDIT_STORAGE_PATH,
    SS_HOST, SS_PORT, SS_USER, SS_PASSWORD, SS_DATABASE,
)

_BATCH_SIZE = 500


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
      1. Escritura en BD según estrategia (omitida en DEMO_MODE)
      2. ZIP del archivo original en cold storage
      3. Log de auditoría en SingleStore (omitido en DEMO_MODE)

    Returns:
        success  : bool
        rows     : int
        zip_path : str | None
        error    : str | None
        demo     : bool  — True si corrió en modo demo (sin BD real)
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
    success = False

    try:
        # 1. Escritura en BD
        if not DEMO_MODE:
            if destino == "singlestore":
                _write_singlestore(df, base_datos, tabla, estrategia)
            elif destino == "hive":
                _write_hive(df, base_datos, tabla, estrategia)
            else:
                raise ValueError(f"Destino desconocido: '{destino}'")

        # 2. Cold storage (funciona en ambos modos)
        zip_path = _save_cold_storage(file_bytes, filename, catalog_id, username)

        # 3. Auditoría
        if not DEMO_MODE:
            _save_audit_log(
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

    except Exception as exc:
        error_msg = str(exc)
        if not DEMO_MODE:
            try:
                _save_audit_log(
                    username=username,
                    project_id=project_id,
                    catalog_id=catalog_id,
                    filename=filename,
                    rows=rows,
                    estrategia=estrategia,
                    destino=destino,
                    estado="Fallo",
                    errores_json={"error": error_msg},
                    zip_path=None,
                )
            except Exception:
                pass

    return {
        "success":  success,
        "rows":     rows,
        "zip_path": zip_path,
        "error":    error_msg,
        "demo":     DEMO_MODE,
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
                _batch_insert(cur, insert_sql, rows_data)

            elif estrategia == "overwrite":
                cur.execute("BEGIN")
                cur.execute(f"TRUNCATE TABLE {tabla_fq}")
                _batch_insert(cur, insert_sql, rows_data)
                cur.execute("COMMIT")

            elif estrategia == "reproceso":
                partition_col = _get_partition_col(df)
                fechas = df[partition_col].dropna().unique().tolist()
                ph_fechas = ", ".join(["%s"] * len(fechas))
                cur.execute("BEGIN")
                cur.execute(
                    f"DELETE FROM {tabla_fq} WHERE `{partition_col}` IN ({ph_fechas})",
                    fechas,
                )
                _batch_insert(cur, insert_sql, rows_data)
                cur.execute("COMMIT")

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
    mode = "OVERWRITE" if overwrite else "INTO"
    partition_clause = ""
    if partition:
        parts = ", ".join(f"{k}='{v}'" for k, v in partition.items())
        partition_clause = f"PARTITION ({parts})"

    def _fmt(v: Any) -> str:
        if isinstance(v, str):
            return "'" + v.replace("'", "''") + "'"
        if pd.isna(v):
            return "NULL"
        return str(v)

    batch_size = 100
    rows_vals = [
        "(" + ", ".join(_fmt(v) for v in row) + ")"
        for row in df.itertuples(index=False, name=None)
    ]

    first = True
    for i in range(0, len(rows_vals), batch_size):
        batch = rows_vals[i: i + batch_size]
        union = " UNION ALL ".join(f"SELECT * FROM (VALUES {v}) AS t" for v in batch)
        op    = ("OVERWRITE" if (overwrite and first) else "INTO")
        sql   = f"INSERT {op} TABLE {tabla} {partition_clause} {union}"
        cur.execute(sql)
        first = False


# ------------------------------------------------------------------
# Cold storage
# ------------------------------------------------------------------
def _save_cold_storage(
    file_bytes: bytes,
    filename: str,
    catalog_id: str,
    username: str,
) -> Optional[str]:
    """
    Guarda ZIP inmutable del archivo original.
    Ruta: {AUDIT_STORAGE_PATH}/{catalog_id}/{YYYYMMDD}/{timestamp}_{user}_{filename}.zip
    """
    if not file_bytes:
        return None
    try:
        timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
        date_dir   = datetime.now().strftime("%Y%m%d")
        safe_user  = "".join(c if c.isalnum() else "_" for c in username)
        safe_name  = "".join(c if (c.isalnum() or c in "._-") else "_" for c in filename)
        zip_name   = f"{timestamp}_{safe_user}_{safe_name}.zip"
        target_dir = os.path.join(AUDIT_STORAGE_PATH, catalog_id, date_dir)

        os.makedirs(target_dir, exist_ok=True)
        zip_path = os.path.join(target_dir, zip_name)

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(filename, file_bytes)

        return zip_path

    except Exception as exc:
        print(f"[COLD STORAGE ERROR] {exc}")
        return None


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
            id, timestamp_carga, usuario_ad, project_id, id_catalogo,
            nombre_archivo_original, filas_procesadas, estrategia_usada,
            destino, estado_carga, ruta_zip_auditoria
        FROM log_auditoria
        WHERE {where}
        ORDER BY timestamp_carga DESC
        LIMIT {int(limit)}
    """

    with _connect_ss() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


# ------------------------------------------------------------------
# Log de auditoría
# ------------------------------------------------------------------
def _save_audit_log(
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
    sql = """
        INSERT INTO log_auditoria (
            usuario_ad, project_id, id_catalogo, nombre_archivo_original,
            filas_procesadas, estrategia_usada, destino, estado_carga,
            errores_json, ruta_zip_auditoria
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    errores_str = json.dumps(errores_json, ensure_ascii=False) if errores_json else None

    with _connect_ss() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                username, project_id, catalog_id, filename,
                rows, estrategia, destino, estado,
                errores_str, zip_path,
            ))
