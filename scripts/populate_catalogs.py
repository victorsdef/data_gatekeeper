"""
scripts/populate_catalogs.py
Lee todas las BDs y tablas de SingleStore,
hace DESCRIBE a cada una y puebla catalogos_config automáticamente.

Uso:
    python populate_catalogs.py
    python populate_catalogs.py --dry-run
"""
import sys
import json
import argparse
import singlestoredb as s2

# ------------------------------------------------------------------
# CONFIGURACIÓN
# ------------------------------------------------------------------
SS_HOST     = "127.0.0.1"
SS_PORT     = 3306
SS_USER     = "root"
SS_PASSWORD = "gatekeeper123"
SS_META_DB  = "gatekeeper_meta"

# Estrategia y destino por defecto
ESTRATEGIA_DEFAULT = "overwrite"
DESTINO_DEFAULT    = "singlestore"

# Bases de datos del sistema que NO se deben mapear
BASES_IGNORAR = [
    "gatekeeper_meta",
    "information_schema",
    "performance_schema",
    "memsql",
    "cluster",
    "mysql",
]

# Tablas que terminan en fecha (historicos/respaldos) — se ignoran
import re
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


def get_databases(conn) -> list:
    """Lista todas las bases de datos disponibles."""
    cursor = conn.cursor()
    cursor.execute("SHOW DATABASES")
    dbs = [row[0] for row in cursor.fetchall()
           if row[0] not in BASES_IGNORAR]
    cursor.close()
    return dbs


def get_tables(conn, database: str) -> list:
    """Lista todas las tablas de una base de datos."""
    cursor = conn.cursor()
    cursor.execute(f"SHOW TABLES IN {database}")
    tables = [row[0] for row in cursor.fetchall()]
    cursor.close()
    return tables


def describe_table(conn, database: str, table: str) -> list:
    """Hace DESCRIBE y retorna columnas con tipo y nullable."""
    cursor = conn.cursor()
    try:
        cursor.execute(f"DESCRIBE {database}.{table}")
        rows = cursor.fetchall()
        return [{
            "nombre":   row[0],
            "tipo":     resolve_type(row[1]),
            "nullable": str(row[2]).upper() == "YES",
            "reglas":   [],
        } for row in rows]
    except Exception as e:
        print(f"    ⚠ DESCRIBE {database}.{table}: {e}")
        return []
    finally:
        cursor.close()


def catalog_id(database: str, table: str) -> str:
    """Genera catalog_id único."""
    prefix = database.upper().replace("DB_", "").replace("_", "")[:8]
    return f"{prefix}_{table.upper()}"[:100]


def project_id_from_db(database: str) -> str:
    """Genera project_id desde el nombre de la base."""
    return database.upper().replace("DB_", "").replace("_", " ").strip()


def nombre_legible(table: str) -> str:
    """Genera nombre legible desde el nombre de la tabla."""
    nombre = table
    for prefix in ["tbsc_", "tcatalogo_", "tcatalgo_", "t"]:
        if nombre.startswith(prefix):
            nombre = nombre[len(prefix):]
            break
    return nombre.replace("_", " ").title()


def get_existing_catalogs(conn) -> set:
    cursor = conn.cursor()
    cursor.execute(f"SELECT catalog_id FROM {SS_META_DB}.catalogos_config")
    result = {row[0] for row in cursor.fetchall()}
    cursor.close()
    return result


def get_existing_projects(conn) -> set:
    cursor = conn.cursor()
    cursor.execute(f"SELECT project_id FROM {SS_META_DB}.proyectos")
    result = {row[0] for row in cursor.fetchall()}
    cursor.close()
    return result


def ensure_project(conn, pid: str, database: str, dry_run: bool):
    """Inserta el proyecto si no existe."""
    existing = get_existing_projects(conn)
    if pid not in existing:
        nombre = database.replace("db_", "").replace("_", " ").title()
        if dry_run:
            print(f"    [DRY-RUN] INSERT proyecto: {pid} → {nombre}")
        else:
            cursor = conn.cursor()
            cursor.execute(
                f"INSERT IGNORE INTO {SS_META_DB}.proyectos VALUES (%s, %s, %s, 1)",
                (pid, nombre, f"Tablas de {database}")
            )
            conn.commit()
            cursor.close()
            print(f"    ✔ Proyecto creado: {pid}")


def run(dry_run: bool = False):
    print("\n=== Data Gatekeeper — Populate Catalogs ===")
    print(f"  Host:  {SS_HOST}:{SS_PORT}")
    print(f"  Modo:  {'DRY-RUN' if dry_run else 'REAL'}\n")

    try:
        conn = get_connection()
        print("✔ Conexión OK\n")
    except Exception as e:
        print(f"✗ No se pudo conectar: {e}")
        sys.exit(1)

    # Descubrir todas las bases automáticamente
    databases = get_databases(conn)
    print(f"→ Bases de datos encontradas: {databases}\n")

    existing_catalogs = get_existing_catalogs(conn)
    total_insertados = 0
    total_ignorados  = 0
    total_errores    = 0

    for database in sorted(databases):
        pid = project_id_from_db(database)
        print(f"→ Base: {database}  →  Proyecto: {pid}")

        # Asegurar que el proyecto exista
        ensure_project(conn, pid, database, dry_run)

        try:
            tables = get_tables(conn, database)
        except Exception as e:
            print(f"  ✗ Error leyendo tablas: {e}\n")
            continue

        print(f"  Tablas encontradas: {len(tables)}")

        for table in sorted(tables):
            cid = catalog_id(database, table)

            # Ignorar históricos (terminan en fecha _20260331, etc.)
            if PATRON_HISTORICO.search(table):
                print(f"  — Histórico ignorado: {table}")
                total_ignorados += 1
                continue

            # Ignorar si ya existe en catalogos_config
            if cid in existing_catalogs:
                print(f"  — Ya existe: {table}")
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

            if dry_run:
                print(f"[DRY-RUN] {len(columnas)} columnas → {cid}")
            else:
                try:
                    cursor = conn.cursor()
                    cursor.execute(f"""
                        INSERT IGNORE INTO {SS_META_DB}.catalogos_config
                        (catalog_id, project_id, nombre, descripcion,
                         base_datos, tabla_destino, destino, estrategia,
                         schema_json, activo)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """, (
                        cid, pid, nombre,
                        f"Tabla {table} de {database}",
                        database, table,
                        DESTINO_DEFAULT, ESTRATEGIA_DEFAULT,
                        schema_json,
                    ))
                    conn.commit()
                    cursor.close()
                    print(f"✔ {len(columnas)} columnas")
                    total_insertados += 1
                except Exception as e:
                    print(f"✗ {e}")
                    total_errores += 1

        print()

    conn.close()

    print("=== Resumen ===")
    print(f"  Insertados: {total_insertados}")
    print(f"  Ignorados:  {total_ignorados}")
    print(f"  Errores:    {total_errores}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra lo que haría sin insertar nada")
    args = parser.parse_args()
    run(dry_run=args.dry_run)