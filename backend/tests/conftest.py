"""Arnés común: base de datos de pruebas, cliente HTTP y un escenario con dos agencias.

Base de datos
-------------
* ``FM_TEST_DATABASE_URL`` definida → PostgreSQL REAL (el CI levanta ``postgres:16``).
* Sin ella → SQLite en un archivo temporal, para correr rápido en local.
* ``FM_EXIGIR_POSTGRES=1`` (lo pone el CI) → la suite FALLA si no hay PostgreSQL: una prueba
  de integración que se salta en silencio es un check verde que no probó nada.

La URL se escribe en ``FM_DATABASE_URL`` ANTES de importar ``app``: así el motor de la propia
aplicación (``app.core.db.engine``) apunta a la base de pruebas y jamás a la de desarrollo,
aunque quien corra la suite tenga su ``FM_DATABASE_URL`` exportada. La configuración de
negocio (RFC receptor, tolerancias) también se fija aquí para que el resultado no dependa
del entorno.
"""
from __future__ import annotations

import os
import secrets
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

URL_PRUEBAS = os.environ.get("FM_TEST_DATABASE_URL", "").strip()
EXIGIR_POSTGRES = os.environ.get("FM_EXIGIR_POSTGRES") == "1"

if not URL_PRUEBAS:
    _carpeta = Path(tempfile.mkdtemp(prefix="fiscalmatch-pruebas-"))
    URL_PRUEBAS_EFECTIVA = f"sqlite+pysqlite:///{_carpeta / 'pruebas.db'}"
else:
    URL_PRUEBAS_EFECTIVA = URL_PRUEBAS

os.environ["FM_DATABASE_URL"] = URL_PRUEBAS_EFECTIVA
os.environ["FM_RFC_RECEPTOR_ESPERADO"] = "FMA240101AB1"
os.environ["FM_TOLERANCIA_CENTAVOS"] = "100"
os.environ["FM_TOLERANCIA_PORCENTAJE"] = "0.005"
os.environ["FM_JWT_SECRET"] = "solo-para-pruebas-no-es-un-secreto"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.seguridad import crear_token, hash_password  # noqa: E402
from app.domain.models import Agencia, Base, Rol, Usuario  # noqa: E402
from app.main import app  # noqa: E402

from apoyo import RFC_ALFA, RFC_BETA  # noqa: E402

#: Contraseña de los usuarios de prueba: aleatoria en cada corrida, nunca un literal en el código.
PASSWORD = secrets.token_urlsafe(16)
#: PBKDF2 con 200 000 iteraciones tarda ~0.1 s: se calcula una sola vez para toda la suite.
_HASH = hash_password(PASSWORD)


def es_postgres(motor: Engine) -> bool:
    return motor.dialect.name == "postgresql"


if engine.dialect.name == "sqlite":
    # SQLite no aplica llaves foráneas salvo que se pida en cada conexión; PostgreSQL siempre.
    # Sin esto, una prueba que viola una FK pasa en local y falla en el CI.
    @event.listens_for(engine, "connect")
    def _activar_llaves_foraneas(conexion, _registro) -> None:
        cursor = conexion.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture(scope="session")
def motor() -> Iterator[Engine]:
    """Esquema creado UNA vez por corrida, desde cero, en la base de pruebas."""
    if EXIGIR_POSTGRES and not es_postgres(engine):
        pytest.fail(
            "FM_EXIGIR_POSTGRES=1 pero la suite corre sobre "
            f"{engine.dialect.name}: define FM_TEST_DATABASE_URL con un PostgreSQL real."
        )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def bd(motor: Engine) -> Iterator[Session]:
    """Sesión sobre tablas vacías: cada prueba arranca de cero."""
    with motor.begin() as conexion:
        for tabla in reversed(Base.metadata.sorted_tables):
            conexion.execute(tabla.delete())
    sesion = SessionLocal()
    try:
        yield sesion
    finally:
        sesion.close()


@pytest.fixture
def cliente(bd: Session) -> Iterator[TestClient]:
    """Cliente HTTP contra la app real (con su ciclo de vida) y la base de pruebas."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def postgres(motor: Engine) -> Engine:
    """Para las pruebas que sólo tienen sentido contra PostgreSQL (concurrencia, tipos).

    En local sin PostgreSQL se saltan con el motivo a la vista; en el CI no pueden
    saltarse: ``FM_EXIGIR_POSTGRES=1`` ya hizo fallar el fixture ``motor``.
    """
    if not es_postgres(motor):
        pytest.skip("requiere PostgreSQL real: define FM_TEST_DATABASE_URL (el CI lo exige)")
    return motor


@dataclass
class Escenario:
    """Dos agencias con su usuario, un analista, un administrador y dos casos raros."""

    agencia_alfa: Agencia
    agencia_beta: Agencia
    usuarios: dict[str, Usuario] = field(default_factory=dict)

    def token(self, clave: str) -> str:
        usuario = self.usuarios[clave]
        return crear_token(usuario.id, usuario.rol, usuario.agencia_id)

    def como(self, clave: str) -> dict[str, str]:
        """Encabezados HTTP del usuario `clave`."""
        return {"Authorization": f"Bearer {self.token(clave)}"}


@pytest.fixture
def escenario(bd: Session) -> Escenario:
    alfa = Agencia(rfc=RFC_ALFA, razon_social="AGENCIA ALFA DE PRUEBA SA DE CV")
    beta = Agencia(rfc=RFC_BETA, razon_social="AGENCIA BETA DE PRUEBA SA DE CV")
    bd.add_all([alfa, beta])
    bd.flush()

    def usuario(email: str, rol: Rol, agencia: Agencia | None = None, activo: bool = True) -> Usuario:
        return Usuario(
            email=email,
            nombre=email.split("@")[0],
            rol=rol,
            password_hash=_HASH,
            agencia_id=agencia.id if agencia else None,
            activo=activo,
        )

    usuarios = {
        "alfa": usuario("alfa@prueba.dev", Rol.AGENCIA, alfa),
        "beta": usuario("beta@prueba.dev", Rol.AGENCIA, beta),
        "analista": usuario("analista@prueba.dev", Rol.ANALISTA),
        "admin": usuario("admin@prueba.dev", Rol.ADMIN),
        # Rol AGENCIA sin agencia asignada: no debe ver ni cargar nada («jamás todo»).
        "huerfano": usuario("huerfano@prueba.dev", Rol.AGENCIA),
        "inactivo": usuario("inactivo@prueba.dev", Rol.ANALISTA, activo=False),
    }
    bd.add_all(usuarios.values())
    bd.commit()
    return Escenario(agencia_alfa=alfa, agencia_beta=beta, usuarios=usuarios)
