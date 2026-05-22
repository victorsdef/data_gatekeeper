# Data Gatekeeper

Portal interno de ingesta y validación de catálogos manuales para Banco del Austro.

---

## Objetivo

La aplicación permite que un publicador cargue archivos manuales (`CSV`, `TXT`, `Excel`), los valide en memoria y luego los escriba en `SingleStore` o `Hive` con auditoría y resguardo del archivo original en ZIP.

---

## Estado actual

Hoy el proyecto ya tiene:

- login con `LDAP` (`NTLM` para entorno real, `SIMPLE` para OpenLDAP de prueba) y admin local;
- portal de publicador con flujo de carga de 3 pasos: `subir -> validar -> resultado`;
- panel admin para registrar catálogos, editar configuración, gestionar permisos y usuarios;
- historial de cargas con filtros, métricas y exportación;
- auditoría en `log_auditoria` y cold storage en `audit_storage/`;
- backups lógicos de catálogos y usuarios en JSON;
- healthchecks para `app` y `nginx`;
- alertas por webhook configurables.

---

## Arquitectura

```text
Usuario AD / Local
        |
        v
     [Nginx]
        |
        v
   [Streamlit App]
        |
        +--> [ldap3 / LDAP]
        +--> [pandas en RAM]
        +--> [dg_validators/engine.py]
        +--> [SingleStore]
        +--> [Hive]
        +--> [audit_storage ZIP]
```

---

## Estructura principal

| Componente | Archivo / carpeta | Función |
|---|---|---|
| Entry point | `app.py` | Inicializa sesión y enruta vistas |
| Configuración | `config/settings.py` | Variables de entorno |
| Catálogos publicados | `config/catalogs.py` | Lee proyectos y catálogos para el publicador |
| Login | `auth/ldap_auth.py` | Autenticación LDAP y admin local |
| UI publicador | `views/main_view.py` | Flujo de carga y resultado |
| UI admin | `views/admin_catalogs_view.py` | Registro, edición, permisos, backups |
| UI historial | `views/history_view.py` | Auditoría y métricas |
| Validación | `dg_validators/engine.py` | Motor de reglas |
| Lectura de archivos | `utils/file_handler.py` | CSV / TXT / Excel |
| Escritura y auditoría | `utils/db_writer.py` | Carga a BD, ZIP, log |
| Administración metadata | `utils/db_admin.py` | Proyectos, catálogos, permisos |
| Usuarios | `utils/user_service.py` | Roles, activo/inactivo, backup |
| Docker local | `docker/docker-compose.prueba.yml` | Simulación completa |
| Docker banco | `docker/docker-compose.yml` | Solo `app` + `nginx` |

---

## Flujo funcional

### Publicador

1. Inicia sesión.
2. Selecciona proyecto y catálogo en el sidebar.
3. Sube uno o varios archivos.
4. Revisa la previsualización.
5. Ejecuta validación.
6. Si la validación pasa, confirma la carga.
7. La app escribe en BD, guarda ZIP auditado y registra `log_auditoria`.

### Admin

Desde `Administrar catálogos`:

- `Resumen`: métricas operativas rápidas;
- `Registrar catálogos`: wizard de 2 pasos;
- `Catálogos activos`: editar, permisos, desactivar, backup/restauración;
- `Usuarios`: roles, activo/inactivo, backup/restauración.

---

## Validación

El motor actual es `dg_validators/engine.py`.

Reglas soportadas:

| Regla | Descripción |
|---|---|
| Estructural | La columna debe existir exactamente como fue configurada |
| Archivo vacío | Rechaza archivos sin filas |
| Tipo | `str`, `int`, `float`, `bool` |
| Nulabilidad | Controla si la columna puede venir vacía |
| `isin` | Dominio de valores permitidos |
| `gte` | Valor mínimo |
| `lte` | Valor máximo |
| `min_length` | Longitud mínima |
| `str_length` | Longitud entre mínimo y máximo |
| `regex` | Patrón esperado |

### Reglas de calidad desde el panel Admin

En el wizard de registro:

- primero se define el esquema base: `tipo` y `nullable`;
- luego se agregan reglas por columna en el bloque **Reglas de calidad**.

Ejemplos:

- `codigo_segmento` con `isin`: `C, D, N`
- `monto` con `gte`: `0`
- `porcentaje` con `lte`: `100`
- `nombre_cliente` con `min_length`: `3`
- `codigo_oficina` con `str_length`: entre `3` y `5`
- `fecha_corte` con `regex`: `^\d{4}-\d{2}-\d{2}$`

Si una regla falla:

- no se escribe nada en BD;
- el publicador recibe el detalle del error;
- puede descargar reporte Excel de validación.

### Motivos comunes de falla al subir un archivo

Un archivo puede fallar en tres capas distintas:

#### 1. Fallas de lectura

Antes de validar datos, la app primero debe poder leer correctamente el archivo.

Casos típicos:

- separador incorrecto en `CSV` o `TXT`;
- codificación incorrecta (`utf-8`, `latin-1`, `cp1252`, etc.);
- hoja equivocada en un archivo Excel;
- archivo vacío;
- archivo que supera `MAX_FILE_SIZE_MB`;
- conjunto de archivos que supera `MAX_ROWS_IN_MEMORY`.

#### 2. Fallas de validación

Una vez leído el archivo, el contenido puede fallar por reglas de calidad o estructura.

Casos típicos:

- faltan columnas;
- sobran columnas;
- el nombre de una columna no coincide con el catálogo configurado;
- una columna tiene tipo incorrecto;
- una columna obligatoria viene vacía;
- un valor no pertenece al dominio permitido (`isin`);
- un número está por debajo del mínimo (`gte`);
- un número supera el máximo (`lte`);
- un texto no cumple la longitud mínima (`min_length`);
- un texto no cumple la longitud esperada (`str_length`);
- un valor no cumple el patrón configurado (`regex`).

#### 3. Fallas de carga a BD

Incluso si el archivo pasa la validación, la carga todavía puede fallar al escribir en el destino.

Casos típicos:

- error de conexión a `SingleStore` o `Hive`;
- permisos insuficientes sobre la tabla destino;
- error SQL en `overwrite`, `append` o `reproceso`;
- error operativo del servidor o de red.

Resumen:

- **lectura**: el archivo no se pudo interpretar correctamente;
- **validación**: el archivo se leyó, pero sus datos no cumplen reglas;
- **carga**: el archivo pasó validación, pero falló al persistirse en la BD.

---

## Estrategias de carga

| Estrategia | SingleStore | Hive |
|---|---|---|
| `append` | Inserta acumulando | Inserta acumulando |
| `overwrite` | `TRUNCATE + INSERT` con transacción | `INSERT OVERWRITE` |
| `reproceso` | Borra por fecha y vuelve a insertar | Sobrescribe particiones afectadas |

---

## Auditoría y ZIP

Cada carga genera:

- un `operation_id` único por operación;
- un registro en `log_auditoria`;
- un ZIP del archivo original en `AUDIT_STORAGE_PATH`.

Ruta del ZIP:

```text
AUDIT_STORAGE_PATH/<catalog_id>/<YYYYMMDD>/<timestamp>_<estado>_<usuario>_<operation_id>_<archivo>.zip
```

Ejemplo:

```text
/app/audit_storage/dg_au_agd_agencias__desembolsos_catalogo_agencias/20260522/20260522_052231_exito_vcastro_d0c8ac9dcb7f_db_catalogos_manuales.desembolsos_catalogo_agencias.csv.zip
```

---

## Modelo de metadata

Base: `gatekeeper_meta`

### `proyectos`

```sql
project_id   VARCHAR(50)  PRIMARY KEY
nombre       VARCHAR(200) NOT NULL
descripcion  TEXT
activo       TINYINT(1)   DEFAULT 1
```

### `catalogos_config`

```sql
catalog_id     VARCHAR(100) PRIMARY KEY
project_id     VARCHAR(50)  NOT NULL
nombre         VARCHAR(200) NOT NULL
descripcion    TEXT
base_datos     VARCHAR(100) NOT NULL
tabla_destino  VARCHAR(200) NOT NULL
destino        VARCHAR(20)  DEFAULT 'singlestore'
estrategia     VARCHAR(20)  DEFAULT 'overwrite'
schema_json    JSON         NOT NULL
activo         TINYINT(1)   DEFAULT 1
```

### `usuarios`

```sql
username       VARCHAR(100) PRIMARY KEY
nombre         VARCHAR(200)
email          VARCHAR(200)
rol            VARCHAR(50)  DEFAULT 'Publicador'
activo         TINYINT(1)   DEFAULT 1
ultimo_acceso  DATETIME
```

### `permisos_catalogo`

```sql
catalog_id   VARCHAR(100) NOT NULL
tipo         VARCHAR(20)  NOT NULL
valor        VARCHAR(100) NOT NULL
PRIMARY KEY (catalog_id, tipo, valor)
```

### `log_auditoria`

```sql
id                      BIGINT        AUTO_INCREMENT PRIMARY KEY
operation_id            VARCHAR(20)
timestamp_carga         DATETIME      DEFAULT NOW()
usuario_ad              VARCHAR(100)  NOT NULL
project_id              VARCHAR(50)   NOT NULL
id_catalogo             VARCHAR(100)  NOT NULL
nombre_archivo_original VARCHAR(500)  NOT NULL
filas_procesadas        INT           DEFAULT 0
estrategia_usada        VARCHAR(50)   NOT NULL
destino                 VARCHAR(50)   NOT NULL
estado_carga            VARCHAR(20)   NOT NULL
errores_json            JSON
ruta_zip_auditoria      VARCHAR(1000)
```

### Ejemplo de `schema_json`

```json
{
  "columnas": [
    { "nombre": "codigo", "tipo": "str", "nullable": false, "reglas": [] },
    { "nombre": "estado", "tipo": "str", "nullable": false,
      "reglas": [{ "tipo": "isin", "valor": ["A", "I"] }] },
    { "nombre": "monto", "tipo": "float", "nullable": true,
      "reglas": [{ "tipo": "gte", "valor": 0 }] }
  ]
}
```

---

## Backups lógicos

### Catálogos activos

Exporta un JSON con:

- proyectos;
- catálogos;
- permisos.

No respalda datos de negocio, solo metadata de configuración.

### Usuarios

Exporta un JSON con:

- `username`
- `nombre`
- `email`
- `rol`
- `activo`

No respalda LDAP real ni contraseñas.

---

## Variables importantes

| Variable | Uso |
|---|---|
| `LDAP_AUTH_METHOD` | `NTLM` para AD real, `SIMPLE` para OpenLDAP |
| `SS_HOST`, `SS_PORT`, `SS_USER`, `SS_PASSWORD` | SingleStore |
| `HIVE_HOST`, `HIVE_PORT`, `HIVE_USER`, `HIVE_PASSWORD` | Hive |
| `AUDIT_STORAGE_PATH` | Ruta donde se guardan los ZIP |
| `MAX_FILE_SIZE_MB` | Límite por archivo |
| `MAX_ROWS_IN_MEMORY` | Límite total en RAM |
| `ALERTS_ENABLED` | Activa alertas webhook |
| `ALERT_WEBHOOK_URL` | URL del webhook |
| `ALERT_ON_SUCCESS` | También alerta cargas exitosas |
| `TBL_PROYECTOS`, `TBL_CATALOGOS`, `TBL_USUARIOS`, `TBL_PERMISOS`, `TBL_LOG_AUDITORIA` | Nombres configurables de tablas metadata |

---

## Ejecución

### Local sin Docker

```bash
pip install -r requirements.txt
streamlit run app.py
```

### Prueba local con Docker

```bash
docker compose -f docker/docker-compose.prueba.yml up -d --build

docker exec -i gatekeeper_singlestore singlestore -uroot -pgatekeeper123 < docker/init_db/00_create_db.sql
docker exec -i gatekeeper_singlestore singlestore -uroot -pgatekeeper123 gatekeeper_meta < docker/init_db/01_init.sql

docker compose -f docker/docker-compose.prueba.yml logs -f app
docker compose -f docker/docker-compose.prueba.yml down
```

### Banco / servidor real

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

O, si primero quieres probar solo la app:

```bash
streamlit run app.py
```

---

## Docker

### `docker/docker-compose.prueba.yml`

Levanta la simulación completa:

- `SingleStore`
- `Hive`
- `LDAP`
- `phpLDAPadmin`
- `app`
- `nginx`

### `docker/docker-compose.yml`

Levanta solo:

- `app`
- `nginx`

Y consume `SingleStore`, `Hive` y `LDAP` reales desde tu `.env`.

---

## Healthchecks

| Componente | Check |
|---|---|
| App | `python scripts/healthcheck.py --mode liveness` |
| App readiness | `python scripts/healthcheck.py --mode readiness` |
| Nginx | `GET /healthz` |

---

## Roles

| Funcionalidad | Publicador | Admin |
|---|---|---|
| Subir y cargar archivos | ✅ | ✅ |
| Ver historial propio | ✅ | ✅ |
| Ver historial global | ❌ | ✅ |
| Descargar reporte de errores | ✅ | ✅ |
| Registrar catálogos | ❌ | ✅ |
| Editar / desactivar catálogos | ❌ | ✅ |
| Gestionar permisos | ❌ | ✅ |
| Gestionar usuarios | ❌ | ✅ |
| Backup / restauración metadata | ❌ | ✅ |

---

## Pendiente real

Lo que falta ya no es rehacer la app, sino validarla y conectarla en el entorno real:

- probar LDAP `NTLM` del banco;
- validar SingleStore y Hive reales;
- confirmar despliegue final con `nginx` y healthchecks;
- conectar logs y alertas a la plataforma operativa del banco;
- ejecutar pruebas end-to-end en servidor.
