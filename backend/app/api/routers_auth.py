"""Autenticación: emisión de token y consulta de la identidad en curso."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import SesionBD, UsuarioActual, registrar_auditoria
from app.api.schemas import LoginIn, TokenOut, UsuarioOut
from app.core.config import get_settings
from app.core.seguridad import crear_token, verificar_password
from app.domain.models import Usuario

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut, summary="Iniciar sesión")
def login(datos: LoginIn, db: SesionBD) -> TokenOut:
    """Entrega un token si las credenciales son válidas.

    El mensaje de error es el mismo para usuario inexistente, contraseña
    incorrecta o cuenta dada de baja: distinguirlos permitiría enumerar
    cuentas válidas.
    """
    email = datos.email.strip().lower()
    usuario = db.scalar(select(Usuario).where(func.lower(Usuario.email) == email))

    credenciales_ok = usuario is not None and verificar_password(
        datos.password, usuario.password_hash
    )
    if not credenciales_ok or not usuario.activo:
        # El intento fallido también se audita: es señal de fuerza bruta.
        registrar_auditoria(
            db, email or "desconocido", "LOGIN_FALLIDO", "Usuario", getattr(usuario, "id", "-"),
            detalle="Credenciales inválidas o cuenta inactiva",
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    config = get_settings()
    token = crear_token(usuario.id, usuario.rol, usuario.agencia_id)

    registrar_auditoria(
        db, usuario, "LOGIN", "Usuario", usuario.id, detalle=f"Rol {usuario.rol.value}"
    )
    db.commit()

    return TokenOut(
        access_token=token,
        expira_en_minutos=config.jwt_expire_minutes,
        usuario=UsuarioOut.model_validate(usuario),
    )


@router.get("/yo", response_model=UsuarioOut, summary="Identidad en curso")
def yo(usuario: UsuarioActual) -> UsuarioOut:
    """Devuelve el usuario dueño del token, con su agencia si la tiene."""
    return UsuarioOut.model_validate(usuario)
