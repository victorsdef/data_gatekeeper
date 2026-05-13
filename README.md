# Data Gatekeeper

Portal interno de ingesta y validación de catálogos manuales — Banco del Austro.

---

## Objetivo del sistema

Aplicación web interna que descentraliza la carga de catálogos manuales (CSV, TXT, Excel), transfiriendo la responsabilidad de la calidad del dato al usuario final. El sistema valida estructura y reglas de negocio en memoria antes de impactar la base de datos, eliminando procesos batch intermedios y asegurando integridad transaccional.

---

## Arquitectura general

```
Usuario AD
    │
    ▼
[Nginx Proxy] ──► [Streamlit :8501]
                        │
               ┌────────┴────────┐
               │                 │
         [pandas en RAM]    [ldap3 → AD]
               │
         [dg_validators/engine.py]
               │
      ┌────────┴────────┐
      ▼                 ▼
[SingleStore]        [Hive]
[log_auditoria]   [particiones]
      │
[ZIP cold storage]
```

---

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Frontend / Backend | Streamlit 1.28.2 |
| Proxy | Nginx (instalado en servidor, 80 → 8501) |
| Autenticación | ldap3 → Active Directory / NTLM o SIMPLE (OpenLDAP) |
| Procesamiento | pandas 2.0.3 (en memoria RAM) |
| Validación | Motor propio `dg_validators/engine.py` (8 reglas) |
| BD principal | SingleStore (metadatos + tablas de negocio) |
| BD secundaria | Hive (tablas de negocio, particiones dinámicas) |
| Cold storage | ZIP comprimido por catálogo/fecha/usuario en `audit_storage/` |
| Python | 3.9.13 |

---

## Estado de implementación

### ✅ Implementado y activo

| Componente | Archivo | Descripción |
|---|---|---|
| Entry point | `app.py` | Inicializa sesión y enruta a login, main, admin e historial |
| Configuración | `config/settings.py` | Carga `DEMO_MODE`, `REAL_CATALOGS`, LDAP, SingleStore, auditoría |
| Login demo | `auth/ldap_auth.py` | Usuarios hardcodeados (`DEMO_MODE=true`) |
| Login LDAP NTLM | `auth/ldap_auth.py` | Autenticación contra Active Directory via NTLM |
| Login LDAP SIMPLE | `auth/ldap_auth.py` | Autenticación contra OpenLDAP local (simulación) |
| Admin del sistema | `auth/ldap_auth.py` | Usuario `admin` siempre disponible, independiente de LDAP |
| Catálogos reales | `config/catalogs.py` | Lee proyectos y catálogos desde `catalogos_config` |
| Fallback mock | `config/mock_catalogs.py` | Retorna listas vacías cuando `REAL_CATALOGS=false` |
| Lectura archivos | `utils/file_handler.py` | CSV, TXT y Excel con auto-detección de separador y encoding |
| Motor de validación | `dg_validators/engine.py` | 8 tipos de reglas en memoria con evaluación lazy |
| Reporte de errores | `utils/report_builder.py` | Excel descargable con 2 hojas y colores por tipo de error |
| UI Login | `views/login_view.py` | Login con branding Banco del Austro |
| UI Principal | `views/main_view.py` | Flujo de 4 pasos: carga → validación → resultado |
| UI Admin | `views/admin_catalogs_view.py` | Panel admin: wizard 2 pasos, Master-Detail, paginación |
| UI Historial | `views/history_view.py` | Historial de cargas con filtros, métricas y exportación CSV |
| Descubrimiento BD | `utils/db_admin.py` | `SHOW DATABASES` / `SHOW TABLES` / `DESCRIBE` con caché 5 min |
| Escritura SingleStore | `utils/db_writer.py` | append / overwrite / reproceso con transacciones |
| Escritura Hive | `utils/db_writer.py` | append / overwrite / reproceso por partición |
| Consulta auditoría | `utils/db_writer.py` | `get_audit_log()` con filtros por usuario, fecha y estado |
| Cold storage | `utils/db_writer.py` | ZIP inmutable del archivo original |
| Log de auditoría | `utils/db_writer.py` | Registro en `log_auditoria` tras cada carga |
| Init BD | `docker/init_db/00_create_db.sql` | Crea la base de datos `gatekeeper_meta` |
| DDL metadatos | `docker/init_db/01_init.sql` | Crea las 5 tablas del esquema + usuario admin inicial |
| Docker dev | `docker/docker-compose.yml` | Stack completo local: SS + Hive + Spark + Zeppelin + LDAP + App |
| Docker prod/sim | `docker/docker-compose.prod.yml` | Simulación de producción: SS + Hive + LDAP + phpLDAPadmin + App |
| Script masivo | `scripts/populate_catalogs.py` | Auto-descubrimiento y registro masivo de tablas con `--dry-run` |

### ⚙️ Implementado pero requiere configuración

| Componente | Archivo | Qué necesita |
|---|---|---|
| Login con Active Directory | `auth/ldap_auth.py` | `DEMO_MODE=false` + `LDAP_AUTH_METHOD=NTLM` + vars LDAP en `.env` |
| Escritura en BD y auditoría | `utils/db_writer.py` | `DEMO_MODE=false` + variables SS en `.env` + tablas creadas |
| Escritura en Hive | `utils/db_writer.py` | Variables `HIVE_*` en `.env` + `pip install pyhive` |
| Historial en producción | `views/history_view.py` | `DEMO_MODE=false` (en demo muestra mensaje informativo) |

### ❌ Pendiente

| Componente | Detalle |
|---|---|
| **Nginx config** | No hay `nginx.conf` en el repo (ver sección Brechas) |
| **Gestión de usuarios en UI** | `user_service.py` tiene las funciones; falta la vista |
| **Editor de catálogos** | Solo se pueden crear y desactivar; no editar schema existente |
| **Validación regex** | El engine no soporta patrones regex todavía |
| **Dashboard de métricas** | Estadísticas globales de cargas por catálogo/usuario/día |

---

## Flujo de usuario

### Rol Publicador

```
Login → Seleccionar Proyecto en sidebar
      → Seleccionar Catálogo destino (BD + tabla)
      → Subir archivo (CSV / TXT / Excel, hasta 50 MB, múltiples archivos)
      → Vista previa automática (20 primeras filas)
      → [Ejecutar validación]
         ✗ Error → Consola de errores + Descargar reporte Excel
         ✓ OK   → [Confirmar carga]
                    → Escribe en BD con transacción
                    → Guarda ZIP en cold storage
                    → Registra en log_auditoria
                    → Muestra resultado y ruta de auditoría
      → [Historial de cargas] → ver cargas propias con filtros
```

### Rol Admin (acceso adicional)

```
Login → [Administrar catálogos]
      → Tab "Registrar catálogo" (wizard 2 pasos):
           PASO 1: Seleccionar BD → tablas (10/página, buscador, ya registradas en verde)
           PASO 2: Ajustar tipos, nullable, estrategia, destino → [Registrar]
      → Tab "Catálogos activos": listar, desactivar
      → Tab "Usuarios": ver roles, activar/desactivar
      → [Historial de cargas] → ver todas las cargas de todos los usuarios
```

---

## Módulos en detalle

### `views/history_view.py` — Historial de cargas

Vista de auditoría accesible desde el sidebar para todos los roles:

| Rol | Qué ve |
|---|---|
| Publicador | Solo sus propias cargas |
| Admin | Todas las cargas de todos los usuarios |

**Filtros disponibles**: rango de fechas, estado (Todos / Exito / Fallo), usuario (solo Admin).

**Métricas**: total de cargas, exitosas, fallidas, filas procesadas en el período.

**Exportación**: botón para descargar el historial filtrado como CSV.

En `DEMO_MODE=true` muestra un mensaje informativo (no hay BD real que consultar).

---

### `utils/file_handler.py` — Lectura de archivos

| Formato | Detección automática | Selección manual |
|---|---|---|
| CSV / TXT | `csv.Sniffer` detecta `,` `;` `\|` `\t` | Expander si columnas no se ven bien |
| Excel `.xlsx/.xls` | Lee hoja 1 automáticamente | Expander si tiene más de 1 hoja |
| Encoding | Intenta utf-8, latin-1, iso-8859-1, cp1252 | Selector manual disponible |

### `dg_validators/engine.py` — Motor de validación

| Regla | Descripción | Aborta |
|---|---|---|
| Estructural | Exactamente las columnas esperadas, ni más ni menos | Sí |
| Archivo vacío | Rechaza archivos sin filas de datos | Sí |
| Tipo de dato | `int` / `float` no pueden contener texto | No (lazy) |
| Nulabilidad | Columnas `nullable: false` no aceptan celdas vacías | No (lazy) |
| Dominio `isin` | El valor debe estar en el conjunto permitido | No (lazy) |
| Rango `gte` / `lte` | Límites numéricos (mayor-igual / menor-igual) | No (lazy) |
| Longitud mínima `min_length` | El texto debe tener al menos N caracteres | No (lazy) |
| Longitud exacta `str_length` | El texto debe tener entre `min` y `max` caracteres | No (lazy) |

Las reglas lazy capturan **todos** los errores en una pasada; el usuario recibe la lista completa para corregir el archivo de una vez.

### `utils/db_writer.py` — Estrategias de ingesta

| Estrategia | SingleStore | Hive |
|---|---|---|
| `append` | `INSERT INTO tabla (...)` en batches de 500 | `INSERT INTO TABLE tabla PARTITION (...)` |
| `overwrite` | `BEGIN; TRUNCATE; INSERT; COMMIT` | `INSERT OVERWRITE TABLE tabla` |
| `reproceso` | `BEGIN; DELETE WHERE fecha=X; INSERT; COMMIT` | `INSERT OVERWRITE PARTITION (fecha=X)` |

---

## Modelo de datos en SingleStore

**Base de datos**: `gatekeeper_meta`

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

### `log_auditoria`
```sql
id                      BIGINT        AUTO_INCREMENT PRIMARY KEY
timestamp_carga         DATETIME      DEFAULT NOW()
usuario_ad              VARCHAR(100)  NOT NULL
project_id              VARCHAR(100)  NOT NULL
id_catalogo             VARCHAR(100)  NOT NULL
nombre_archivo_original VARCHAR(500)  NOT NULL
filas_procesadas        INT           DEFAULT 0
estrategia_usada        VARCHAR(50)   NOT NULL
destino                 VARCHAR(50)   NOT NULL
estado_carga            VARCHAR(20)   NOT NULL
errores_json            JSON
ruta_zip_auditoria      VARCHAR(1000)
```

### Estructura de `schema_json`

```json
{
  "columnas": [
    { "nombre": "codigo",  "tipo": "str",   "nullable": false },
    { "nombre": "estado",  "tipo": "str",   "nullable": false,
      "reglas": [{ "tipo": "isin", "valor": ["A", "I"] }] },
    { "nombre": "monto",   "tipo": "float", "nullable": true,
      "reglas": [{ "tipo": "gte", "valor": 0 }] }
  ]
}
```

---

## Variables de entorno

| Variable | Controla | Valores |
|---|---|---|
| `DEMO_MODE` | Login + escritura en BD | `true` = demo / `false` = real |
| `REAL_CATALOGS` | Fuente de catálogos | `true` = SingleStore / `false` = vacío |
| `LDAP_AUTH_METHOD` | Protocolo LDAP | `NTLM` = AD real / `SIMPLE` = OpenLDAP |

### Escenarios de configuración

**Desarrollo sin BD ni AD:**
```env
DEMO_MODE=true
REAL_CATALOGS=false
```

**Simulación con Docker (docker-compose.prod.yml):**
```env
DEMO_MODE=false
REAL_CATALOGS=true
LDAP_AUTH_METHOD=SIMPLE        # OpenLDAP no soporta NTLM
SS_HOST=singlestore            # override en compose → contenedor
HIVE_HOST=hive                 # override en compose → contenedor
LDAP_SERVER=ldap://ldap        # override en compose → contenedor
```

**Producción real (servicios del banco):**
```env
DEMO_MODE=false
REAL_CATALOGS=true
LDAP_AUTH_METHOD=NTLM
SS_HOST=10.16.190.51
SS_PORT=3306
HIVE_HOST=10.16.190.21
HIVE_PORT=10000
LDAP_SERVER=ldap://austro.grpfin
LDAP_PORT=389
LDAP_DOMAIN=BAUSTRO
```

---

## Instalación y ejecución

### Desarrollo local (sin Docker)

```bash
pip install -r requirements.txt
# Crear .env con DEMO_MODE=true, REAL_CATALOGS=false
streamlit run app.py
```

### Docker — simulación de producción

Simula los servicios reales del banco (SingleStore + Hive + LDAP) en contenedores locales:

```bash
# Levantar el stack completo
docker compose -f docker/docker-compose.prod.yml up -d --build

# Primera vez — inicializar la BD
docker exec -i gatekeeper_singlestore singlestore -uroot -pgatekeeper123 \
  < docker/init_db/00_create_db.sql
docker exec -i gatekeeper_singlestore singlestore -uroot -pgatekeeper123 gatekeeper_meta \
  < docker/init_db/01_init.sql

# Ver logs
docker compose -f docker/docker-compose.prod.yml logs -f app

# Apagar
docker compose -f docker/docker-compose.prod.yml down
```

### Docker — desarrollo local (con Spark y Zeppelin)

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

---

## Puertos expuestos

### docker-compose.prod.yml (simulación)

| Servicio | Puerto host | URL |
|---|---|---|
| **Nginx (entrada principal)** | 80 | http://localhost |
| **SingleStore Studio** | 8080 | http://localhost:8080 |
| **SingleStore MySQL** | 3307 | `mysql -h localhost -P 3307 -uroot -pgatekeeper123` |
| **Hive Web UI** | 10002 | http://localhost:10002 |
| **phpLDAPadmin** | 8083 | http://localhost:8083 |
| **LDAP** | 389 | ldap://localhost:389 |

> La app ya no expone el puerto 8501 directamente; la entrada siempre es **http://localhost** (Nginx en puerto 80).
> Si Docker corre en un servidor remoto, reemplaza `localhost` por la IP del servidor.

### Login en phpLDAPadmin (http://localhost:8083)
- Login DN: `cn=admin,dc=baustro,dc=fin,dc=ec`
- Password: `admin123`

### Usuarios disponibles en la simulación

| Usuario | Contraseña | Rol |
|---|---|---|
| `admin` | `admin123` | Admin (siempre disponible, no depende de LDAP) |
| `vcastro` | `demo123` | Publicador (en LDAP contenedor) |

---

## Infraestructura Docker

### docker-compose.prod.yml — Simulación de producción

Simula los 3 servicios externos del banco:

```
Servicio real del banco         Simulado por contenedor
────────────────────────────────────────────────────────
SingleStore  10.16.190.51:3306  →  singlestore:3306
Hive         10.16.190.21:10000 →  hive:10000
LDAP/AD      austro.grpfin:389  →  ldap:389
```

El servicio `app` sobreescribe en `environment` los hosts del `.env` para apuntar a los contenedores:

```yaml
environment:
  SS_HOST: singlestore
  HIVE_HOST: hive
  LDAP_SERVER: ldap://ldap
  LDAP_AUTH_METHOD: SIMPLE    # OpenLDAP no soporta NTLM
```

### docker-compose.yml — Desarrollo

Stack completo con herramientas de análisis: SingleStore + Hive + Spark + Zeppelin + LDAP + App.

---

## Roles

| Funcionalidad | Publicador | Admin |
|---|---|---|
| Subir archivos y cargar | ✅ | ✅ |
| Ver historial de sus cargas | ✅ | ✅ |
| Ver historial de todos | ❌ | ✅ |
| Descargar reporte de errores | ✅ | ✅ |
| Registrar nuevos catálogos | ❌ | ✅ |
| Desactivar catálogos | ❌ | ✅ |
| Gestionar usuarios | ❌ | ✅ |

---

## Brechas respecto al documento de arquitectura

### 1. Nginx — no configurado en el repositorio

No existe un `nginx.conf` en el repo. Configuración recomendada:

```nginx
server {
    listen 80;
    location / {
        proxy_pass http://localhost:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

### 2. pandera — no utilizado

Está en `requirements.txt` pero la validación usa `dg_validators/engine.py` (motor custom con mejor control del formato de errores).

### 3. Gestión de usuarios sin UI

La tabla `usuarios` existe y `user_service.py` tiene `get_all_usuarios()`, `update_user_rol()`, `toggle_user_activo()`. Falta la vista en el panel admin.

### 4. NTLM vs SIMPLE

El AD real del banco usa NTLM. El contenedor OpenLDAP de simulación usa SIMPLE. El código maneja ambos según `LDAP_AUTH_METHOD` en el `.env`.

---

## Próximos pasos

### Prioridad alta

| Tarea | Detalle |
|---|---|
| **Activar AD real** | `DEMO_MODE=false` + `LDAP_AUTH_METHOD=NTLM` + ajustar grupos en `auth/ldap_auth.py` |
| **Nginx en el servidor** | Crear `nginx.conf` y apuntar proxy 80 → 8501 |
| **Gestión de usuarios en UI** | Vista en panel admin para cambiar roles y activar/desactivar |

### Prioridad media

| Tarea | Detalle |
|---|---|
| **Editor de catálogos** | Editar `schema_json` de catálogos ya registrados |
| **Validación regex** | Agregar tipo de regla `regex` en el engine |
| **Dashboard de métricas** | Estadísticas globales de cargas por catálogo/usuario/día |
| **Preview de impacto** | Mostrar cuántas filas afectará la carga antes de confirmar |

### Prioridad baja

| Tarea | Detalle |
|---|---|
| **Notificaciones** | Alerta por correo o webhook si una carga falla |
| **Export/import de configuración** | Backup de catálogos como JSON |
| **Test suite** | Pruebas unitarias para `engine.py` y `file_handler.py` |
