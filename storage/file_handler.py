"""
storage/file_handler.py
Lectura de archivos en memoria con pandas.
Soporta CSV, TXT y Excel. Detecta encoding y delimitadores automáticamente.
"""
from __future__ import annotations
from typing import Optional, Tuple, Dict, Any
import csv
import io
import pandas as pd


SUPPORTED_EXTENSIONS = [".csv", ".txt", ".xlsx", ".xls"]


def read_uploaded_file(
    file_bytes: bytes,
    filename: str,
    delimiter: str = ",",
    encoding: str = "utf-8",
    sheet_name: Optional[str] = None,
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    Lee un archivo subido y retorna (DataFrame, None) o (None, mensaje_error).

    Args:
        file_bytes:  Contenido del archivo en bytes
        filename:    Nombre original del archivo (para detectar extensión)
        delimiter:   Separador de columnas (CSV/TXT)
        encoding:    Encoding del archivo
        sheet_name:  Hoja de Excel (None = primera hoja)

    Returns:
        Tuple (DataFrame | None, error_str | None)
    """
    if not file_bytes:
        return None, "El archivo está vacío."

    ext = _get_extension(filename)

    if ext not in SUPPORTED_EXTENSIONS:
        return None, f"Formato '{ext}' no soportado. Use: {', '.join(SUPPORTED_EXTENSIONS)}"

    try:
        if ext in (".csv", ".txt"):
            return _read_csv(file_bytes, delimiter, encoding)
        elif ext in (".xlsx", ".xls"):
            return _read_excel(file_bytes, sheet_name)
    except Exception as exc:
        return None, _friendly_file_error(exc, ext)

    return None, "Formato no reconocido."


def detect_file_delimiter(file_bytes: bytes, encoding: str = "utf-8") -> str:
    """Detecta el delimitador de un CSV/TXT analizando los primeros 4 KB."""
    return _detect_delimiter(file_bytes, encoding)


def get_excel_sheets(file_bytes: bytes) -> list:
    """Retorna los nombres de las hojas de un archivo Excel."""
    try:
        xl = pd.ExcelFile(io.BytesIO(file_bytes))
        return xl.sheet_names
    except Exception:
        return []


def _detect_delimiter(file_bytes: bytes, encoding: str) -> str:
    try:
        sample = file_bytes[:4096].decode(encoding, errors="ignore")
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except Exception:
        return ","


def _read_csv(file_bytes: bytes, delimiter: str, encoding: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Lee CSV/TXT con detección automática de encoding y delimitador."""
    encodings_to_try = [encoding, "utf-8", "latin-1", "iso-8859-1", "cp1252"]
    tried = []

    for enc in encodings_to_try:
        if enc in tried:
            continue
        tried.append(enc)
        try:
            sep = delimiter if delimiter != "," else _detect_delimiter(file_bytes, enc)
            df = pd.read_csv(
                io.BytesIO(file_bytes),
                sep=sep,
                encoding=enc,
                dtype=str,
                keep_default_na=True,
                skipinitialspace=True,
            )
            df.columns = [c.strip() for c in df.columns]
            return df, None
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            return None, _friendly_file_error(exc, ".csv")

    return None, "No se pudo decodificar el archivo. Prueba con encoding: latin-1 o utf-8."


def _read_excel(file_bytes: bytes, sheet_name: Optional[str]) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Lee Excel (.xlsx o .xls)."""
    df = pd.read_excel(
        io.BytesIO(file_bytes),
        sheet_name=sheet_name or 0,
        dtype=str,
        keep_default_na=True,
    )
    df.columns = [str(c).strip() for c in df.columns]
    return df, None


def _get_extension(filename: str) -> str:
    import os
    _, ext = os.path.splitext(filename.lower())
    return ext


def _friendly_file_error(exc: Exception, ext: str) -> str:
    text = str(exc).lower()
    if "encoding" in text or "decode" in text or "codec" in text:
        return "No se pudo leer la codificación del archivo. Prueba con Latin-1, ISO-8859-1 o Windows-1252."
    if "delimiter" in text or "tokenizing" in text or "expected" in text or "fields" in text:
        return "No se pudo separar correctamente las columnas. Revisa el delimitador seleccionado."
    if ext in (".xlsx", ".xls") or "excel" in text or "workbook" in text:
        return "No se pudo leer el archivo Excel. Verifica que no esté dañado y que la hoja seleccionada tenga datos."
    if "empty" in text or "no columns" in text:
        return "El archivo no contiene columnas o filas de datos."
    return "No se pudo leer el archivo. Verifica el formato, delimitador y codificación."


def get_file_stats(df: pd.DataFrame, file_bytes: bytes) -> Dict[str, Any]:
    """Retorna estadísticas básicas del archivo para mostrar en la UI."""
    size_kb = len(file_bytes) / 1024
    return {
        "filas":     len(df),
        "columnas":  len(df.columns),
        "size_kb":   round(size_kb, 1),
        "size_str":  f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB",
        "col_names": list(df.columns),
        "null_count": int(df.isnull().sum().sum()),
    }
