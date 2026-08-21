"""Seguridad: derivación de contraseñas y emisión/verificación de JWT.

No se usa ninguna librería externa de criptografía: basta la biblioteca estándar.

* Contraseñas → PBKDF2-HMAC-SHA256 con salt aleatorio por usuario.
  Formato almacenado: ``pbkdf2_sha256$<iteraciones>$<salt_hex>$<hash_hex>``.
* Tokens → JWS compacto (``header.payload.firma``) firmado con HMAC y
  codificado en base64url sin relleno, igual que un JWT convencional.

Toda comparación de material sensible pasa por :func:`hmac.compare_digest`
para no filtrar información por tiempo de respuesta.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from app.core.config import get_settings

# --------------------------------------------------------------------------- #
# Contraseñas
# --------------------------------------------------------------------------- #

_ETIQUETA_ALGORITMO: Final[str] = "pbkdf2_sha256"
_ITERACIONES: Final[int] = 200_000
_BYTES_SALT: Final[int] = 16
_BYTES_DERIVADOS: Final[int] = 32


def _derivar(password: str, salt: bytes, iteraciones: int, longitud: int) -> bytes:
    """Deriva la contraseña con PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iteraciones, dklen=longitud
    )


def hash_password(password: str) -> str:
    """Devuelve el hash almacenable de `password`, con salt nuevo cada vez."""
    salt = secrets.token_bytes(_BYTES_SALT)
    derivado = _derivar(password, salt, _ITERACIONES, _BYTES_DERIVADOS)
    return f"{_ETIQUETA_ALGORITMO}${_ITERACIONES}${salt.hex()}${derivado.hex()}"


def verificar_password(password: str, hash_guardado: str) -> bool:
    """Indica si `password` corresponde a `hash_guardado`.

    Nunca lanza: un hash corrupto, vacío o con otro algoritmo simplemente
    no verifica.
    """
    if not password or not hash_guardado:
        return False
    partes = hash_guardado.split("$")
    if len(partes) != 4:
        return False
    etiqueta, iteraciones_txt, salt_hex, hash_hex = partes
    if etiqueta != _ETIQUETA_ALGORITMO:
        return False
    try:
        iteraciones = int(iteraciones_txt)
        salt = bytes.fromhex(salt_hex)
        esperado = bytes.fromhex(hash_hex)
    except (ValueError, binascii.Error):
        return False
    if iteraciones <= 0 or not esperado:
        return False
    derivado = _derivar(password, salt, iteraciones, len(esperado))
    return hmac.compare_digest(derivado, esperado)


# --------------------------------------------------------------------------- #
# JWT firmado a mano (HMAC + base64url)
# --------------------------------------------------------------------------- #

_HASHES_JWT: Final[dict[str, Any]] = {
    "HS256": hashlib.sha256,
    "HS384": hashlib.sha384,
    "HS512": hashlib.sha512,
}


def _b64url(datos: bytes) -> str:
    """base64url sin relleno, tal como exige el formato JWT."""
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def _des_b64url(texto: str) -> bytes:
    """Inverso de :func:`_b64url`; repone el relleno que el formato omite."""
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _serializar(objeto: dict[str, Any]) -> str:
    """JSON compacto y determinista (misma entrada ⇒ misma firma)."""
    crudo = json.dumps(objeto, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return _b64url(crudo.encode("utf-8"))


def _funcion_hash(algoritmo: str) -> Any:
    try:
        return _HASHES_JWT[algoritmo]
    except KeyError:
        raise ValueError(f"Algoritmo de firma no soportado: {algoritmo}") from None


def _firmar(mensaje: str, secreto: str, algoritmo: str) -> bytes:
    return hmac.new(
        secreto.encode("utf-8"), mensaje.encode("ascii"), _funcion_hash(algoritmo)
    ).digest()


def crear_token(
    sub: int | str,
    rol: Any,
    agencia_id: int | None = None,
    *,
    minutos: int | None = None,
) -> str:
    """Emite un token firmado para el usuario `sub`.

    `rol` acepta el enum :class:`app.domain.models.Rol` o su valor en texto.
    `agencia_id` viaja en el token para que el front sepa de inmediato si el
    usuario está acotado a una agencia (la autorización real se recalcula
    siempre contra la base de datos).
    """
    config = get_settings()
    algoritmo = config.jwt_algorithm
    _funcion_hash(algoritmo)  # falla temprano si la configuración es inválida

    ahora = datetime.now(timezone.utc)
    vigencia = minutos if minutos is not None else config.jwt_expire_minutes

    encabezado = {"alg": algoritmo, "typ": "JWT"}
    cuerpo: dict[str, Any] = {
        "sub": str(sub),
        "rol": getattr(rol, "value", rol),
        "agencia_id": agencia_id,
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(minutes=vigencia)).timestamp()),
    }

    mensaje = f"{_serializar(encabezado)}.{_serializar(cuerpo)}"
    firma = _b64url(_firmar(mensaje, config.jwt_secret, algoritmo))
    return f"{mensaje}.{firma}"


def decodificar_token(token: str) -> dict[str, Any]:
    """Valida firma y vigencia de `token` y devuelve su cuerpo.

    Lanza :class:`ValueError` ante cualquier anomalía —formato, algoritmo,
    firma o expiración— sin revelar cuál, para no dar pistas a quien sondea.
    """
    if not token:
        raise ValueError("Token vacío")

    partes = token.split(".")
    if len(partes) != 3:
        raise ValueError("Token mal formado")
    encabezado_b64, cuerpo_b64, firma_b64 = partes

    try:
        encabezado = json.loads(_des_b64url(encabezado_b64))
        cuerpo = json.loads(_des_b64url(cuerpo_b64))
        firma = _des_b64url(firma_b64)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("Token ilegible") from exc

    if not isinstance(encabezado, dict) or not isinstance(cuerpo, dict):
        raise ValueError("Token ilegible")

    config = get_settings()
    # El algoritmo lo fija el servidor: jamás se acepta el que declare el token
    # (ese es el clásico ataque `alg: none`).
    if encabezado.get("alg") != config.jwt_algorithm:
        raise ValueError("Algoritmo de firma no permitido")

    esperada = _firmar(
        f"{encabezado_b64}.{cuerpo_b64}", config.jwt_secret, config.jwt_algorithm
    )
    if not hmac.compare_digest(firma, esperada):
        raise ValueError("Firma inválida")

    expira = cuerpo.get("exp")
    if not isinstance(expira, (int, float)) or isinstance(expira, bool):
        raise ValueError("Token sin expiración válida")
    if datetime.now(timezone.utc).timestamp() >= expira:
        raise ValueError("Token expirado")

    return cuerpo
