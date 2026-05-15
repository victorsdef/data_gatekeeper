-- =============================================================
-- Data Gatekeeper — Datos iniciales
-- Carga inicial de catálogos de referencia a la base de datos.
-- Ejecutar después de 001_create_metadata_tables.sql
-- =============================================================

-- -------------------------------------------------------------
-- proyectos
-- -------------------------------------------------------------
INSERT INTO proyectos (id, nombre) VALUES
    ('COMERCIAL_BDA', 'Comercial'),
    ('CREDITOS',      'Créditos'),
    ('TARJETAS',      'Tarjetas de Débito'),
    ('CUMPLIMIENTO',  'Cumplimiento')
ON DUPLICATE KEY UPDATE nombre = VALUES(nombre);

-- -------------------------------------------------------------
-- catalogos_config — COMERCIAL_BDA
-- -------------------------------------------------------------
INSERT INTO catalogos_config
    (catalog_id, project_id, nombre, tabla_destino, estrategia, destino, schema_json)
VALUES (
    'CAT_CATALOGO_PRODUCTOS',
    'COMERCIAL_BDA',
    'Catálogo de Productos',
    'tbsc_catalogo_productos',
    'overwrite',
    'singlestore',
    '{
        "columnas": [
            {
                "nombre": "",
                "tipo": "str",
                "nullable": false,
                "reglas": [],
                "nota": "TODO: confirmar nombre real de la primera columna con DBA"
            },
            {
                "nombre": "subcategoria",
                "tipo": "str",
                "nullable": false,
                "reglas": [
                    {"tipo": "isin", "valor": ["CAPTACIONES", "COLOCACIONES"]}
                ]
            },
            {"nombre": "csubsistema",    "tipo": "str", "nullable": false, "reglas": []},
            {"nombre": "subsistema",     "tipo": "str", "nullable": false, "reglas": []},
            {"nombre": "cgrupoproducto", "tipo": "str", "nullable": false, "reglas": []},
            {"nombre": "grupoproducto",  "tipo": "str", "nullable": false, "reglas": []},
            {"nombre": "cproducto",      "tipo": "str", "nullable": false, "reglas": []},
            {"nombre": "producto",       "tipo": "str", "nullable": false, "reglas": []},
            {
                "nombre": "desc_grupo_tablero",
                "tipo": "str",
                "nullable": false,
                "reglas": [
                    {"tipo": "isin", "valor": ["CDPS", "CONSUMO", "MONETARIOS", "AHORROS",
                                               "TARJETAS", "INMOBILIARIO", "MICRO", "DIGITAL"]}
                ]
            },
            {
                "nombre": "crol",
                "tipo": "int",
                "nullable": false,
                "reglas": [
                    {"tipo": "isin", "valor": [1, 2, 3, 5]}
                ]
            },
            {
                "nombre": "rol",
                "tipo": "str",
                "nullable": false,
                "reglas": [
                    {"tipo": "isin", "valor": ["MASIVO", "MASIVO AFLUENTE", "AFLUENTE", "JEFE DE AGENCIA"]}
                ]
            },
            {
                "nombre": "fecha_proceso",
                "tipo": "str",
                "nullable": false,
                "reglas": [
                    {"tipo": "str_length", "min": 8, "max": 8}
                ]
            }
        ]
    }'
)
ON DUPLICATE KEY UPDATE
    nombre        = VALUES(nombre),
    tabla_destino = VALUES(tabla_destino),
    estrategia    = VALUES(estrategia),
    destino       = VALUES(destino),
    schema_json   = VALUES(schema_json);

-- -------------------------------------------------------------
-- catalogos_config — CREDITOS
-- -------------------------------------------------------------
INSERT INTO catalogos_config
    (catalog_id, project_id, nombre, tabla_destino, estrategia, destino, schema_json)
VALUES (
    'CAT_ROLES_CREDITO',
    'CREDITOS',
    'Roles de crédito',
    'cat_roles_credito',
    'overwrite',
    'singlestore',
    '{
        "columnas": [
            {"nombre": "cod_rol", "tipo": "int", "nullable": false, "reglas": []},
            {"nombre": "rol",     "tipo": "str", "nullable": false, "reglas": [{"tipo": "min_length", "valor": 2}]},
            {"nombre": "estado",  "tipo": "int", "nullable": false, "reglas": [{"tipo": "isin", "valor": [0, 1]}]},
            {"nombre": "peso_1",  "tipo": "int", "nullable": false, "reglas": [{"tipo": "gte", "valor": 0}]}
        ]
    }'
),
(
    'CAT_TIPO_CREDITO',
    'CREDITOS',
    'Tipos de crédito',
    'cat_tipo_credito',
    'append',
    'singlestore',
    '{
        "columnas": [
            {"nombre": "cod_tipo",    "tipo": "str",   "nullable": false, "reglas": []},
            {"nombre": "descripcion", "tipo": "str",   "nullable": false, "reglas": [{"tipo": "min_length", "valor": 3}]},
            {"nombre": "tasa_max",    "tipo": "float", "nullable": false, "reglas": [{"tipo": "gte", "valor": 0}]},
            {"nombre": "activo",      "tipo": "int",   "nullable": false, "reglas": [{"tipo": "isin", "valor": [0, 1]}]}
        ]
    }'
)
ON DUPLICATE KEY UPDATE
    nombre        = VALUES(nombre),
    tabla_destino = VALUES(tabla_destino),
    estrategia    = VALUES(estrategia),
    destino       = VALUES(destino),
    schema_json   = VALUES(schema_json);

-- -------------------------------------------------------------
-- catalogos_config — TARJETAS
-- -------------------------------------------------------------
INSERT INTO catalogos_config
    (catalog_id, project_id, nombre, tabla_destino, estrategia, destino, schema_json)
VALUES (
    'CAT_SEGMENTOS_TD',
    'TARJETAS',
    'Segmentos de tarjeta',
    'cat_segmentos_td',
    'overwrite',
    'hive',
    '{
        "columnas": [
            {"nombre": "cod_segmento",  "tipo": "str",   "nullable": false, "reglas": []},
            {"nombre": "nombre",        "tipo": "str",   "nullable": false, "reglas": [{"tipo": "min_length", "valor": 2}]},
            {"nombre": "limite_diario", "tipo": "float", "nullable": false, "reglas": [{"tipo": "gte", "valor": 0}]},
            {"nombre": "estado",        "tipo": "int",   "nullable": false, "reglas": [{"tipo": "isin", "valor": [0, 1]}]}
        ]
    }'
)
ON DUPLICATE KEY UPDATE
    nombre        = VALUES(nombre),
    tabla_destino = VALUES(tabla_destino),
    estrategia    = VALUES(estrategia),
    destino       = VALUES(destino),
    schema_json   = VALUES(schema_json);

-- -------------------------------------------------------------
-- catalogos_config — CUMPLIMIENTO
-- -------------------------------------------------------------
INSERT INTO catalogos_config
    (catalog_id, project_id, nombre, tabla_destino, estrategia, destino, schema_json)
VALUES (
    'CAT_LISTAS_CONTROL',
    'CUMPLIMIENTO',
    'Listas de control',
    'cat_listas_control',
    'reproceso',
    'singlestore',
    '{
        "columnas": [
            {"nombre": "cedula",        "tipo": "str", "nullable": false, "reglas": [{"tipo": "str_length", "min": 10, "max": 13}]},
            {"nombre": "nombres",       "tipo": "str", "nullable": false, "reglas": [{"tipo": "min_length", "valor": 3}]},
            {"nombre": "tipo_lista",    "tipo": "str", "nullable": false, "reglas": [{"tipo": "isin", "valor": ["NEGRA", "GRIS", "BLANCA"]}]},
            {"nombre": "fecha_proceso", "tipo": "str", "nullable": false, "reglas": []}
        ]
    }'
)
ON DUPLICATE KEY UPDATE
    nombre        = VALUES(nombre),
    tabla_destino = VALUES(tabla_destino),
    estrategia    = VALUES(estrategia),
    destino       = VALUES(destino),
    schema_json   = VALUES(schema_json);
