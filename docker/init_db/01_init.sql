-- =============================================================
-- Data Gatekeeper — Inicialización de base de datos
-- Se ejecuta automáticamente al levantar el contenedor
-- =============================================================

CREATE DATABASE IF NOT EXISTS gatekeeper_meta;
USE gatekeeper_meta;

-- -------------------------------------------------------------
-- 1. proyectos
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proyectos (
    project_id      VARCHAR(50)     NOT NULL,
    nombre          VARCHAR(200)    NOT NULL,
    descripcion     TEXT,
    activo          TINYINT(1)      NOT NULL DEFAULT 1,
    PRIMARY KEY (project_id)
);

-- -------------------------------------------------------------
-- 2. catalogos_config
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalogos_config (
    catalog_id      VARCHAR(100)    NOT NULL,
    project_id      VARCHAR(50)     NOT NULL,
    nombre          VARCHAR(200)    NOT NULL,
    descripcion     TEXT,
    base_datos      VARCHAR(100)    NOT NULL,
    tabla_destino   VARCHAR(200)    NOT NULL,
    destino         VARCHAR(20)     NOT NULL DEFAULT 'singlestore',
    estrategia      VARCHAR(20)     NOT NULL DEFAULT 'overwrite',
    schema_json     JSON            NOT NULL,
    activo          TINYINT(1)      NOT NULL DEFAULT 1,
    PRIMARY KEY (catalog_id),
    CONSTRAINT fk_catalogo_proyecto
        FOREIGN KEY (project_id) REFERENCES proyectos(project_id)
);

-- -------------------------------------------------------------
-- 3. usuarios
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usuarios (
    username        VARCHAR(100)    NOT NULL,
    rol             VARCHAR(50)     NOT NULL DEFAULT 'Publicador',
    activo          TINYINT(1)      NOT NULL DEFAULT 1,
    ultimo_acceso   DATETIME,
    PRIMARY KEY (username)
);

-- -------------------------------------------------------------
-- 4. log_auditoria
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
    estado_carga            VARCHAR(20)     NOT NULL,
    errores_json            JSON,
    ruta_zip_auditoria      VARCHAR(1000),
    PRIMARY KEY (id),
    CONSTRAINT fk_log_usuario
        FOREIGN KEY (usuario_ad)  REFERENCES usuarios(username),
    CONSTRAINT fk_log_catalogo
        FOREIGN KEY (id_catalogo) REFERENCES catalogos_config(catalog_id)
);

-- -------------------------------------------------------------
-- Datos de prueba
-- -------------------------------------------------------------
INSERT IGNORE INTO proyectos VALUES
('CATALOGOS_MANUALES', 'Catálogos Manuales', 'Tablas de catálogos generales', 1),
('BSC_BANCA_PERSONAS', 'BSC Banca Personas', 'Cuadro de mando banca personas', 1),
('CUMPLIMIENTO',       'Cumplimiento',        'Listas y control de riesgo',    1);

INSERT IGNORE INTO usuarios VALUES
('vcastro',   'Publicador', 1, NULL),
('admin',     'Admin',      1, NULL),
('pgdelgado', 'Publicador', 1, NULL);
