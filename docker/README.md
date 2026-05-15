# Data Gatekeeper — Docker

## Estructura de carpetas requerida

```
proyecto/
├── docker/
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── .env
│   └── init_db/
│       └── 01_init.sql
└── data_gatekeeper/
    ├── app.py
    ├── requirements.txt
    └── ... (resto del proyecto)
```

## Levantar todo

```powershell
# Entrar a la carpeta docker
cd docker

# Levantar los contenedores
docker-compose up --build
```

## URLs una vez levantado

| Servicio | URL |
|----------|-----|
| Portal Data Gatekeeper | http://localhost:8501 |
| SingleStore Studio (UI web) | http://localhost:8080 |

## Credenciales SingleStore

| Campo | Valor |
|-------|-------|
| Host | localhost |
| Puerto | 3306 |
| Usuario | root |
| Contraseña | gatekeeper123 |
| Base de datos | gatekeeper_meta |

## Usuarios de la simulación LDAP

| Usuario | Contraseña | Rol |
|---------|------------|-----|
| vcastro | demo123 | Publicador |
| admin | admin123 | Admin |

## Comandos útiles

```powershell
# Ver logs del portal
docker logs gatekeeper_app -f

# Ver logs de SingleStore
docker logs gatekeeper_singlestore -f

# Detener todo
docker-compose down

# Detener y borrar datos
docker-compose down -v

# Reiniciar solo el portal (sin reconstruir)
docker-compose restart app

# Reconstruir solo el portal
docker-compose up --build app
```

## Conectarse a SingleStore desde la terminal

```powershell
docker exec -it gatekeeper_singlestore singlestore -u root -pgatekeeper123 gatekeeper_meta
```
