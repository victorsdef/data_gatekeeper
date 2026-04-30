USE gatekeeper_meta;

CREATE TABLE IF NOT EXISTS proyectos (
    project_id      VARCHAR(50)     NOT NULL,
    nombre          VARCHAR(200)    NOT NULL,
    descripcion     TEXT,
    activo          TINYINT(1)      NOT NULL DEFAULT 1,
    PRIMARY KEY (project_id)
);

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
    PRIMARY KEY (catalog_id)
);

CREATE TABLE IF NOT EXISTS usuarios (
    username      VARCHAR(100) NOT NULL,
    nombre        VARCHAR(200),
    email         VARCHAR(200),
    rol           VARCHAR(50)  NOT NULL DEFAULT 'Publicador',
    activo        TINYINT(1)   NOT NULL DEFAULT 1,
    ultimo_acceso DATETIME,
    PRIMARY KEY (username)
);

CREATE TABLE IF NOT EXISTS permisos_catalogo (
    catalog_id      VARCHAR(100)    NOT NULL,
    tipo            VARCHAR(20)     NOT NULL,   -- 'rol' | 'usuario'
    valor           VARCHAR(100)    NOT NULL,   -- 'Publicador', 'Admin', 'vcastro', etc.
    PRIMARY KEY (catalog_id, tipo, valor)
);

CREATE TABLE IF NOT EXISTS log_auditoria (
    id                      BIGINT          NOT NULL AUTO_INCREMENT,
    timestamp_carga         DATETIME        NOT NULL DEFAULT NOW(),
    usuario_ad              VARCHAR(100)    NOT NULL,
    project_id              VARCHAR(50)     NOT NULL,
    id_catalogo             VARCHAR(100)    NOT NULL,
    nombre_archivo_original VARCHAR(500)    NOT NULL,
    filas_procesadas        INT             NOT NULL DEFAULT 0,
    estrategia_usada        VARCHAR(50)     NOT NULL,
    destino                 VARCHAR(50)     NOT NULL,
    estado_carga            VARCHAR(20)     NOT NULL,
    errores_json            JSON,
    ruta_zip_auditoria      VARCHAR(1000),
    PRIMARY KEY (id)
);

-- Admin del sistema (siempre existe, independiente de LDAP)
INSERT IGNORE INTO usuarios (username, nombre, email, rol)
VALUES ('admin', 'Administrador', 'admin@baustro.fin.ec', 'Admin');
