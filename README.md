# Data Gatekeeper

Portal interno de ingesta y validación de catálogos — Banco del Austro.

## Requisitos

- Python 3.9.16
- pip

## Instalación

```bash
# 1. Clonar / descomprimir el proyecto
cd data_gatekeeper

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con los valores reales del servidor
```

## Ejecución

```bash
# Modo demo (sin LDAP ni BD reales)
streamlit run app.py

# Producción
DEMO_MODE=false streamlit run app.py --server.port 8501
```

## Credenciales demo

| Usuario | Contraseña | Rol        |
|---------|------------|------------|
| vcastro | demo123    | Publicador |
| admin   | admin123   | Admin      |

## Estructura del proyecto

```
data_gatekeeper/
├── app.py                   # Entry point
├── requirements.txt
├── .env.example
├── .streamlit/
│   └── config.toml          # Tema y configuración Streamlit
├── auth/
│   └── ldap_auth.py         # Autenticación LDAP / demo
├── config/
│   ├── settings.py          # Variables de configuración
│   └── mock_catalogs.py     # Catálogos de prueba (demo)
├── validators/
│   └── engine.py            # Motor de validación pandera
├── views/
│   ├── login_view.py        # Pantalla de login
│   └── main_view.py         # App principal
└── utils/
    └── file_handler.py      # Lectura de CSV/Excel/TXT
```

## Pasar a producción

1. Cambiar `DEMO_MODE=false` en `.env`
2. Configurar credenciales LDAP reales
3. Configurar conexión a SingleStore
4. Reemplazar `mock_catalogs.py` por consulta real a `catalogos_config`
5. Implementar la escritura real en SingleStore/Hive en `views/main_view.py` → `_render_result_step`
6. Configurar Nginx como proxy inverso hacia el puerto 8501
