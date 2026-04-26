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

## Estado de implementación

### Implementado y activo

| Componente | Archivo | Descripción |
|---|---|---|
| Entry point | `app.py` | Inicializa sesión y enruta a login, main o admin |
| Configuración | `config/settings.py` | Carga `DEMO_MODE`, `REAL_CATALOGS`, LDAP, SingleStore, auditoría |
| Login demo | `auth/ldap_auth.py` | Usuarios hardcodeados (`DEMO_MODE=true`) |
| Login LDAP | `auth/ldap_auth.py` | Autenticación real contra Active Directory via NTLM (`DEMO_MODE=false`) |
| Catálogos reales | `config/catalogs.py` | Lee proyectos y catálogos desde `catalogos_config` |
| Fallback mock | `config/mock_catalogs.py` | Retorna listas vacías cuando `REAL_CATALOGS=false` |
| Lectura archivos | `utils/file_handler.py` | CSV, TXT y Excel con auto-detección de separador y encoding |
| Motor de validación | `dg_validators/engine.py` | 8 tipos de reglas en memoria (ver sección aparte) |
| Reporte de errores | `utils/report_builder.py` | Excel descargable con 2 hojas y colores por tipo de error |
| UI Login | `views/login_view.py` | Login con branding Banco del Austro |
| UI Principal | `views/main_view.py` | Flujo de 4 pasos: carga → validación → resultado |
| UI Admin | `views/admin_catalogs_view.py` | Panel admin para registrar y desactivar catálogos |
| Descubrimiento BD | `utils/db_admin.py` | `SHOW DATABASES` / `SHOW TABLES` / `DESCRIBE` con caché 5 min |
| Escritura SingleStore | `utils/db_writer.py` | append / overwrite / reproceso con transacciones |
| Escritura Hive | `utils/db_writer.py` | append / overwrite / reproceso por partición |
| Cold storage | `utils/db_writer.py` | ZIP inmutable del archivo original |
| Log de auditoría | `utils/db_writer.py` | Registro en `log_auditoria` tras cada carga |
| DDL metadatos | `db/migrations/001_create_metadata_tables.sql` | Script DDL completo |
| Seed inicial | `db/migrations/002_seed_initial_data.sql` | Datos de referencia |
| Docker | `docker/docker-compose.yml` | SingleStore + Streamlit + volúmenes |

### Implementado pero desactivado

| Componente | Archivo | Qué necesita |
|---|---|---|
| Login con Active Directory | `auth/ldap_auth.py` | `DEMO_MODE=false` + variables LDAP en `.env` |
| Escritura en BD y auditoría | `utils/db_writer.py` | `DEMO_MODE=false` + variables SS en `.env` + tablas creadas |
| Escritura en Hive | `utils/db_writer.py` | Variables `HIVE_*` en `.env` + `pip install pyhive` |

---

## Flujo de usuario

### Rol Publicador

```
Login → Seleccionar Proyecto en sidebar
      → Seleccionar Catálogo destino (BD + tabla)
      → Subir archivo (CSV / TXT / Excel, hasta 50 MB, múltiples archivos)
      → Vista previa automática (20 primeras filas)
      → Seleccionar estrategia: append | overwrite | reproceso
      → [Ejecutar validación]
         ✗ Error → Consola de errores + Descargar reporte Excel
         ✓ OK   → [Confirmar carga]
                    → Escribe en BD con transacción
                    → Guarda ZIP en cold storage
                    → Registra en log_auditoria
                    → Muestra resultado y ruta de auditoría
```

### Rol Admin (acceso adicional)

```
Login → Botón "Administrar catálogos" en sidebar
      → Tab "Registrar catálogo":
           Seleccionar BD → Seleccionar tabla → Consultar esquema
           → Ajustar tipos y nullable por columna
           → Configurar: proyecto, ID, nombre, descripción, estrategia, destino
           → Guardar en catalogos_config (INSERT IGNORE)
      → Tab "Catálogos activos":
           Lista de catálogos con proyecto, BD, tabla, estrategia
           → Botón Desactivar con confirmación
```

---

## Módulos en detalle

### `utils/file_handler.py` — Lectura de archivos

| Formato | Detección automática | Selección manual |
|---|---|---|
| CSV / TXT | `csv.Sniffer` detecta `,` `;` `|` `\t` | Expander si columnas no se ven bien |
| Excel `.xlsx/.xls` | Lee hoja 1 automáticamente | Expander si tiene más de 1 hoja |
| Encoding | Intenta utf-8, latin-1, iso-8859-1, cp1252 | Selector manual disponible |

### `dg_validators/engine.py` — Motor de validación

Aplicadas en orden; cualquier falla aborta el proceso y no escribe nada en BD:

| Regla | Descripción |
|---|---|
| Estructural | Exactamente las columnas esperadas, ni más ni menos |
| Archivo vacío | Rechaza archivos sin filas de datos |
| Tipo de dato | `int` / `float` no pueden contener texto |
| Nulabilidad | Columnas `nullable: false` no aceptan celdas vacías |
| Dominio `isin` | El valor debe estar en el conjunto permitido |
| Rango `gte` / `lte` | Límites numéricos (mayor-igual / menor-igual) |
| Longitud mínima `min_length` | El texto debe tener al menos N caracteres |
| Longitud exacta `str_length` | El texto debe tener entre `min` y `max` caracteres |

### `utils/db_writer.py` — Estrategias de ingesta

| Estrategia | SingleStore | Hive |
|---|---|---|
| `append` | `INSERT INTO tabla (...)` | `INSERT INTO TABLE tabla PARTITION (...)` |
| `overwrite` | `TRUNCATE + INSERT` en transacción | `INSERT OVERWRITE TABLE tabla` |
| `reproceso` | `BEGIN; DELETE WHERE fecha=X; INSERT; COMMIT` | `INSERT OVERWRITE TABLE tabla PARTITION (fecha=X)` |

### `utils/db_admin.py` — Descubrimiento de esquemas

| Función | Descripción | Caché |
|---|---|---|
| `get_all_databases()` | `SHOW DATABASES` (excluye BDs de sistema) | 5 min |
| `get_tables_from_db(db)` | `SHOW TABLES FROM db` | 5 min |
| `describe_table(db, tbl)` | `DESCRIBE db.tabla` | Sin caché (datos dinámicos) |
| `build_schema_json(rows)` | Mapea tipos SS → str/int/float/bool | — |
| `get_mapped_tables()` | Par (bd, tabla) ya registrados | Sin caché |
| `save_catalog_config(...)` | `INSERT IGNORE` en catalogos_config | — |
| `deactivate_catalog(id)` | `UPDATE SET activo=0` | — |

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
base_datos     VARCHAR(100) NOT NULL   -- BD donde vive la tabla destino
tabla_destino  VARCHAR(200) NOT NULL   -- tabla final de negocio
destino        VARCHAR(20)  DEFAULT 'singlestore'  -- singlestore | hive
estrategia     VARCHAR(20)  DEFAULT 'overwrite'    -- overwrite | append | reproceso
schema_json    JSON         NOT NULL               -- columnas + reglas
activo         TINYINT(1)   DEFAULT 1
```

### `usuarios`
```sql
username       VARCHAR(100) PRIMARY KEY
rol            VARCHAR(50)  DEFAULT 'Publicador'   -- Admin | Publicador
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
estado_carga            VARCHAR(20)   NOT NULL   -- Exito | Fallo
errores_json            JSON
ruta_zip_auditoria      VARCHAR(1000)
```

### Estructura de `schema_json`

```json
{
  "columnas": [
    { "nombre": "codigo",      "tipo": "str",   "nullable": false },
    { "nombre": "descripcion", "tipo": "str",   "nullable": true  },
    { "nombre": "estado",      "tipo": "str",   "nullable": false,
      "reglas": [{ "tipo": "isin", "valor": ["A", "I"] }] },
    { "nombre": "monto",       "tipo": "float", "nullable": true,
      "reglas": [{ "tipo": "gte", "valor": 0 }] }
  ]
}
```

---

## Variables de entorno

`DEMO_MODE` y `REAL_CATALOGS` son independientes para transición gradual:

| Variable | Controla | Valores |
|---|---|---|
| `DEMO_MODE` | Login | `true` = demo / `false` = Active Directory |
| `REAL_CATALOGS` | Catálogos | `true` = SingleStore / `false` = listas vacías |

### Escenarios

**Desarrollo sin BD ni AD:**
```
DEMO_MODE=true
REAL_CATALOGS=false
```

**Desarrollo con BD real (estado actual):**
```
DEMO_MODE=true
REAL_CATALOGS=true
SS_HOST=127.0.0.1
SS_PORT=3306
SS_USER=root
SS_PASSWORD=gatekeeper123
SS_DATABASE=gatekeeper_meta
```

**Producción completa:**
```
DEMO_MODE=false
REAL_CATALOGS=true
SS_HOST=...   SS_PORT=3306   SS_USER=...   SS_PASSWORD=...   SS_DATABASE=gatekeeper_meta
LDAP_SERVER=ldap://ad.baustro.fin.ec
LDAP_PORT=389
LDAP_DOMAIN=BAUSTRO
LDAP_BASE_DN=DC=baustro,DC=fin,DC=ec
LDAP_USE_SSL=false
AUDIT_STORAGE_PATH=./audit_storage
MAX_FILE_SIZE_MB=50
MAX_ROWS_IN_MEMORY=500000
```

---

## Instalación y ejecución

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Configurar .env (copiar desde variables de entorno de arriba)

# 3. Inicializar BD (primera vez)
docker exec -i gatekeeper_singlestore singlestore -u root -pgatekeeper123 < docker/init_db/01_init.sql

# 4. Ejecutar app
streamlit run app.py

# Docker (reconstruir y levantar)
docker compose -f docker/docker-compose.yml up --build -d
```

**Usuarios demo:**
- `vcastro / demo123` → rol Publicador
- `admin / admin123` → rol Admin

---

## Roles

| Funcionalidad | Publicador | Admin |
|---|---|---|
| Subir archivos | ✅ | ✅ |
| Ver catálogos disponibles | ✅ | ✅ |
| Validar datos | ✅ | ✅ |
| Confirmar carga a BD | ✅ | ✅ |
| Descargar reporte de errores | ✅ | ✅ |
| Registrar nuevos catálogos | ❌ | ✅ |
| Desactivar catálogos | ❌ | ✅ |
| Explorar bases de datos / tablas | ❌ | ✅ |

---

## Pendiente antes de producción

| Tarea | Detalle |
|---|---|
| **Activar AD** | Configurar variables LDAP en `.env` y `DEMO_MODE=false` |
| **Ajustar grupos AD** | En `auth/ldap_auth.py` cambiar `GATEKEEPER_ADMIN` y `GATEKEEPER_PUBLICADOR` por los grupos reales del banco |
| **Configurar Nginx** | Proxy inverso puerto 80 → 8501 (Nginx ya instalado en el servidor) |
| **Hive (si aplica)** | `pip install pyhive thrift thrift-sasl` + variables `HIVE_*` en `.env` |
| **Usuarios iniciales** | Insertar registros en tabla `usuarios` con los roles correctos |

---

## Lo que falta del documento de arquitectura

Ver sección **Brechas respecto al documento original** más abajo.

---

## Brechas respecto al documento de arquitectura

El documento original (`document_pdf.pdf`) especificó los siguientes puntos que **aún no están completamente cubiertos**:

### 1. Nginx — no configurado en el repositorio
El PDF indica que Nginx ya está instalado en el servidor y actuará como proxy inverso. No existe un `nginx.conf` en el repositorio. Se necesita:
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
El PDF menciona pandera como motor de validación declarativa. Está en `requirements.txt` pero la validación real está en `dg_validators/engine.py` (motor custom). Opciones:
- Migrar a pandera para una definición más estándar y mantenible
- Quedarse con el engine custom (más control sobre el formato de errores)

### 3. Gestión de usuarios sin UI
La tabla `usuarios` existe pero no hay pantalla para crear, editar o desactivar usuarios sin acceso SQL directo. El flujo espera que los usuarios sean gestionados manualmente.

### 4. Kerberos / SSL en LDAP
El PDF menciona "Kerberos LDAP". La implementación actual usa NTLM (LDAP simple). Si el AD del banco requiere SASL/Kerberos, `auth/ldap_auth.py` necesita ajuste.

---

## Mejoras sugeridas (próximos pasos)

### Prioridad alta

| Mejora | Descripción |
|---|---|
| **nginx.conf en el repo** | Agregar archivo de configuración Nginx al repositorio para no depender de configuración manual en el servidor |
| **Panel de historial de cargas** | Vista donde el usuario vea sus cargas pasadas (fecha, archivo, filas, estado) consultando `log_auditoria` |
| **Gestión de usuarios en UI Admin** | Pantalla para que el Admin cree, active/desactive y cambie roles sin necesitar acceso SQL |

### Prioridad media

| Mejora | Descripción |
|---|---|
| **Editor de catálogo existente** | Admin puede editar el `schema_json` de un catálogo ya registrado (agregar/quitar reglas) |
| **Validación regex** | Agregar tipo de regla `regex` en `schema_json` para validar patrones (ej. código con formato `EC-[0-9]{4}`) |
| **Dashboard de métricas** | Vista de estadísticas: cargas por día, tasa de éxito por catálogo, usuarios más activos |
| **Preview de impacto antes de cargar** | Mostrar cuántas filas afectará la carga (en overwrite: cuántas se borrarán; en reproceso: el rango de fechas) |

### Prioridad baja

| Mejora | Descripción |
|---|---|
| **Notificaciones por correo** | Alerta automática si una carga falla (SMTP o webhook corporativo) |
| **Exportar/importar configuración de catálogos** | Admin puede exportar catálogos como JSON para backup o migración |
| **Regla de longitud mínima en UI** | El admin panel permite definir reglas `min_length` y `str_length` gráficamente, no solo en JSON |
| **Soporte multi-idioma** | Mensajes de error en inglés/español según preferencia del usuario |
| **Test suite** | Pruebas unitarias para `engine.py` y `file_handler.py` con archivos de muestra |
