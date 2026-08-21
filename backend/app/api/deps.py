"""Dependencias compartidas por los routers: identidad, autorización y bitácora."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.seguridad import decodificar_token
from app.domain.models import EventoAuditoria, Rol, Usuario

SesionBD = Annotated[Session, Depends(get_db)]


# --------------------------------------------------------------------------- #
# Identidad
# --------------------------------------------------------------------------- #


def _no_autorizado(detalle: str = "Credenciales inválidas o token expirado") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detalle,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_usuario_actual(
    db: SesionBD,
    authorization: Annotated[str | None, Header()] = None,
) -> Usuario:
    """Resuelve el usuario dueño del `Authorization: Bearer <token>`.

    El token sólo aporta el identificador: el rol y el estado se releen de la
    base de datos, de modo que una baja o un cambio de rol surte efecto sin
    esperar a que el token caduque.
    """
    if not authorization:
        raise _no_autorizado("Falta el encabezado Authorization")

    esquema, _, token = authorization.partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        raise _no_autorizado("El encabezado Authorization debe ser de tipo Bearer")

    try:
        cuerpo = decodificar_token(token.strip())
        usuario_id = int(cuerpo["sub"])
    except (ValueError, KeyError, TypeError):
        raise _no_autorizado() from None

    usuario = db.get(Usuario, usuario_id)
    if usuario is None or not usuario.activo:
        raise _no_autorizado("El usuario no existe o está inactivo")
    return usuario


UsuarioActual = Annotated[Usuario, Depends(get_usuario_actual)]


def requiere_rol(*roles: Rol) -> Callable[[Usuario], Usuario]:
    """Construye una dependencia que exige alguno de `roles`.

    Uso: ``usuario: Usuario = Depends(requiere_rol(Rol.ANALISTA, Rol.ADMIN))``.
    """
    permitidos = frozenset(roles)

    def _verificar(usuario: UsuarioActual) -> Usuario:
        if usuario.rol not in permitidos:
            esperados = ", ".join(sorted(rol.value for rol in permitidos))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operación reservada a los roles: {esperados}",
            )
        return usuario

    return _verificar


def es_de_otra_agencia(usuario: Usuario, agencia_id: int | None) -> bool:
    """`True` si el usuario está acotado a una agencia distinta de `agencia_id`.

    Analistas y administradores ven todo, así que para ellos siempre es `False`.
    """
    if usuario.rol is not Rol.AGENCIA:
        return False
    return usuario.agencia_id is None or usuario.agencia_id != agencia_id


# --------------------------------------------------------------------------- #
# Bitácora de auditoría
# --------------------------------------------------------------------------- #


def registrar_auditoria(
    db: Session,
    actor: Usuario | str,
    accion: str,
    entidad: str,
    entidad_id: Any,
    detalle: str = "",
) -> EventoAuditoria:
    """Deja constancia de una operación en la bitácora.

    Sólo hace `flush`: el `commit` es de quien orquesta la transacción, para que
    el evento y el cambio que describe viajen juntos o no viajen.
    """
    evento = EventoAuditoria(
        actor=actor.email if isinstance(actor, Usuario) else str(actor),
        accion=accion,
        entidad=entidad,
        entidad_id=str(entidad_id),
        detalle=detalle,
    )
    db.add(evento)
    db.flush()
    return evento
