-- =============================================================
-- Data Gatekeeper — Tablas de metadatos
-- Base de datos: gatekeeper_meta (SS_DATABASE)
-- Motor: SingleStore
-- =============================================================

-- -------------------------------------------------------------
-- 1. proyectos
-- Agrupador lógico de catálogos.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proyectos (
    id      VARCHAR(100)  NOT NULL,
    nombre  VARCHAR(255)  NOT NULL,
    PRIMARY KEY (id)
);

-- -------------------------------------------------------------
-- 2. catalogos_config
-- Define cada catálogo: a qué tabla apunta, estrategia y esquema de validación.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalogos_config (
    catalog_id      VARCHAR(100)    NOT NULL,
    project_id      VARCHAR(100)    NOT NULL,
    nombre          VARCHAR(255)    NOT NULL,
    tabla_destino   VARCHAR(255)    NOT NULL,
    estrategia      VARCHAR(50)     NOT NULL,   -- append | overwrite | reproceso
    destino         VARCHAR(50)     NOT NULL,   -- singlestore | hive
    schema_json     JSON            NOT NULL,   -- {"columnas": [...]}
    activo          TINYINT(1)      NOT NULL DEFAULT 1,
    PRIMARY KEY (catalog_id, project_id),
    SHARD KEY (project_id)
);

-- -------------------------------------------------------------
-- 3. usuarios
-- Mapeo de usuarios del Active Directory y sus roles.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usuarios (
    username        VARCHAR(100)    NOT NULL,
    nombre_completo VARCHAR(255)    NOT NULL,
    email           VARCHAR(255)    NOT NULL,
    rol             VARCHAR(50)     NOT NULL,   -- Admin | Publicador
    activo          TINYINT(1)      NOT NULL DEFAULT 1,
    PRIMARY KEY (username)
);

-- -------------------------------------------------------------
-- 4. log_auditoria
-- Registro inmutable de cada intento de carga (éxito o fallo).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS log_auditoria (
    id                      BIGINT          NOT NULL AUTO_INCREMENT,
    timestamp_carga         DATETIME        NOT NULL DEFAULT NOW(),
    usuario_ad              VARCHAR(100)    NOT NULL,
    project_id              VARCHAR(100)    NOT NULL,
    id_catalogo             VARCHAR(100)    NOT NULL,
    nombre_archivo_original VARCHAR(500)    NOT NULL,
    filas_procesadas        INT             NOT NULL DEFAULT 0,
    estrategia_usada        VARCHAR(50)     NOT NULL,
    destino                 VARCHAR(50)     NOT NULL,
    estado_carga            VARCHAR(20)     NOT NULL,   -- Exito | Fallo
    errores_json            JSON,                       -- null si estado=Exito
    ruta_zip_auditoria      VARCHAR(1000),              -- null si no se guardó zip
    PRIMARY KEY (id)
);
