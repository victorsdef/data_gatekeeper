# Data Gatekeeper

Data Gatekeeper es un portal interno para cargar archivos de catalogos de forma
controlada, segura y auditada.

Su funcion principal es revisar un archivo antes de enviarlo a la base de
datos. Si el archivo tiene errores, el sistema lo rechaza para evitar que se
dañe la informacion final.

## Que es este sistema

Este portal sirve como punto unico de ingreso para archivos manuales como CSV,
TXT o Excel.

En lugar de cargar archivos por correo, por procesos informales o con apoyo
tecnico cada vez, el usuario puede hacerlo directamente desde el portal, siempre
que tenga permisos sobre el catalogo correspondiente.

## Inicio de sesion

El usuario entra al portal con sus credenciales y desde ahi accede segun su
perfil y permisos.

Imagen sugerida:

![Pantalla de login](assets/login.png)

## Seleccion de proyecto y catalogo

Una vez dentro del portal, el usuario selecciona el proyecto y el catalogo que
tiene habilitado para cargar.

Imagen sugerida:

![Seleccion de proyecto y catalogo](assets/seleccion_catalogo.png)`

## Que puede hacer el usuario

Con Data Gatekeeper, un usuario puede:

- iniciar sesion con sus credenciales;
- seleccionar el proyecto y catalogo que tiene habilitado;
- subir uno o varios archivos permitidos;
- revisar una previsualizacion antes de continuar;
- validar el contenido antes de cargarlo;
- ver los errores encontrados si el archivo no cumple las reglas;
- descargar un reporte de errores;
- confirmar la carga si todo esta correcto;
- consultar su historial de cargas.

## Perfiles del sistema

Dentro del portal existen dos perfiles principales:

- `Publicador`: usuario que carga archivos.
- `Admin`: usuario que configura y controla el portal.

## Que hace el publicador

El publicador es la persona responsable de subir el archivo correcto y revisar
si cumple las reglas del catalogo antes de cargarlo.

En la practica, el publicador puede:

- ingresar al portal con sus credenciales;
- ver solo los proyectos y catalogos para los que tiene permiso;
- seleccionar el catalogo que desea cargar;
- revisar informacion basica del catalogo;
- subir uno o varios archivos permitidos;
- ajustar opciones de lectura si el archivo no se visualiza bien;
- revisar una previsualizacion antes de validar;
- ejecutar la validacion del archivo;
- revisar los errores encontrados por fila, columna y motivo;
- descargar un reporte de errores;
- confirmar la carga cuando la validacion sea exitosa;
- consultar su historial de cargas;
- volver a intentar una carga despues de corregir un archivo.

### Carga de archivos

El publicador puede subir archivos permitidos y revisar si fueron leidos
correctamente antes de continuar.

Imagen sugerida:

`![Carga de archivos](assets/carga_archivo.png)`

### Previsualizacion del archivo

Antes de validar, el usuario puede revisar una muestra del contenido para
confirmar que columnas y datos se ven correctamente.

Imagen sugerida:

`![Previsualizacion del archivo](assets/previsualizacion.png)`

### Validacion con errores

Si el archivo no cumple las reglas, el sistema muestra el detalle de los
errores para que el usuario los corrija.

Imagen sugerida:

`![Errores de validacion](assets/errores_validacion.png)`

### Carga exitosa

Si todo esta correcto, el sistema permite confirmar la carga y muestra el
resultado final.

Imagen sugerida:

`![Carga exitosa](assets/carga_exitosa.png)`

## Que no hace el publicador

El publicador no administra el sistema ni cambia la configuracion interna del
catalogo.

Por eso, normalmente no puede:

- crear nuevos catalogos;
- cambiar reglas de validacion;
- cambiar permisos de otros usuarios;
- modificar usuarios o roles;
- cargar catalogos que no tiene habilitados;
- forzar una carga con errores;
- omitir la validacion previa.

## Que hace el admin

El admin es la persona encargada de preparar el portal para que los
publicadores puedan trabajar de forma controlada.

En la practica, el admin puede:

- ingresar al panel de administracion;
- revisar un resumen general de actividad;
- ver catalogos activos;
- registrar nuevos catalogos;
- asociar catalogos a proyectos;
- definir el nombre visible del catalogo;
- indicar la tabla destino;
- definir la estrategia de carga;
- configurar la estructura esperada del archivo;
- definir tipos de dato por columna;
- marcar si una columna permite o no valores vacios;
- configurar reglas de validacion;
- asignar permisos por rol o por usuario;
- activar o desactivar catalogos;
- editar configuraciones existentes;
- revisar el historial de cargas;
- administrar usuarios del portal;
- cambiar roles de usuarios;
- activar o desactivar usuarios segun necesidad.

### Panel de administracion

Desde esta vista el admin puede configurar catalogos, permisos y usuarios.

Imagen sugerida:

`![Panel de administracion](assets/admin_panel.png)`

## Que no hace el admin desde el portal

Aunque el admin tiene mas permisos, el portal no reemplaza tareas de
infraestructura ni de desarrollo.

Por eso, el admin normalmente no hace desde aqui:

- cambios tecnicos del servidor;
- cambios directos en la base de datos fuera del flujo del portal;
- correcciones automáticas del contenido de un archivo;
- desarrollo de nuevas funcionalidades;
- administracion del sistema operativo o de la red.

## Que revisa el sistema antes de cargar

Antes de aceptar una carga, el sistema puede revisar cosas como estas:

- que el archivo tenga las columnas correctas;
- que no falten columnas obligatorias;
- que no existan columnas adicionales;
- que el archivo no este vacio;
- que los datos tengan el formato esperado;
- que los campos obligatorios no vengan vacios;
- que ciertos valores esten dentro de lo permitido;
- que algunos textos o numeros cumplan reglas definidas para ese catalogo.

Si alguna de estas validaciones falla, no se carga nada.

## Que pasa si el archivo tiene errores

Si el archivo no cumple las reglas:

- la carga se rechaza;
- el sistema muestra el detalle del problema;
- el usuario debe corregir el archivo en origen;
- luego puede volver a intentarlo.

El sistema no corrige los datos automaticamente ni completa informacion faltante.

## Que si puede hacer el sistema

- validar el archivo antes de la carga;
- proteger la tabla destino de archivos incorrectos;
- mostrar errores de forma clara;
- registrar quien hizo la carga y cuando;
- guardar evidencia auditada del archivo cargado;
- permitir trazabilidad de cargas exitosas o fallidas.

## Que no hace el sistema

- no corrige errores del archivo por el usuario;
- no modifica reglas de negocio desde la pantalla del publicador;
- no permite cargar catalogos para los que no tengas permiso;
- no garantiza la carga si el archivo tiene errores;
- no reemplaza la responsabilidad del usuario sobre el contenido del archivo.

## Que esperar del proceso de carga

Cuando un usuario sube un archivo, el sistema sigue una logica simple:

1. recibe el archivo;
2. revisa si el formato puede leerse;
3. muestra una previsualizacion;
4. valida estructura y contenido;
5. si hay errores, detiene el proceso;
6. si todo esta correcto, permite confirmar la carga;
7. registra el resultado para auditoria e historial.

Esto significa que una carga exitosa depende tanto del archivo como de los
permisos y la configuracion del catalogo.

## Historial de cargas

El sistema permite consultar el historial de cargas para revisar intentos
exitosos o fallidos.

Imagen sugerida:

`![Historial de cargas](assets/historial.png)`

## Como usarlo de forma simple

1. Ingresa al portal.
2. Elige el proyecto y el catalogo.
3. Sube tu archivo.
4. Revisa la previsualizacion.
5. Ejecuta la validacion.
6. Corrige el archivo si el sistema reporta errores.
7. Confirma la carga solo cuando la validacion sea exitosa.

## Que gana el usuario con este portal

El usuario mantiene el control de su archivo, pero dentro de un proceso mas
seguro y ordenado.

Esto ayuda a:

- reducir errores en tablas finales;
- evitar retrabajos posteriores;
- tener mas claridad sobre por que una carga falla;
- contar con un historial de lo que ya fue cargado;
- trabajar con un proceso estandar y auditable.

## Recomendacion para las imagenes

Para que el README se vea ordenado, procura que las capturas:

- tengan nombres simples;
- muestren solo la parte importante;
- no expongan datos sensibles;
- mantengan un orden parecido al flujo real del usuario.
