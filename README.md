# Data Gatekeeper

Data Gatekeeper es un portal interno para ordenar, controlar y auditar la carga
de catálogos manuales hacia las bases de datos corporativas.

El sistema busca resolver un problema común en procesos de datos: los archivos
manuales suelen llegar por canales informales, con estructuras distintas,
errores de formato, valores inválidos o cambios no controlados. Cuando esos
archivos se cargan directamente o mediante procesos batch poco visibles, el
riesgo se traslada a las tablas finales y los errores aparecen tarde, cuando ya
afectaron reportes, procesos operativos o análisis de negocio.

Data Gatekeeper propone un punto único de ingreso. El usuario publicador carga
su archivo, selecciona el proyecto y catálogo correspondiente, revisa una
previsualización y ejecuta validaciones antes de que los datos lleguen a
SingleStore o Hive. Si el archivo no cumple la estructura o las reglas definidas
para el catálogo, la carga se rechaza y el usuario recibe el detalle de los
errores para corregirlos en origen.

Con esto, la responsabilidad de la calidad del dato se acerca al usuario que
conoce el archivo, mientras que el sistema protege las tablas destino con
validaciones previas, trazabilidad, control de permisos y registro de auditoría.

En términos prácticos, el sistema ayuda a:

- descentralizar la carga de catálogos manuales sin perder control;
- validar estructura, tipos de datos, nulabilidad y reglas de negocio antes de
  escribir en base de datos;
- evitar que archivos incorrectos impacten tablas finales;
- registrar quién cargó qué archivo, cuándo, hacia qué catálogo y con qué
  resultado;
- conservar evidencia auditada del archivo original cargado;
- administrar catálogos, permisos y usuarios desde un panel interno.

El objetivo final es que la carga manual de información deje de depender de
intervenciones técnicas repetitivas y pase a un flujo controlado, validado y
auditable.

## Modelo de configuración y auditoría

Para que el portal no dependa de configuraciones quemadas en el código, se
propone mantener un esquema administrativo llamado `gatekeeper_meta`. Este
esquema guarda la metadata necesaria para saber qué catálogos existen, quién
puede cargarlos, qué reglas deben cumplir los archivos y qué ocurrió en cada
intento de carga.

La idea es separar claramente dos mundos:

- las **tablas de negocio**, donde finalmente aterrizan los catálogos;
- las **tablas de control**, donde Data Gatekeeper guarda configuración,
  permisos y auditoría.

### `proyectos`

Agrupa catálogos bajo una unidad lógica de negocio o dominio de datos.

Campos principales:

- `project_id`: identificador único del proyecto.
- `nombre`: nombre legible del proyecto.
- `descripcion`: detalle opcional.
- `activo`: permite ocultar o deshabilitar proyectos sin borrarlos.

### `catalogos_config`

Es la tabla central de configuración. Define qué catálogos puede cargar el
portal, hacia dónde deben escribirse y qué validaciones deben aplicarse antes de
la carga.

Campos principales:

- `catalog_id`: identificador único del catálogo.
- `project_id`: proyecto al que pertenece.
- `nombre`: nombre visible para el usuario.
- `base_datos`: base donde está la tabla destino.
- `tabla_destino`: tabla final donde se cargarán los datos.
- `destino`: motor de persistencia, por ejemplo `singlestore` o `hive`.
- `estrategia`: modo de carga, por ejemplo `append`, `overwrite` o `reproceso`.
- `schema_json`: definición de columnas, tipos, nulabilidad y reglas de calidad.
- `activo`: permite deshabilitar el catálogo sin perder su configuración.

Esta tabla permite que un administrador registre o ajuste catálogos desde el
portal, sin modificar el código de la aplicación.

### `usuarios`

Guarda los usuarios que han ingresado o que pueden ser administrados desde el
portal. No reemplaza al Active Directory; funciona como una capa interna para
roles y estado dentro de Data Gatekeeper.

Campos principales:

- `username`: usuario corporativo.
- `nombre`: nombre completo.
- `email`: correo.
- `rol`: perfil dentro del portal, por ejemplo `Admin` o `Publicador`.
- `activo`: permite bloquear el acceso al portal sin tocar Active Directory.
- `ultimo_acceso`: última fecha de ingreso.

### `permisos_catalogo`

Controla qué usuarios o roles pueden cargar cada catálogo.

Campos principales:

- `catalog_id`: catálogo al que aplica el permiso.
- `tipo`: tipo de permiso, por ejemplo `rol` o `usuario`.
- `valor`: valor asociado al permiso, por ejemplo `Publicador` o un username.

Con esta tabla se puede permitir que algunos catálogos sean públicos para todos
los publicadores y otros queden restringidos a usuarios específicos.

### `log_auditoria`

Registra cada intento de carga, exitoso o fallido. Esta tabla es clave para la
trazabilidad operativa y para responder preguntas como quién cargó un archivo,
cuándo, hacia qué catálogo y con qué resultado.

Campos principales:

- `operation_id`: identificador único de la operación.
- `timestamp_carga`: fecha y hora de la carga.
- `usuario_ad`: usuario que ejecutó la operación.
- `project_id`: proyecto relacionado.
- `id_catalogo`: catálogo cargado.
- `nombre_archivo_original`: nombre del archivo recibido.
- `filas_procesadas`: cantidad de filas procesadas.
- `estrategia_usada`: estrategia aplicada en la carga.
- `destino`: motor destino usado.
- `estado_carga`: resultado, por ejemplo `Exito` o `Fallo`.
- `errores_json`: detalle técnico o funcional del error, si aplica.
- `ruta_zip_auditoria`: ubicación del ZIP auditado del archivo original.

Con este modelo, Data Gatekeeper puede administrar su propia configuración y
mantener una bitácora confiable de las cargas sin mezclarse con las tablas de
negocio.

## Flujo del administrador

El administrador es responsable de preparar el entorno para que los publicadores
puedan cargar archivos sin intervención técnica en cada operación.

Desde su panel, el administrador puede revisar el estado general del portal,
registrar catálogos, ajustar reglas de validación, administrar permisos y
gestionar usuarios.

El flujo principal del administrador es:

1. Ingresa al portal con perfil `Admin`.
2. Accede al panel de administración.
3. Revisa un resumen operativo con catálogos activos, usuarios, cargas recientes
   y fallos.
4. Explora las bases y tablas disponibles en SingleStore o Hive.
5. Selecciona una o varias tablas que desea habilitar como catálogos cargables.
6. Define o confirma el proyecto al que pertenece cada catálogo.
7. Configura el catálogo:
   - nombre visible;
   - tabla destino;
   - motor destino;
   - estrategia de carga;
   - estructura esperada del archivo;
   - tipos de datos;
   - campos obligatorios o permitidos como nulos;
   - reglas de calidad.
8. Guarda la configuración en `catalogos_config`.
9. Define permisos por rol o por usuario en `permisos_catalogo`.
10. Revisa los catálogos activos y, si es necesario, los edita, desactiva o
    respalda.
11. Administra usuarios del portal, sus roles y su estado activo/inactivo.
12. Consulta el historial global de cargas para seguimiento y control.

El administrador no carga necesariamente todos los archivos. Su función principal
es dejar preparado el catálogo para que el publicador pueda operar de forma
controlada.

## Flujo del publicador

El publicador es el usuario de negocio responsable de cargar el archivo manual.
El sistema lo guía para que el archivo sea validado antes de llegar a las tablas
finales.

El flujo principal del publicador es:

1. Ingresa al portal con sus credenciales corporativas.
2. Selecciona el proyecto disponible según sus permisos.
3. Selecciona el catálogo que desea cargar.
4. Revisa la información del catálogo:
   - tabla destino;
   - estrategia de carga;
   - motor destino;
   - columnas esperadas.
5. Sube uno o varios archivos en formato CSV, TXT o Excel.
6. Si el archivo lo requiere, ajusta delimitador, codificación u hoja de Excel.
7. Revisa la previsualización de los datos.
8. Ejecuta la validación.
9. Si la validación falla:
   - el sistema no escribe nada en la base de datos;
   - muestra errores por fila, columna, valor y regla incumplida;
   - permite descargar un reporte de errores;
   - el usuario corrige el archivo en origen y vuelve a cargarlo.
10. Si la validación es exitosa:
    - el usuario confirma la carga;
    - el sistema escribe en SingleStore o Hive según la estrategia configurada;
    - guarda evidencia auditada del archivo original;
    - registra la operación en `log_auditoria`.
11. El publicador puede consultar su historial de cargas y exportarlo si lo
    necesita.

Este flujo permite que el usuario de negocio conserve el control sobre sus
archivos, pero dentro de un marco de validación, permisos y auditoría definido
por la organización.

## Variables de entorno

La aplicación se configura mediante un archivo `.env`. Este archivo no debe
versionarse con credenciales reales. Para ambientes productivos se recomienda
mantenerlo fuera del repositorio y montarlo desde una ruta controlada por
infraestructura.

### Aplicación

```env
APP_ENV=production
APP_PORT=8501
LOG_LEVEL=INFO
```

- `APP_ENV`: identifica el ambiente de ejecución.
- `APP_PORT`: puerto interno usado por Streamlit.
- `LOG_LEVEL`: nivel de logs de la aplicación.

### SingleStore

```env
SS_HOST=
SS_PORT=3306
SS_DATABASE=gatekeeper_meta
SS_USER=
SS_PASSWORD=
SS_USE_BALANCER=false
SS_DIRECT_HOST=
SS_BALANCER_HOST=
DB_NAME_FILTERS=
```

- `SS_HOST`: host activo de SingleStore.
- `SS_PORT`: puerto de conexión.
- `SS_DATABASE`: base administrativa donde vive `gatekeeper_meta`.
- `SS_USER` y `SS_PASSWORD`: credenciales de conexión.
- `SS_USE_BALANCER`: permite elegir si se usa balanceador.
- `SS_DIRECT_HOST`: host directo.
- `SS_BALANCER_HOST`: host del balanceador.
- `DB_NAME_FILTERS`: filtro opcional para mostrar solo ciertas bases en el panel admin.

### Hive

```env
HIVE_HOST=
HIVE_PORT=10000
HIVE_DATABASE=default
HIVE_USER=
HIVE_PASSWORD=
```

- `HIVE_HOST`: host de HiveServer2.
- `HIVE_PORT`: puerto de conexión.
- `HIVE_DATABASE`: base por defecto.
- `HIVE_USER` y `HIVE_PASSWORD`: credenciales o usuario de conexión, según la configuración del entorno.

### LDAP / Active Directory

```env
LDAP_SERVER=
LDAP_PORT=389
LDAP_DOMAIN=
LDAP_AUTH_METHOD=NTLM
LDAP_BASE_DN=
LDAP_USE_SSL=false
LDAP_REQUIRED_GROUP=
```

Para Active Directory real se recomienda `LDAP_AUTH_METHOD=NTLM`. En ese modo el
usuario final se autentica con su usuario y contraseña corporativos desde la
pantalla de login.

Para OpenLDAP o pruebas con `SIMPLE`, también pueden requerirse:

```env
LDAP_BIND_USER=
LDAP_BIND_PASSWORD=
LDAP_USERS_OU=
LDAP_GROUPS_OU=
LDAP_ADMIN_DN=
LDAP_ADMIN_PASSWORD=
```

Estas variables corresponden a una cuenta técnica o a rutas internas del árbol
LDAP. No son las credenciales de los publicadores.

### Administrador local

```env
SYSTEM_ADMIN_USERNAME=admin
SYSTEM_ADMIN_PASSWORD=
```

Permite tener un acceso administrativo temporal si LDAP todavía no está
disponible. Debe usarse con clave fuerte y solo como contingencia o para pruebas.

### Tablas metadata

```env
TBL_PROYECTOS=proyectos
TBL_CATALOGOS=catalogos_config
TBL_USUARIOS=usuarios
TBL_PERMISOS=permisos_catalogo
TBL_LOG_AUDITORIA=log_auditoria
```

Permiten cambiar los nombres físicos de las tablas administrativas si el entorno
lo requiere.

### Auditoría

```env
AUDIT_STORAGE_PATH=/app/audit_storage
AUDIT_STORAGE_READONLY_AFTER_WRITE=true
AUDIT_STORAGE_DIR_MODE=750
AUDIT_STORAGE_FILE_MODE=440
```

- `AUDIT_STORAGE_PATH`: ruta donde se guardan los ZIP auditados.
- `AUDIT_STORAGE_READONLY_AFTER_WRITE`: deja el ZIP en modo solo lectura después de crearlo.
- `AUDIT_STORAGE_DIR_MODE`: permisos para directorios de auditoría.
- `AUDIT_STORAGE_FILE_MODE`: permisos para archivos ZIP.

La inmutabilidad fuerte debe completarse a nivel de infraestructura con permisos
de volumen, backups, retención y control de acceso al servidor.

### Validaciones

```env
MAX_UPLOAD_SIZE_MB=100
MAX_FILE_SIZE_MB=100
MAX_ROWS_IN_MEMORY=500000
```

- `MAX_FILE_SIZE_MB`: tamaño máximo por archivo.
- `MAX_ROWS_IN_MEMORY`: máximo de filas que pueden mantenerse en memoria durante una carga.

### Alertas

```env
ALERTS_ENABLED=false
ALERT_WEBHOOK_URL=
ALERT_ON_SUCCESS=false
```

Permiten enviar notificaciones operativas a un webhook cuando una carga falla o,
si se habilita, también cuando finaliza exitosamente.

## Construcción y despliegue con Docker

El proyecto incluye Docker para empaquetar la aplicación Streamlit y ejecutarla
detrás de Nginx.

### Componentes Docker

- `docker/Dockerfile`: construye la imagen Python/Streamlit de la app.
- `docker/docker-compose.yml`: despliegue pensado para servidor real; levanta
  `app` y `nginx`, conectándose a SingleStore, Hive y LDAP externos.
- `docker/docker-compose.prueba.yml`: entorno local de prueba; levanta
  SingleStore, Hive, OpenLDAP, la app y Nginx.
- `docker/nginx.conf`: proxy inverso hacia Streamlit.
- `docker/init_db/`: scripts para crear el esquema administrativo.

### Construir y levantar en servidor real

En el servidor real, la app espera que SingleStore, Hive y LDAP ya existan fuera
del compose. El archivo de entorno productivo se referencia desde:

```text
/opt/configs/data-gatekeeper/.env.prod
```

Comando:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

Esto construye la imagen de la aplicación, levanta el contenedor `gatekeeper_app`
y publica el acceso mediante `gatekeeper_nginx` en el puerto `80`.

Para revisar logs:

```bash
docker compose -f docker/docker-compose.yml logs -f app
```

Para detener:

```bash
docker compose -f docker/docker-compose.yml down
```

### Levantar entorno local de prueba

El compose de prueba simula la infraestructura completa:

- SingleStore;
- Hive;
- OpenLDAP;
- phpLDAPadmin;
- Streamlit app;
- Nginx.

Comando:

```bash
docker compose -f docker/docker-compose.prueba.yml up -d --build
```

La primera vez se debe inicializar la base administrativa:

```bash
docker exec -i gatekeeper_singlestore singlestore -uroot -pgatekeeper123 < docker/init_db/00_create_db.sql
docker exec -i gatekeeper_singlestore singlestore -uroot -pgatekeeper123 gatekeeper_meta < docker/init_db/01_init.sql
```

Luego se puede revisar la app desde Nginx:

```text
http://localhost
```

Y revisar logs:

```bash
docker compose -f docker/docker-compose.prueba.yml logs -f app
```

### Volumen de auditoría

El compose monta la carpeta de auditoría como volumen:

```yaml
volumes:
  - ../audit_storage:/app/audit_storage
```

Esto permite que los ZIP auditados sobrevivan aunque el contenedor se reconstruya
o reinicie.

### Healthchecks

La app expone el healthcheck de Streamlit:

```text
http://localhost:8501/_stcore/health
```

Nginx expone:

```text
http://localhost/healthz
```

Además, el script `scripts/healthcheck.py` valida conectividad básica hacia la
app, SingleStore, LDAP y, si está configurado, Hive.
