"""
utils/error_messages.py
Mensajes de error seguros y entendibles para mostrar en la UI.

Los detalles técnicos deben quedar en logs/auditoría, no en pantalla.
"""
from __future__ import annotations

from typing import Any


def user_facing_error(exc: Any, context: str = "general") -> str:
    """
    Convierte una excepción técnica en un mensaje breve para usuario final.

    context ayuda a elegir palabras de negocio sin exponer IPs, puertos,
    nombres internos de servidores, códigos SQL ni trazas.
    """
    raw = str(exc or "").strip()
    text = raw.lower()

    if not raw:
        return "Ocurrió un error inesperado. Intenta nuevamente o contacta al administrador."

    if any(token in text for token in (
        "only one dialog is allowed",
        "dialog-decorated function",
        "streamlitapiexception",
    )):
        return "La ventana de resultado ya está abierta o se acaba de cerrar. Vuelve a intentarlo desde el botón de resultado."

    if any(token in text for token in (
        "connection refused",
        "connect timed out",
        "timed out",
        "timeout",
        "unreachable",
        "could not connect",
        "can't connect",
        "cannot connect",
        "connection reset",
        "connection aborted",
        "getaddrinfo",
        "name or service not known",
        "temporary failure in name resolution",
        "network is unreachable",
        "no route to host",
        "communications link failure",
        "lost connection",
        "broken pipe",
    )):
        return _connection_message(context)

    if any(token in text for token in (
        "access denied",
        "permission denied",
        "not authorized",
        "unauthorized",
        "authentication failed",
        "invalid credentials",
        "password",
        "denied",
    )):
        return "No tienes permisos suficientes o las credenciales del servicio no son válidas. Contacta al administrador."

    if any(token in text for token in (
        "cannot be blank",
        "1103",
        "unknown database",
        "database does not exist",
        "unknown table",
        "table doesn't exist",
        "no such table",
        "does not exist",
    )):
        return "La base o tabla configurada para este catálogo no existe o no está disponible. Contacta al administrador."

    if any(token in text for token in (
        "duplicate",
        "primary key",
        "unique constraint",
        "duplicate entry",
    )):
        return "La carga contiene registros duplicados o datos que ya existen en la tabla destino."

    if any(token in text for token in (
        "data too long",
        "out of range",
        "incorrect integer",
        "incorrect decimal",
        "truncated",
        "cannot be null",
        "invalid date",
    )):
        return "La tabla destino rechazó algunos valores por formato, longitud, nulidad o tipo de dato."

    if any(token in text for token in (
        "syntax",
        "sql",
        "parseexception",
        "semanticexception",
    )):
        return "La operación no pudo ejecutarse por una configuración SQL del catálogo. Contacta al administrador."

    if any(token in text for token in (
        "no se encontró columna de fecha",
        "columna de fecha",
        "partition",
        "partición",
    )):
        return "El reproceso requiere una columna de fecha válida en el archivo o en la configuración del catálogo."

    if any(token in text for token in (
        "no space left",
        "disk full",
        "read-only file system",
        "permissionerror",
        "permission denied",
    )):
        return "No se pudo guardar la evidencia de auditoría. Contacta al administrador."

    if any(token in text for token in (
        "no module named",
        "importerror",
        "module not found",
    )):
        return "Falta una dependencia técnica del sistema. Contacta al administrador."

    return "Ocurrió un error inesperado durante la operación. Intenta nuevamente o contacta al administrador."


def _connection_message(context: str) -> str:
    if context == "ldap":
        return "No se pudo conectar con el servicio de autenticación. Intenta nuevamente o contacta al administrador."
    if context == "hive":
        return "No se pudo conectar con Hive. Intenta nuevamente o contacta al administrador."
    if context in {"singlestore", "database", "catalogs", "audit"}:
        return "No se pudo conectar con la base de datos. Intenta nuevamente o contacta al administrador."
    return "No se pudo conectar con uno de los servicios requeridos. Intenta nuevamente o contacta al administrador."
