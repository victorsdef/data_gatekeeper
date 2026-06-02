# Data Gatekeeper - Pase a produccion

Este documento sirve como guia de ejecucion para desplegar Data Gatekeeper en
un servidor con Docker.

El despliegue usa:

- imagen Docker de la aplicacion;
- archivo `docker/.env` con variables de produccion;
- volumen `/data` para auditoria;
- SingleStore por balanceador;
- autenticacion LDAP/AD;
- metadata en la base `gatekeeper_meta`.

> Importante:   El servidor productivo no requiere internet. La imagen se debe construir
> previamente en una maquina con acceso a internet y luego pasarla como archivo
> `.tar`.

## 1. Prerrequisitos del servidor

El servidor destino debe tener:

- Docker Engine o Docker Desktop instalado;
- imagen `docker-app:latest` cargada con `docker load`;
- acceso de red a `singlestore.austro.grpfin:3306`;
- acceso de red a `austro.grpfin:389`;
- puerto `8501` disponible, o el puerto que se publique para la aplicacion;
- ruta `/data` creada para almacenar auditoria;
- permisos de escritura sobre `/data` para el contenedor.

Validaciones recomendadas desde el servidor:

```bash
nslookup singlestore.austro.grpfin
nc -vz singlestore.austro.grpfin 3306
nc -vz austro.grpfin 389
```

Si `nc` no esta disponible, usar una herramienta equivalente de conectividad.

## 2. Dependencias incluidas en la imagen

La imagen se construye con `docker/Dockerfile`.

Base:

```text
python:3.9.16-slim
```

Paquetes de sistema:

```text
build-essential
libldap2-dev
libsasl2-dev
ldap-utils
curl
```

Dependencias Python principales:

```text
streamlit==1.28.2
ldap3==2.9.1
pandas==2.0.3
openpyxl==3.1.2
xlrd==2.0.1
python-dotenv==1.0.0
singlestoredb==1.3.0
pyhive[hive]==0.7.0
thrift==0.16.0
thrift-sasl==0.4.3
pure-sasl>=0.6.2
pytest==8.3.5
```

## 3. Variables de entorno

El archivo usado por Docker es:

```text
docker/.env
```

Estructura esperada:

```env
# APP
APP_ENV=production
APP_PORT=8501
LOG_LEVEL=INFO

# ADMIN LOCAL
SYSTEM_ADMIN_USERNAME=
SYSTEM_ADMIN_PASSWORD=

# SINGLESTORE
SS_USE_BALANCER=true
SS_DIRECT_HOST=
SS_BALANCER_HOST=singlestore.austro.grpfin
SS_HOST=singlestore.austro.grpfin
SS_PORT=3306
SS_DATABASE=gatekeeper_meta
SS_USER=<usuario_singlestore>
SS_PASSWORD=<password_singlestore>

# FILTRO DE DB
DB_NAME_FILTERS=catalogo

# HIVE
HIVE_ENABLED=false
HIVE_HOST=10.16.190.21
HIVE_PORT=10000
HIVE_DATABASE=default
HIVE_USER=
HIVE_PASSWORD=

# LDAP
LDAP_AUTH_METHOD=LDAP3_SIMPLE
LDAP_SERVER=ldap://austro.grpfin:389
LDAP_PORT=389
LDAP_DOMAIN=austro.grpfin
LDAP_USE_SSL=false
LDAP_BASE_DN=DC=austro,DC=grpfin
LDAP_SEARCH_ATTRIBUTE=sAMAccountName
LDAP_REQUIRED_GROUP=Usuarios_Hadoop

# Solo para LDAPSEARCH
# LDAPSEARCH_BIN=ldapsearch
# LDAP_BIND_TEMPLATE={username}@{domain}
# LDAP_SEARCH_BIND_DN=
# LDAP_SEARCH_BIND_PASSWORD=

# Solo para LDAP3_SIMPLE
LDAP_BIND_USER=
LDAP_BIND_PASSWORD=
LDAP_USERS_OU=
LDAP_GROUPS_OU=
LDAP_ADMIN_DN=
LDAP_ADMIN_PASSWORD=

# METADATA
TBL_PROYECTOS=proyectos
TBL_CATALOGOS=catalogos_config
TBL_USUARIOS=usuarios
TBL_PERMISOS=permisos_catalogo
TBL_LOG_AUDITORIA=log_auditoria

# AUDITORIA
AUDIT_STORAGE_PATH=/data
AUDIT_STORAGE_READONLY_AFTER_WRITE=true
AUDIT_STORAGE_DIR_MODE=750
AUDIT_STORAGE_FILE_MODE=440

# LIMITES
MAX_UPLOAD_SIZE_MB=100
MAX_FILE_SIZE_MB=100
MAX_ROWS_IN_MEMORY=500000

# ALERTAS
ALERTS_ENABLED=false
ALERT_WEBHOOK_URL=
ALERT_ON_SUCCESS=false
```

## 4. DDL de metadata

Ejecutar en SingleStore con un usuario que tenga permisos para crear base y
tablas.

```sql
CREATE DATABASE IF NOT EXISTS gatekeeper_meta;

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
    tipo            VARCHAR(20)     NOT NULL,
    valor           VARCHAR(100)    NOT NULL,
    PRIMARY KEY (catalog_id, tipo, valor)
);

CREATE TABLE IF NOT EXISTS log_auditoria (
    id                      BIGINT          NOT NULL AUTO_INCREMENT,
    operation_id            VARCHAR(20),
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

```

## 5. Preparar ruta de auditoria

En el servidor:

```bash
sudo mkdir -p /data
sudo chmod 750 /data
```

Si Docker corre con un usuario/grupo especifico, ajustar propietario segun la
politica del servidor.

La aplicacion guarda evidencia en:

```text
/data
```

El compose monta:

```yaml
volumes:
  - /data:/data
```

## 6. Preparar imagen en maquina con internet

Este paso se ejecuta fuera del servidor productivo, en una maquina que si tenga
acceso a internet para descargar la imagen base y dependencias.

Desde la raiz del proyecto:

```bash
docker compose -f docker/docker-compose.yml build
```

La imagen generada por Compose puede quedar como:

```text
docker-app:latest
```

Verificar:

```bash
docker images
```

No ejecutar este paso en produccion si el servidor no tiene internet.

## 7. Exportar imagen para enviar a produccion

```bash
docker save docker-app:latest -o gatekeeper_app.tar
```

Opcional para comprimir:

```powershell
Compress-Archive -Path gatekeeper_app.tar -DestinationPath gatekeeper_app.zip
```

Enviar junto con:

- `gatekeeper_app.tar` o `gatekeeper_app.zip`;
- `docker/docker-compose.yml`;
- `docker/.env` sin credenciales reales, o con credenciales por canal seguro;
- este documento.

Motivo de enviar `docker/docker-compose.yml`: la imagen ya contiene la
aplicacion, pero el compose define como ejecutarla en el servidor: archivo
`.env`, puerto, volumen `/data`, healthcheck, nombre del contenedor y politica
de reinicio.

## 8. Cargar imagen en servidor sin internet

Este es el primer paso que se ejecuta en el servidor productivo.

Si se recibe `.tar`:

```bash
docker load -i gatekeeper_app.tar
```

Si se recibe `.zip` en Windows:

```powershell
Expand-Archive gatekeeper_app.zip
docker load -i .\gatekeeper_app\gatekeeper_app.tar
```

Verificar:

```bash
docker images
```

Debe aparecer:

```text
docker-app   latest
```

## 9. Levantar con Docker Compose sin reconstruir

Esta es la forma oficial recomendada para produccion.

Desde la carpeta del proyecto o desde una carpeta que tenga `docker/`:

```bash
docker compose -f docker/docker-compose.yml up -d
```

No usar `--build` en produccion sin internet. Si se usa `--build`, Docker va a
intentar reconstruir la imagen y puede fallar al no poder descargar dependencias
o imagenes base.

Compose mantiene el despliegue repetible y evita olvidar variables, volumenes,
healthcheck o politica de reinicio.

Ver logs:

```bash
docker compose -f docker/docker-compose.yml logs -f app
```

Estado:

```bash
docker ps
docker inspect gatekeeper_app --format "{{json .State.Health}}"
```

## 10. Validacion posterior al despliegue

Abrir:

```text
http://<servidor>:8501
```

Validar:

1. La pantalla de login carga.
2. Un usuario del grupo `Usuarios_Hadoop` puede autenticarse.
3. Un usuario fuera del grupo no ingresa.
4. El panel lista proyectos/catalogos permitidos.
5. Se puede subir un archivo de prueba.
6. La previsualizacion se muestra.
7. La validacion exitosa habilita confirmar carga.
8. Una validacion con errores permite descargar reporte.
9. Una carga exitosa registra auditoria.
10. Se crea evidencia en `/data`.
11. El historial muestra la carga.

## 11. Comandos utiles de soporte

Ver variables LDAP dentro del contenedor:

```bash
docker exec -it gatekeeper_app sh -c "env | grep LDAP"
```

Ver variables SingleStore:

```bash
docker exec -it gatekeeper_app sh -c "env | grep SS_"
```

Probar resolucion DNS:

```bash
docker exec -it gatekeeper_app sh -c "getent hosts singlestore.austro.grpfin"
docker exec -it gatekeeper_app sh -c "getent hosts austro.grpfin"
```

Probar puerto LDAP:

```bash
docker exec -it gatekeeper_app sh -c "nc -zv austro.grpfin 389 || true"
```

Ver logs:

```bash
docker logs -f gatekeeper_app
```

Bajar contenedor:

```bash
docker compose -f docker/docker-compose.yml down
```

Eliminar contenedor manual:

```bash
docker rm -f gatekeeper_app
```

## 12. Actualizacion del sistema

Para actualizar Data Gatekeeper en un servidor sin internet, no se construye la
imagen en produccion. Se prepara una nueva imagen en una maquina con internet,
se exporta y se carga en el servidor.

### 12.1 Preparar nueva imagen

En la maquina con internet, desde la raiz del proyecto actualizado:

```bash
docker compose -f docker/docker-compose.yml build
docker save docker-app:latest -o gatekeeper_app_nueva.tar
```

Opcionalmente comprimir:

```powershell
Compress-Archive -Path gatekeeper_app_nueva.tar -DestinationPath gatekeeper_app_nueva.zip
```

Enviar al servidor:

- `gatekeeper_app_nueva.tar` o `gatekeeper_app_nueva.zip`;
- cambios de `docker/docker-compose.yml` si existieran;
- cambios de `docker/.env` si existieran;
- scripts SQL de migracion si la version cambia metadata.

### 12.2 Respaldar antes de actualizar

En el servidor, antes de cargar la nueva imagen:

```bash
docker images
docker compose -f docker/docker-compose.yml logs --tail=200 app > gatekeeper_logs_antes_update.txt
```

Si se quiere conservar la imagen actual como archivo de rollback:

```bash
docker save docker-app:latest -o gatekeeper_app_version_anterior.tar
```

La carpeta `/data` no se debe borrar.

### 12.3 Cargar nueva imagen

Si se recibe `.tar`:

```bash
docker load -i gatekeeper_app_nueva.tar
```

Si se recibe `.zip`, descomprimir primero y luego ejecutar `docker load`.

Verificar que la imagen exista:

```bash
docker images
```

### 12.4 Recrear contenedor con Compose

Usar Compose sin `--build`:

```bash
docker compose -f docker/docker-compose.yml up -d
```

Si el contenedor ya existia, Compose lo recrea usando la imagen cargada y la
configuracion del compose.

Ver logs:

```bash
docker compose -f docker/docker-compose.yml logs -f app
```

### 12.5 Validar actualizacion

Validar:

1. El contenedor queda `healthy`.
2. La pantalla de login abre.
3. Un usuario LDAP puede iniciar sesion.
4. Los catalogos se listan.
5. Se puede previsualizar un archivo.
6. La validacion funciona.
7. La carga registra auditoria.
8. La evidencia sigue guardandose en `/data`.

Comando de estado:

```bash
docker inspect gatekeeper_app --format "{{json .State.Health}}"
```

### 12.6 Consideraciones si hay cambios de base

Si la actualizacion incluye cambios de metadata:

1. respaldar la base `gatekeeper_meta`;
2. ejecutar scripts SQL de migracion;
3. cargar nueva imagen;
4. levantar con Compose;
5. validar login, catalogos, carga e historial.

No modificar manualmente tablas de metadata sin script o evidencia del cambio.

## 13. Rollback

Si la nueva version falla:

1. Detener contenedor actual:

```bash
docker rm -f gatekeeper_app
```

2. Cargar imagen anterior:

```bash
docker load -i gatekeeper_app_version_anterior.tar
```

3. Levantar nuevamente:

```bash
docker compose -f docker/docker-compose.yml up -d
```

La carpeta `/data` no se elimina al hacer rollback.

## 14. Pendientes antes de ejecutar

Completar y confirmar:

- usuario SingleStore;
- password SingleStore;
- si se mantiene `SYSTEM_ADMIN_USERNAME` vacio o se define admin local;
- si LDAP requiere usuario tecnico de busqueda;
- permisos de escritura sobre `/data`;
- que el usuario LDAP pertenece a `Usuarios_Hadoop`;
- que el balanceador `singlestore.austro.grpfin` resuelve desde el servidor.

## 15. Resumen rapido

En maquina con internet:

```bash
docker compose -f docker/docker-compose.yml build
docker save docker-app:latest -o gatekeeper_app.tar
```

En servidor productivo sin internet:

```bash
docker load -i gatekeeper_app.tar
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f app
```
