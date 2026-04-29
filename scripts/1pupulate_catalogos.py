"""
scripts/populate_catalogs.py
Lee todas las BDs y tablas de SingleStore,
hace DESCRIBE a cada una y puebla catalogos_config automáticamente.

Uso:
    python populate_catalogs.py
    python populate_catalogs.py --dry-run
    python populate_catalogs.py --only-db nombre_base
"""
import sys
import json
import argparse
import re
import singlestoredb as s2

# ------------------------------------------------------------------
# CONFIGURACIÓN
# ------------------------------------------------------------------
SS_HOST     = "127.0.0.1"
SS_PORT     = 3306
SS_USER     = "root"
SS_PASSWORD = "gatekeeper123"
SS_META_DB  = "gatekeeper_meta"

ESTRATEGIA_DEFAULT = "overwrite"
DESTINO_DEFAULT    = "singlestore"
BATCH_SIZE         = 200
MAX_COLS           = 300

BASES_IGNORAR = {
    "gatekeeper_meta",
    "information_schema",
    "performance_schema",
    "memsql",
    "cluster",
    "mysql",
}

PATRON_HISTORICO = re.compile(r'_\d{6,8}$')

# ------------------------------------------------------------------
# Mapa de tipos SingleStore → Python
# ------------------------------------------------------------------
SS_TYPE_MAP = {
    "tinyint":    "int",
    "smallint":   "int",
    "mediumint":  "int",
    "int":        "int",
    "integer":    "int",
    "bigint":     "int",
    "float":      "float",
    "double":     "float",
    "decimal":    "float",
    "numeric":    "float",
    "real":       "float",
    "char":       "str",
    "varchar":    "str",
    "text":       "str",
    "tinytext":   "str",
    "mediumtext": "str",
    "longtext":   "str",
    "date":       "str",
    "datetime":   "str",
    "timestamp":  "str",
    "time":       "str",
    "year":       "str",
    "json":       "str",
    "string":     "str",
}


def resolve_type(ss_type: str) -> str:
    base = ss_type.lower().split("(")[0].strip()
    return SS_TYPE_MAP.get(base, "str")


def get_connection():
    return s2.connect(
        host=SS_HOST,
        port=SS_PORT,
        user=SS_USER,
        password=SS_PASSWORD,
    )


# ------------------------------------------------------------------
# Generadores — nunca cargan todo en memoria
# ------------------------------------------------------------------
def iter_databases(conn):
    """
    Itera las bases de datos disponibles fila a fila,
    ordenadas por SingleStore directamente en la query.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT SCHEMA_NAME
        FROM information_schema.SCHEMATA
        ORDER BY SCHEMA_NAME
    """)
    for row in cursor:
        if row[0] not in BASES_IGNORAR:
            yield row[0]
    cursor.close()


def iter_tables(conn, database: str):
    """
    Itera las tablas de una base de datos fila a fila,
    ordenadas por SingleStore directamente en la query.
    Sin list(), sin sorted() — RAM constante.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT TABLE_NAME
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s
        ORDER BY TABLE_NAME
    """, (database,))
    for row in cursor:
        yield row[0]
    cursor.close()


def describe_table(conn, database: str, table: str) -> list:
    """Hace DESCRIBE y retorna columnas con tipo y nullable (máx MAX_COLS)."""
    cursor = conn.cursor()
    try:
        cursor.execute(f"DESCRIBE {database}.{table}")
        rows = cursor.fetchall()
        columnas = [{
            "nombre":   row[0],
            "tipo":     resolve_type(row[1]),
            "nullable": str(row[2]).upper() == "YES",
            "reglas":   [],
        } for row in rows]
        return columnas[:MAX_COLS]
    except Exception as e:
        print(f"    ⚠ DESCRIBE {database}.{table}: {e}")
        return []
    finally:
        cursor.close()


# ------------------------------------------------------------------
# Helpers de nomenclatura
# ------------------------------------------------------------------
def catalog_id(database: str, table: str) -> str:
    prefix = database.upper().replace("DB_", "").replace("_", "")[:8]
    return f"{prefix}_{table.upper()}"[:100]


def project_id_from_db(database: str) -> str:
    return database.upper().replace("DB_", "").replace("_", " ").strip()


def nombre_legible(table: str) -> str:
    nombre = table
    for prefix in ["tbsc_", "tcatalogo_", "tcatalgo_", "t"]:
        if nombre.startswith(prefix):
            nombre = nombre[len(prefix):]
            break
    return nombre.replace("_", " ").title()


# ------------------------------------------------------------------
# INSERT IGNORE directo — sin pre-cargar sets en memoria
# ------------------------------------------------------------------
def ensure_project(conn, pid: str, database: str, dry_run: bool):
    """Inserta el proyecto si no existe, usando INSERT IGNORE."""
    nombre = database.replace("db_", "").replace("_", " ").title()
    if dry_run:
        print(f"    [DRY-RUN] INSERT IGNORE proyecto: {pid} → {nombre}")
        return
    cursor = conn.cursor()
    cursor.execute(
        f"INSERT IGNORE INTO {SS_META_DB}.proyectos VALUES (%s, %s, %s, 1)",
        (pid, nombre, f"Tablas de {database}")
    )
    conn.commit()
    cursor.close()


def flush_batch(conn, batch: list, dry_run: bool):
    """Inserta un lote de registros en catalogos_config."""
    if not batch:
        return
    if dry_run:
        print(f"    [DRY-RUN] flush_batch → {len(batch)} registros")
        return
    cursor = conn.cursor()
    cursor.executemany(f"""
        INSERT IGNORE INTO {SS_META_DB}.catalogos_config
        (catalog_id, project_id, nombre, descripcion,
         base_datos, tabla_destino, destino, estrategia,
         schema_json, activo)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
    """, batch)
    conn.commit()
    cursor.close()


# ------------------------------------------------------------------
# Lógica principal
# ------------------------------------------------------------------
def run(dry_run: bool = False, only_db: str = None):
    print("\n=== Data Gatekeeper — Populate Catalogs ===")
    print(f"  Host:  {SS_HOST}:{SS_PORT}")
    print(f"  Modo:  {'DRY-RUN' if dry_run else 'REAL'}")
    if only_db:
        print(f"  Filtro: solo base '{only_db}'")
    print()

    try:
        conn = get_connection()
        print("✔ Conexión OK\n")
    except Exception as e:
        print(f"✗ No se pudo conectar: {e}")
        sys.exit(1)

    total_insertados = 0
    total_ignorados  = 0
    total_errores    = 0

    databases = iter_databases(conn)
    if only_db:
        databases = (db for db in databases if db == only_db)

    for database in databases:          # ← ya viene ordenado de SingleStore
        pid = project_id_from_db(database)
        print(f"→ Base: {database}  →  Proyecto: {pid}")

        ensure_project(conn, pid, database, dry_run)

        batch = []

        try:
            for table in iter_tables(conn, database):   # ← ya viene ordenado, sin list()
                cid = catalog_id(database, table)

                # Ignorar históricos
                if PATRON_HISTORICO.search(table):
                    print(f"  — Histórico ignorado: {table}")
                    total_ignorados += 1
                    continue

                print(f"  → {table}", end=" ... ")
                columnas = describe_table(conn, database, table)

                if not columnas:
                    print("✗ sin columnas")
                    total_errores += 1
                    continue

                schema_json = json.dumps({"columnas": columnas}, ensure_ascii=False)
                nombre      = nombre_legible(table)

                batch.append((
                    cid, pid, nombre,
                    f"Tabla {table} de {database}",
                    database, table,
                    DESTINO_DEFAULT, ESTRATEGIA_DEFAULT,
                    schema_json,
                ))

                print(f"{'[DRY-RUN] ' if dry_run else ''}en batch ({len(columnas)} cols)")

                # Flush cada BATCH_SIZE registros
                if len(batch) >= BATCH_SIZE:
                    try:
                        flush_batch(conn, batch, dry_run)
                        total_insertados += len(batch)
                        print(f"  ✔ Batch de {len(batch)} insertado")
                    except Exception as e:
                        print(f"  ✗ Error en batch: {e}")
                        total_errores += len(batch)
                    batch = []

        except Exception as e:
            print(f"  ✗ Error leyendo tablas de {database}: {e}\n")
            continue

        # Flush del remanente al terminar la base
        if batch:
            try:
                flush_batch(conn, batch, dry_run)
                total_insertados += len(batch)
                print(f"  ✔ Batch final de {len(batch)} insertado")
            except Exception as e:
                print(f"  ✗ Error en batch final: {e}")
                total_errores += len(batch)
            batch = []

        print()

    conn.close()

    print("=== Resumen ===")
    print(f"  Insertados: {total_insertados}")
    print(f"  Ignorados:  {total_ignorados}")
    print(f"  Errores:    {total_errores}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Muestra lo que haría sin insertar nada"
    )
    parser.add_argument(
        "--only-db", default=None,
        help="Procesar solo esta base de datos (para paralelizar)"
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run, only_db=args.only_db)