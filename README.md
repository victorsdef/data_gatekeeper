# Data Gatekeeper

Portal interno de ingesta y validación de catálogos manuales — Banco del Austro.

---

## Objetivo del sistema

Aplicación web interna que descentraliza la carga de catálogos manuales (CSV, TXT, Excel), transfiriendo la responsabilidad de la calidad del dato al usuario final. El sistema valida estructura y reglas de negocio en memoria antes de impactar la base de datos, eliminando procesos batch intermedios y asegurando integridad transaccional.

---

## Estado de implementación

###  Implementado y activo

| Componente | Archivo | Descripción |
|---|---|---|
| Entry point | `app.py` | Inicializa sesión y enruta a login o app principal |
| Configuración | `config/settings.py` | Carga variables de entorno: DEMO_MODE, LDAP, SingleStore, auditoría |
| Login demo | `auth/ldap_auth.py` | Usuarios hardcodeados para desarrollo sin AD |
| Login LDAP | `auth/ldap_auth.py` | Autenticación real contra Active Directory via NTLM — activo con `DEMO_MODE=false` |
| Catálogos mock | `config/mock_catalogs.py` | 4 proyectos con esquemas definidos para desarrollo |
| Router catálogos | `config/catalogs.py` | Enruta a mock o SingleStore según `DEMO_MODE` |
| Lectura de archivos | `utils/file_handler.py` | Lee CSV, TXT y Excel a pandas DataFrame |
| Motor de validación | `dg_validators/engine.py` | Validación completa en memoria (ver detalle abajo) |
| Reporte de errores | `utils/report_builder.py` | Genera Excel descargable con 2 hojas, colores y metadatos |
| UI Login | `views/login_view.py` | Pantalla de login con manejo de sesión |
| UI Principal | `views/main_view.py` | Selector proyecto/catálogo, drag & drop, previsualización, consola de errores |
| Escritura en SingleStore | `utils/db_writer.py` | Estrategias `append`, `overwrite` y `reproceso` con transacciones SQL |
| Escritura en Hive | `utils/db_writer.py` | Estrategias `append`, `overwrite` y `reproceso` por partición de fecha |
| Cold storage | `utils/db_writer.py` | Guarda ZIP inmutable del archivo original en `AUDIT_STORAGE_PATH` |
| Log de auditoría | `utils/db_writer.py` | Inserta en `log_auditoria` tras cada carga (éxito o fallo) |
| DDL metadatos | `db/migrations/001_create_metadata_tables.sql` | Script para crear tablas en SingleStore |
| Seed inicial | `db/migrations/002_seed_initial_data.sql` | INSERTs con los catálogos actuales del mock |

###  Implementado pero desactivado (requiere `DEMO_MODE=false` + BD real)

| Componente | Archivo | Qué necesita |
|---|---|---|
| Login con Active Directory | `auth/ldap_auth.py` | Variables LDAP en `.env` |
| Lectura de catálogos desde BD | `config/catalogs.py` | Ejecutar los scripts SQL + variables SingleStore en `.env` |
| Escritura en BD y auditoría | `utils/db_writer.py` | Variables SingleStore en `.env` + tablas creadas |
| Escritura en Hive | `utils/db_writer.py` | Variables `HIVE_HOST`, `HIVE_PORT`, `HIVE_DATABASE` en `.env` + `pip install pyhive` |

###  Pendiente antes de producción

| Tarea | Descripción |
|---|---|
| **Ejecutar DDLs** | Correr `db/migrations/001_create_metadata_tables.sql` y `002_seed_initial_data.sql` en SingleStore |
| **Ajustar grupos del AD** | En `auth/ldap_auth.py` línea 92, reemplazar `GATEKEEPER_ADMIN` y `GATEKEEPER_PUBLICADOR` por los nombres reales de los grupos en el AD del banco |
| **Confirmar primera columna** | En `config/mock_catalogs.py` hay un `TODO`: la primera columna de `tbsc_catalogo_productos` está sin nombre — confirmar con el DBA |

---

## Reglas de validación implementadas

El motor `dg_validators/engine.py` aplica las siguientes validaciones en orden:

| Regla | Descripción |
|---|---|
| **Estructural** | El archivo debe tener exactamente las columnas esperadas, ni más ni menos |
| **Archivo vacío** | Se rechaza si el archivo no tiene filas de datos |
| **Tipo de dato** | Columnas `int` y `float` no pueden contener texto ni caracteres inválidos |
| **Nulabilidad** | Columnas marcadas `nullable: false` no pueden tener celdas vacías |
| **Dominio (`isin`)** | El valor debe pertenecer a un conjunto permitido |
| **Rango (`gte` / `lte`)** | El valor debe ser mayor/igual o menor/igual que un límite |
| **Longitud mínima (`min_length`)** | El texto debe tener al menos N caracteres |
| **Longitud exacta (`str_length`)** | El texto debe tener entre `min` y `max` caracteres |

Si cualquier validación falla, el proceso se aborta inmediatamente y no se escribe nada en la BD.

---

## Estrategias de ingesta (según arquitectura)

| Estrategia | SingleStore | Hive |
|---|---|---|
| `append` | `INSERT INTO tabla (...) VALUES (...)` | `INSERT INTO TABLE tabla PARTITION (...) SELECT ...` |
| `overwrite` | `TRUNCATE + INSERT` dentro de transacción | `INSERT OVERWRITE TABLE tabla SELECT ...` |
| `reproceso` | `BEGIN; DELETE WHERE fecha=X; INSERT; COMMIT` | `INSERT OVERWRITE TABLE tabla PARTITION (fecha=X)` |

---

## Estructura del proyecto

```
data_gatekeeper/
├── app.py                          # Entry point
├── requirements.txt
├── .env.example
├── .streamlit/
│   └── config.toml                 # Tema y configuración Streamlit
├── auth/
│   └── ldap_auth.py                # Autenticación LDAP / demo
├── config/
│   ├── settings.py                 # Variables de configuración
│   ├── catalogs.py                 # Router mock vs SingleStore
│   └── mock_catalogs.py            # Catálogos hardcodeados para DEMO_MODE
├── db/
│   └── migrations/
│       ├── 001_create_metadata_tables.sql   # DDL tablas de metadatos
│       └── 002_seed_initial_data.sql        # Datos iniciales
├── dg_validators/
│   └── engine.py                   # Motor de validación en memoria
├── utils/
│   ├── file_handler.py             # Lectura CSV / TXT / Excel
│   └── report_builder.py          # Generador reporte Excel de errores
└── views/
    ├── login_view.py               # Pantalla de login
    └── main_view.py                # App principal
```

---

## Modelo de metadatos en SingleStore

```sql
proyectos          -- Agrupador lógico de catálogos
catalogos_config   -- Definición de cada catálogo (tabla destino, estrategia, schema JSON)
usuarios           -- Mapeo de usuarios del AD y sus roles (Admin / Publicador)
log_auditoria      -- Registro inmutable de cada carga (timestamp, usuario, filas, estado)
```

---

## Instalación y ejecución

```bash
# 1. Clonar el repositorio
git clone https://github.com/victorsdef/data_gatekeeper.git
cd data_gatekeeper

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con los valores reales
```

### Modo desarrollo (sin BD ni AD)

```bash
streamlit run app.py
```

| Usuario | Contraseña | Rol |
|---|---|---|
| vcastro | demo123 | Publicador |
| admin | admin123 | Admin |

### Modo producción

```bash
# 1. Ejecutar scripts SQL en SingleStore
# db/migrations/001_create_metadata_tables.sql
# db/migrations/002_seed_initial_data.sql

# 2. Configurar .env
DEMO_MODE=false
SS_HOST=tu-servidor
SS_PORT=3306
SS_USER=tu-usuario
SS_PASSWORD=tu-password
SS_DATABASE=gatekeeper_meta
LDAP_SERVER=ldap://ad.baustro.fin.ec
LDAP_DOMAIN=BAUSTRO
LDAP_BASE_DN=DC=baustro,DC=fin,DC=ec

# 3. Levantar la app
streamlit run app.py --server.port 8501
```

Nginx actúa como proxy inverso hacia el puerto 8501.

---

## Pendiente antes de producción

1. **Ejecutar DDLs en SingleStore** — correr `db/migrations/001_create_metadata_tables.sql` y `002_seed_initial_data.sql` en la BD del banco
2. **Configurar `.env`** — copiar `.env.example` a `.env` y ajustar credenciales reales de SingleStore y LDAP
3. **Ajustar grupos del AD** — en `auth/ldap_auth.py` línea 92, reemplazar `GATEKEEPER_ADMIN` y `GATEKEEPER_PUBLICADOR` por los nombres reales de los grupos en el Active Directory del banco
4. **Confirmar primera columna** — en `config/mock_catalogs.py` hay un `TODO`: la primera columna de `tbsc_catalogo_productos` está sin nombre — confirmar con el DBA y reemplazar `""` por el nombre real
5. **Instalar pyhive** (solo si se usa Hive) — `pip install pyhive thrift thrift-sasl` y agregar variables `HIVE_HOST`, `HIVE_PORT`, `HIVE_DATABASE`, `HIVE_USER` al `.env`
