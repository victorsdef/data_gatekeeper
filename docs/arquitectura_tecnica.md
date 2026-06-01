# Data Gatekeeper

## Manual tecnico del sistema

Este documento describe la arquitectura tecnica de Data Gatekeeper para
desarrolladores y responsables de soporte. Explica como esta organizado el
codigo, como fluye una carga de catalogo, que contratos internos usa la
aplicacion y que configuracion debe revisarse antes de desplegar o modificar
el sistema.

El objetivo no es repetir el README funcional, sino dejar claro como mantener
el sistema sin romper autenticacion, permisos, validaciones, auditoria o carga
a destino.

## 1. Alcance tecnico

Data Gatekeeper es una aplicacion interna para cargar catalogos manuales de
forma controlada.

Desde el punto de vista tecnico, el sistema hace cuatro cosas principales:

1. autentica al usuario contra LDAP/Active Directory usando `ldapsearch`;
2. resuelve su rol interno y los catalogos a los que tiene acceso;
3. lee, previsualiza y valida archivos CSV, TXT o Excel contra una
   configuracion de catalogo;
4. carga datos validos a SingleStore o Hive y registra evidencia de auditoria.

La aplicacion esta construida en Python con Streamlit. La UI y la logica de
orquestacion corren en el mismo proceso de aplicacion; las operaciones de base
de datos, validacion, auditoria y autenticacion estan separadas en modulos.

## 2. Vista tecnica rapida

| Area | Implementacion |
| --- | --- |
| UI principal | Streamlit |
| Entrada de aplicacion | `app.py` |
| Autenticacion | `auth/ldap_auth.py` mediante binario `ldapsearch` |
| Sesion | `st.session_state` |
| Metadata | SingleStore, base `gatekeeper_meta` |
| Carga destino | SingleStore y, opcionalmente, Hive |
| Flag Hive | `HIVE_ENABLED` |
| Validacion | `validators/` |
| Persistencia destino | `services/db_writer.py` |
| Administracion | `views/admin_catalogs_view.py` y `services/db_admin.py` |
| Historial | `views/history_view.py` y `services/audit_service.py` |
| Evidencia auditada | `audit_storage/` |
| Docker | `docker/Dockerfile` y compose en `docker/` |

## 3. Mapa de ejecucion

El flujo general de la aplicacion es este:

1. `app.py` carga variables de entorno, configura Streamlit e inicializa estado
   de sesion.
2. Si no existe usuario autenticado en sesion, se muestra `views/login_view.py`.
3. El login llama a `auth/ldap_auth.py`.
4. Si la autenticacion es correcta, se registra o actualiza el usuario interno
   en metadata.
5. Segun rol, el usuario entra al portal de carga o al panel de administracion.
6. El portal carga catalogos activos desde metadata y los filtra por permisos.
7. El usuario sube archivos, el sistema los lee y muestra previsualizacion.
8. El validador compara columnas, tipos, nulabilidad y reglas de calidad.
9. Si hay errores, se bloquea la carga y se permite descargar reporte.
10. Si todo esta correcto, `services/db_writer.py` carga a destino.
11. `services/audit_service.py` registra resultado, usuario, archivo,
    operacion y ruta auditada.

## 4. Componentes principales

### `app.py`

Es el punto de entrada. Sus responsabilidades son:

- cargar configuracion general;
- inicializar `st.session_state`;
- decidir si mostrar login, portal, administracion o historial;
- aplicar estilos globales;
- mantener una navegacion simple entre vistas.

Este archivo no deberia contener reglas de negocio pesadas. Si una validacion,
consulta o carga crece, debe vivir en `validators/`, `services/` o `config/`.

### `views/login_view.py`

Renderiza el formulario de login.

Responsabilidades:

- pedir usuario y contrasena;
- llamar al servicio de autenticacion;
- guardar datos de sesion si el login es valido;
- mostrar error generico si falla.

No debe conocer detalles del comando `ldapsearch`; eso pertenece a
`auth/ldap_auth.py`.

### `auth/ldap_auth.py`

Modulo de autenticacion. Actualmente no usa `ldap3`.

La autenticacion soportada ejecuta el binario del sistema `ldapsearch` mediante
`subprocess`. Esto permite trabajar tanto con Active Directory real como con un
LDAP simulado en Docker, siempre que el contenedor o servidor tenga disponible
el binario.

Responsabilidades:

- construir el comando `ldapsearch`;
- hacer bind con el usuario;
- buscar atributos del usuario autenticado;
- detectar credenciales invalidas;
- resolver datos basicos como nombre, correo y grupos;
- aplicar fallback de admin local si esta configurado.

### `views/main_view.py`

Vista principal del publicador y tambien punto de carga para admins.

Responsabilidades:

- mostrar proyectos y catalogos permitidos;
- mostrar informacion del catalogo seleccionado;
- recibir archivos;
- leer y previsualizar datos;
- ejecutar validacion;
- confirmar carga si la validacion fue exitosa;
- mostrar resultado final y resumen de auditoria.

### `views/admin_catalogs_view.py`

Panel tecnico-funcional del administrador.

Responsabilidades:

- explorar bases y tablas disponibles;
- registrar catalogos desde SingleStore o Hive;
- definir nombre legible, proyecto, estrategia y destino;
- configurar columnas, tipos, nulabilidad y reglas;
- administrar catalogos activos;
- editar configuracion existente;
- editar permisos por rol o usuario;
- respaldar/restaurar configuraciones JSON;
- administrar usuarios internos.

Cuando `HIVE_ENABLED=false`, las opciones de Hive no deben mostrarse ni quedar
disponibles para registro o carga.

### `views/history_view.py`

Vista de auditoria de cargas.

Responsabilidades:

- mostrar filtros por fecha y estado;
- mostrar totales, exitos, fallos y filas procesadas;
- mostrar tendencias;
- listar cargas en tabla;
- exportar historial a CSV.

Regla importante:

- un publicador solo ve sus propias cargas;
- un admin ve cargas de todos los usuarios.

### `config/settings.py`

Centraliza variables de entorno.

Las variables de entorno deben leerse aqui o mediante funciones de este modulo,
para evitar que la configuracion quede repartida por todo el codigo.

### `config/catalogs.py`

Normaliza y filtra catalogos disponibles.

Responsabilidades:

- leer catalogos activos desde metadata;
- aplicar filtros de permisos;
- ocultar catalogos Hive cuando `HIVE_ENABLED=false`;
- entregar estructuras estables para las vistas.

### `services/db_admin.py`

Servicio de administracion de metadata y exploracion de bases.

Responsabilidades:

- listar bases y tablas disponibles;
- leer esquemas;
- registrar catalogos;
- actualizar catalogos;
- activar o desactivar catalogos;
- guardar permisos;
- exportar/importar configuraciones.

### `services/db_writer.py`

Servicio que escribe datos validados al destino.

Responsabilidades:

- recibir un `DataFrame` ya validado;
- aplicar estrategia de carga;
- escribir a SingleStore o Hive;
- devolver resultado de carga;
- bloquear Hive si `HIVE_ENABLED=false`.

La validacion debe ocurrir antes de llamar a este servicio.

### `validators/`

Contiene la logica de calidad de datos.

Responsabilidades:

- revisar columnas requeridas;
- detectar columnas no esperadas;
- validar tipos;
- validar nulabilidad;
- aplicar reglas por columna;
- producir errores descargables.

El validador no debe escribir en base de datos.

### `services/audit_service.py`

Registra trazabilidad.

Responsabilidades:

- crear identificador de operacion;
- guardar resultado en metadata;
- registrar usuario, archivo, catalogo, estado y filas;
- guardar evidencia en almacenamiento auditado;
- alimentar el historial.

## 5. Contratos internos

### Catalogo activo

Las vistas trabajan con una estructura de catalogo que debe contener, como
minimo:

| Campo | Uso |
| --- | --- |
| `catalog_id` | Identificador interno del catalogo |
| `project_id` | Agrupador tecnico del proyecto |
| `project_name` | Nombre visible del proyecto |
| `display_name` | Nombre visible del catalogo |
| `database_name` | Base origen o destino |
| `table_name` | Tabla fisica |
| `destination` | `singlestore` o `hive` |
| `load_strategy` | Estrategia de carga |
| `schema_json` | Definicion de columnas y reglas |
| `allowed_roles` | Roles permitidos |
| `allowed_users` | Usuarios especificos permitidos |
| `is_active` | Estado de publicacion |

Si `allowed_users` esta vacio, el permiso aplica segun roles. Si roles y
usuarios estan vacios, el catalogo queda accesible para publicadores segun la
regla de negocio definida en administracion.

### Esquema de columnas

Cada columna configurada debe representar:

| Campo | Descripcion |
| --- | --- |
| `name` | Nombre esperado en el archivo |
| `type` | Tipo logico: `str`, `int`, `float`, `date`, etc. |
| `nullable` | Si permite valores vacios |
| `rules` | Lista de reglas de calidad |

Las reglas dependen del tipo. Ejemplos:

- minimo o maximo para numeros;
- dominio de valores permitidos para texto;
- longitud minima o maxima;
- formato esperado;
- obligatoriedad.

### Resultado de validacion

El proceso de validacion debe devolver informacion suficiente para decidir si
se puede cargar:

| Dato | Uso |
| --- | --- |
| `success` | Indica si no hay errores |
| `errors` | Lista tabular de errores |
| `rows_validated` | Filas revisadas |
| `columns_validated` | Columnas revisadas |
| `error_report` | Archivo descargable si hay errores |

Si existen errores, no se debe llamar a `db_writer`.

### Sesion Streamlit

Las claves de sesion mas sensibles son las relacionadas con:

- usuario autenticado;
- rol actual;
- catalogo seleccionado;
- archivo cargado;
- datos previsualizados;
- resultado de validacion;
- paso actual del flujo.

Cuando se cambia de catalogo o se inicia nueva carga, se debe limpiar el estado
asociado al archivo anterior para evitar que una validacion vieja se use en un
catalogo distinto.

## 6. Autenticacion LDAP/AD

El sistema usa `ldapsearch` nativo del sistema operativo o del contenedor.

No se debe agregar nuevamente `ldap3` salvo que se decida cambiar formalmente
la estrategia de autenticacion.

### Comando base

La forma general del comando es:

```bash
ldapsearch -x \
  -H ldap://servidor:389 \
  -D "<bind_del_usuario>" \
  -w "<password>" \
  -b "<base_dn>" \
  "(<atributo>=<usuario>)"
```

Para Active Directory real, el bind normalmente usa:

```text
usuario@dominio
```

Para el LDAP simulado en Docker, el bind usa un DN completo:

```text
uid=usuario,ou=users,dc=austro,dc=grpfin
```

### Variables LDAP principales

| Variable | Uso |
| --- | --- |
| `LDAP_SERVER` | URL del servidor, por ejemplo `ldap://austro.grpfin:389` |
| `LDAP_DOMAIN` | Dominio usado para bind tipo `usuario@dominio` |
| `LDAP_BASE_DN` | Base de busqueda |
| `LDAP_SEARCH_ATTRIBUTE` | Atributo para localizar usuario: `sAMAccountName` o `uid` |
| `LDAP_BIND_TEMPLATE` | Plantilla opcional para construir el bind DN |
| `LDAPSEARCH_BIN` | Nombre o ruta del binario `ldapsearch` |
| `LDAP_REQUIRED_GROUP` | Grupo requerido para permitir ingreso |
| `LDAP_SEARCH_BIND_DN` | Bind tecnico opcional para completar busqueda |
| `LDAP_SEARCH_BIND_PASSWORD` | Contrasena del bind tecnico |

### Real vs simulado

Configuracion tipica para AD real:

```env
LDAP_SERVER=ldap://austro.grpfin:389
LDAP_DOMAIN=austro.grpfin
LDAP_BASE_DN=DC=austro,DC=grpfin
LDAP_SEARCH_ATTRIBUTE=sAMAccountName
LDAP_BIND_TEMPLATE={username}@{domain}
LDAPSEARCH_BIN=ldapsearch
```

Configuracion tipica para Docker simulado:

```env
LDAP_SERVER=ldap://ldap:389
LDAP_DOMAIN=austro.grpfin
LDAP_BASE_DN=dc=austro,dc=grpfin
LDAP_SEARCH_ATTRIBUTE=uid
LDAP_BIND_TEMPLATE=uid={username},ou=users,{base_dn}
LDAPSEARCH_BIN=ldapsearch
LDAP_SEARCH_BIND_DN=cn=admin,dc=austro,dc=grpfin
LDAP_SEARCH_BIND_PASSWORD=admin
```

### Roles

El rol interno no debe depender solo del texto ingresado en login. El sistema
debe registrar o actualizar al usuario en metadata y asignarle rol de acuerdo
con:

- grupos LDAP/AD cuando esten disponibles;
- configuracion interna de usuarios;
- fallback local para administrador tecnico si esta habilitado.

## 7. Metadata

La metadata vive en SingleStore, usualmente en la base `gatekeeper_meta`.

Tablas esperadas:

| Tabla | Proposito |
| --- | --- |
| `usuarios` | Usuarios internos, rol, estado y ultimo acceso |
| `catalogos_config` | Configuracion de catalogos activos o inactivos |
| `permisos_catalogo` | Permisos por rol o usuario, si aplica |
| `log_auditoria` | Registro de cargas y resultados |

### `catalogos_config`

Debe guardar la configuracion necesaria para que el publicador pueda cargar sin
conocer detalles tecnicos:

- proyecto;
- tabla fisica;
- nombre legible;
- destino;
- estrategia;
- esquema esperado;
- reglas por columna;
- permisos;
- estado activo/inactivo.

### `usuarios`

Se actualiza al iniciar sesion. Permite:

- diferenciar `Admin` y `Publicador`;
- desactivar acceso al sistema;
- recordar ultimo acceso;
- respaldar/restaurar usuarios internos.

### `log_auditoria`

Registra cada intento de carga:

- fecha y hora;
- usuario;
- proyecto;
- catalogo;
- archivo;
- filas;
- estrategia;
- destino;
- estado;
- operacion;
- ruta auditada.

## 8. Administracion de catalogos

El panel admin se divide en cuatro secciones: resumen, registrar catalogos,
catalogos activos y usuarios.

### Resumen

Muestra una vista general de actividad y estado de configuraciones. Sirve como
entrada rapida para revisar si el sistema tiene catalogos activos, usuarios y
cargas registradas.

### Registrar catalogos

El registro usa dos pasos.

Paso 1: seleccion de tablas.

- El admin escoge el motor disponible: SingleStore o Hive.
- Si `HIVE_ENABLED=false`, Hive no aparece.
- El explorador lista bases disponibles.
- Al seleccionar una base, se listan sus tablas.
- El admin puede buscar, refrescar y seleccionar una o varias tablas.
- La tabla activa muestra su estructura de columnas.
- Cada columna muestra tipo y si acepta nulos.
- Desde la misma vista se pueden agregar reglas de calidad por columna.

Paso 2: configuracion y registro.

- El admin define o acepta el ID del proyecto.
- El admin define el nombre visible del proyecto.
- El sistema genera el ID tecnico del catalogo.
- El admin define el nombre legible del catalogo.
- Puede agregar descripcion.
- Selecciona estrategia de carga.
- Selecciona destino.
- Define roles permitidos.
- Define usuarios especificos si el acceso debe limitarse.

Si no se seleccionan usuarios especificos, el acceso se controla por roles. Si
la configuracion queda abierta segun la regla de negocio, el catalogo puede
quedar disponible para todos los publicadores permitidos.

### Registro de varias tablas

Cuando se selecciona mas de una tabla, el flujo es el mismo, pero el sistema
registra un lote.

El proyecto funciona como carpeta logica. Por ejemplo, varias tablas de un
mismo dominio, como COMEX o SRI, pueden guardarse bajo el mismo proyecto y
luego mostrarse al usuario con su nombre legible.

Cada tabla conserva:

- tabla fisica;
- nombre legible;
- esquema;
- reglas;
- estrategia;
- destino;
- permisos.

### Catalogos activos

Esta seccion permite mantener lo ya registrado.

Funciones principales:

- preparar backup JSON de configuraciones;
- restaurar configuraciones desde JSON;
- decidir si se sobrescriben catalogos existentes;
- buscar catalogos por nombre, base o tabla;
- ver catalogos agrupados por proyecto;
- abrir una carpeta de proyecto para ver sus tablas;
- editar configuracion;
- editar permisos;
- desactivar catalogos.

Editar configuracion permite cambiar nombre, descripcion, estrategia, destino,
columnas, tipos, nulabilidad y reglas.

Editar permisos permite ajustar roles y usuarios especificos. Esto afecta que
catalogos ve cada usuario en el portal de carga.

Desactivar no deberia borrar historico. Solo impide que el catalogo siga
disponible para nuevas cargas.

### Usuarios

La seccion de usuarios administra usuarios internos detectados por login.

Funciones principales:

- preparar backup JSON de usuarios;
- restaurar usuarios desde JSON;
- sobrescribir usuarios existentes si se marca la opcion;
- cambiar rol entre `Admin` y `Publicador`;
- activar o desactivar acceso al sistema;
- ver ultimo acceso.

Desactivar un usuario impide usar el sistema aunque sus credenciales LDAP sean
validas.

## 9. Portal de carga

El portal de carga es usado por publicadores y tambien por admins cuando
necesitan cargar archivos.

### Seleccion de proyecto y catalogo

El usuario solo ve proyectos y catalogos permitidos.

El filtro se calcula con:

- rol interno;
- usuario especifico;
- estado activo del catalogo;
- destino habilitado;
- configuracion de permisos.

La barra lateral muestra informacion del catalogo:

- tabla fisica;
- estrategia;
- destino;
- columnas esperadas.

### Paso 1: subir archivo

El usuario puede cargar uno o varios archivos permitidos.

El sistema:

- detecta separador en CSV/TXT cuando aplica;
- lee Excel cuando aplica;
- muestra cantidad de filas, columnas y archivos;
- presenta una previsualizacion;
- permite ajustar lectura si las columnas no se ven correctamente.

La previsualizacion no carga datos a la base. Solo confirma que el archivo se
lee de forma esperada.

### Paso 2: validar

El usuario ejecuta la validacion antes de cargar.

Validaciones principales:

- archivo legible;
- archivo no vacio;
- columnas requeridas presentes;
- columnas no esperadas detectadas;
- tipos compatibles;
- campos obligatorios no vacios;
- reglas de calidad por columna.

### Validacion con errores

Si falla la validacion:

- no se carga nada a la base;
- se muestra un resumen de errores;
- se lista fila, columna, valor encontrado, regla y detalle;
- se permite descargar un reporte `.xlsx`;
- el usuario debe corregir el archivo en origen y volver a intentarlo.

### Validacion exitosa

Si la validacion no encuentra errores:

- se muestran filas validadas;
- se muestra conteo de errores en cero;
- se habilita la confirmacion de carga;
- el usuario puede volver al archivo si necesita revisar antes de confirmar.

La validacion exitosa todavia no implica carga final; la carga ocurre al
confirmar.

### Paso 3: resultado

Cuando el usuario confirma:

- se ejecuta la carga a destino;
- se aplica la estrategia configurada;
- se registra auditoria;
- se guarda evidencia del archivo;
- se muestra estado final.

Si la carga termina correctamente, la pantalla muestra:

- filas cargadas;
- tabla destino;
- estrategia;
- estado;
- operacion;
- usuario;
- archivo;
- fecha;
- ruta auditada.

## 10. Estrategias de carga

Las estrategias pueden variar por catalogo. Las comunes son:

| Estrategia | Descripcion |
| --- | --- |
| `append` | Inserta nuevas filas sin limpiar la tabla |
| `overwrite` | Reemplaza el contenido destino antes de insertar |
| `reproceso` | Aplica logica especial de reproceso si esta implementada |

La estrategia debe resolverse en `services/db_writer.py` y no en la vista.

## 11. Destinos de datos

### SingleStore

Es el destino principal.

La conexion se configura con variables `SS_*`. Las operaciones administrativas
pueden explorar bases, tablas y esquemas para registrar catalogos.

### Hive

Hive es opcional y esta controlado por:

```env
HIVE_ENABLED=true
```

Cuando `HIVE_ENABLED=false`:

- no se muestran opciones Hive en administracion;
- no se listan catalogos Hive;
- no se permite cargar a Hive;
- el healthcheck no exige Hive.

Esto permite operar el sistema en ambientes donde Hive no esta disponible.

## 12. Auditoria y almacenamiento

Toda carga debe dejar evidencia.

La auditoria tiene dos niveles:

1. registro estructurado en metadata;
2. archivo auditado en almacenamiento local o volumen.

El almacenamiento auditado debe conservar el archivo original y datos de la
operacion. La ruta se muestra en resultado final y queda disponible en el
historial.

Reglas tecnicas:

- cada carga debe tener `operation_id`;
- las cargas fallidas tambien deben registrarse cuando aplique;
- una validacion fallida no debe insertar datos;
- el archivo original no debe modificarse;
- la evidencia no debe contener credenciales.

## 13. Configuracion por entorno

Los archivos `.env` controlan comportamiento del sistema.

Variables importantes:

| Variable | Descripcion |
| --- | --- |
| `APP_ENV` | Ambiente logico |
| `SS_HOST` | Host SingleStore |
| `SS_PORT` | Puerto SingleStore |
| `SS_USER` | Usuario SingleStore |
| `SS_PASSWORD` | Contrasena SingleStore |
| `SS_DATABASE` | Base metadata o destino por defecto |
| `HIVE_ENABLED` | Activa/oculta Hive |
| `HIVE_HOST` | Host Hive |
| `HIVE_PORT` | Puerto Hive |
| `LDAP_SERVER` | Servidor LDAP/AD |
| `LDAP_BASE_DN` | Base DN |
| `LDAP_DOMAIN` | Dominio de bind |
| `LDAP_BIND_TEMPLATE` | Plantilla de bind |
| `LDAP_SEARCH_ATTRIBUTE` | Atributo de busqueda |
| `LDAPSEARCH_BIN` | Binario ldapsearch |
| `AUDIT_STORAGE_PATH` | Ruta de evidencia auditada |

Buenas practicas:

- no versionar credenciales reales;
- mantener `.env.example` sin secretos;
- usar `.env.local-sim-real` solo para pruebas locales;
- documentar cambios de variables cuando cambie el codigo.

## 14. Docker local

El Dockerfile instala dependencias del sistema, incluyendo `ldap-utils`, porque
la autenticacion depende de `ldapsearch`.

Para levantar el ambiente local simulado con LDAP parecido al real:

```bash
docker compose -f docker/docker-compose.local-sim-real.yml --env-file docker/.env.local-sim-real up -d --build
```

Para ver logs de la app:

```bash
docker compose -f docker/docker-compose.local-sim-real.yml --env-file docker/.env.local-sim-real logs -f app
```

Para bajar el ambiente:

```bash
docker compose -f docker/docker-compose.local-sim-real.yml --env-file docker/.env.local-sim-real down
```

Usuarios de prueba del LDAP simulado:

| Usuario | Contrasena | Rol esperado |
| --- | --- | --- |
| `vcastro` | `demo123` | Publicador |
| `admin` | `admin123` | Admin LDAP simulado, si se usa esa ruta |

Tambien puede existir admin local segun configuracion del sistema.

## 15. Healthcheck y observabilidad

El healthcheck vive en:

```text
scripts/healthcheck.py
```

Debe validar:

- que la aplicacion pueda responder;
- que SingleStore este disponible;
- que Hive se revise solo si `HIVE_ENABLED=true`;
- que errores sean visibles en logs.

Logs importantes:

- errores de autenticacion LDAP;
- fallos de lectura de archivo;
- errores de validacion;
- fallos de escritura a destino;
- errores de auditoria.

No se deben imprimir contrasenas en logs.

## 16. Reglas de mantenimiento

Antes de modificar una parte del sistema, revisar estas dependencias:

| Si cambias | Revisa tambien |
| --- | --- |
| Login | `auth/ldap_auth.py`, `views/login_view.py`, usuarios metadata |
| Roles | permisos de catalogo, usuarios, historial |
| Registro de catalogos | `db_admin`, schema JSON, filtros del publicador |
| Validaciones | reporte de errores, UI de validacion, tests |
| Carga destino | auditoria, historial, rollback o limpieza |
| Hive | `HIVE_ENABLED`, admin, writer, healthcheck |
| Docker | variables `.env`, Dockerfile, compose, healthcheck |

## 17. Pruebas recomendadas

Pruebas minimas por cambio:

1. Login correcto con usuario publicador.
2. Login incorrecto.
3. Admin puede entrar al panel.
4. Publicador solo ve catalogos permitidos.
5. Admin ve catalogos segun configuracion.
6. Archivo correcto muestra previsualizacion.
7. Archivo con columnas erroneas falla validacion.
8. Reporte de errores se descarga.
9. Validacion exitosa habilita confirmar carga.
10. Carga exitosa registra auditoria.
11. Historial de publicador muestra solo sus cargas.
12. Historial de admin muestra todas.
13. Con `HIVE_ENABLED=false`, Hive no aparece ni se usa.

Pruebas tecnicas utiles en Docker:

```bash
docker exec -it gatekeeper_app sh -c "env | grep LDAP"
docker exec -it gatekeeper_app sh -c "ldapsearch -x -H ldap://ldap:389 -D 'uid=vcastro,ou=users,dc=austro,dc=grpfin' -w demo123 -b 'dc=austro,dc=grpfin' '(uid=vcastro)'"
docker compose -f docker/docker-compose.local-sim-real.yml --env-file docker/.env.local-sim-real logs -f app
```

## 18. Puntos de extension

El sistema puede crecer de forma ordenada en estos puntos:

- nuevos tipos de regla en `validators/`;
- nuevos destinos en `services/db_writer.py`;
- nuevos motores de exploracion en `services/db_admin.py`;
- integracion con almacenamiento externo para auditoria;
- permisos mas granulares por proyecto;
- reportes historicos mas completos;
- pruebas automatizadas de validacion y carga.

Cada extension debe respetar tres reglas:

1. validar antes de cargar;
2. registrar auditoria;
3. no mostrar opciones que el entorno tenga deshabilitadas.

## 19. Resumen para desarrolladores

Data Gatekeeper es un portal Streamlit con logica backend modular. El usuario
entra por LDAP, selecciona un catalogo permitido, sube un archivo, valida contra
la configuracion almacenada en metadata y solo entonces carga a destino.

La parte critica del sistema esta en estos contratos:

- autenticacion por `ldapsearch`;
- permisos por rol/usuario;
- `schema_json` de catalogos;
- validacion previa obligatoria;
- `HIVE_ENABLED` para ocultar o bloquear Hive;
- auditoria obligatoria de cada carga.

Si esos contratos se mantienen, el sistema puede evolucionar sin perder control
sobre seguridad, calidad de datos y trazabilidad.
