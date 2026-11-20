"""Autorización por rol y frontera por agencia (docs/reglas.md, sección «Acceso»).

* AGENCIA: carga y ve SÓLO lo de su agencia; sin agencia asignada no ve nada (jamás «todo»);
  nunca entra a la operación (excepciones, resolución, tablero, exportación, bitácora).
* ANALISTA: ve todo, carga a nombre de una agencia del padrón y resuelve excepciones.
* ADMIN: opera como el analista, pero no carga comprobantes.
* La identidad se relee de la base en cada petición: una baja o un cambio de rol surte efecto
  sin esperar a que el token caduque.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.seguridad import crear_token
from app.domain.models import Cfdi, EventoAuditoria, Rol, Usuario

from apoyo import RFC_ALFA, RFC_BETA, cargar, xml_cfdi
from conftest import PASSWORD


def _cfdi_de(cliente, escenario, quien: str, rfc: str) -> dict:
    respuesta = cargar(cliente, escenario.como(quien), xml_cfdi(rfc_emisor=rfc))
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _filas(bd) -> int:
    bd.expire_all()
    return bd.scalar(select(func.count()).select_from(Cfdi))


# --------------------------------------------------------------------------- #
# Frontera por agencia
# --------------------------------------------------------------------------- #


def test_agencia_carga_sus_propios_cfdis(cliente, escenario) -> None:
    cfdi = _cfdi_de(cliente, escenario, "alfa", RFC_ALFA)
    assert cfdi["agencia_id"] == escenario.agencia_alfa.id
    assert cfdi["cargado_por_id"] == escenario.usuarios["alfa"].id


def test_agencia_no_carga_cfdis_emitidos_por_otra(cliente, bd, escenario) -> None:
    respuesta = cargar(cliente, escenario.como("alfa"), xml_cfdi(rfc_emisor=RFC_BETA))
    assert respuesta.status_code == 403
    assert _filas(bd) == 0


def test_agencia_solo_lista_lo_suyo(cliente, escenario) -> None:
    propio = _cfdi_de(cliente, escenario, "alfa", RFC_ALFA)
    _cfdi_de(cliente, escenario, "beta", RFC_BETA)

    respuesta = cliente.get("/api/cfdis", headers=escenario.como("alfa"))

    assert [c["id"] for c in respuesta.json()] == [propio["id"]]
    assert respuesta.headers["X-Total-Count"] == "1"


@pytest.mark.parametrize("sufijo", ["", "/xml"])
def test_agencia_no_ve_el_detalle_ni_el_xml_de_otra(cliente, escenario, sufijo: str) -> None:
    ajeno = _cfdi_de(cliente, escenario, "beta", RFC_BETA)

    respuesta = cliente.get(f"/api/cfdis/{ajeno['id']}{sufijo}", headers=escenario.como("alfa"))

    assert respuesta.status_code == 403
    assert RFC_BETA not in respuesta.text


def test_agencia_sin_agencia_asignada_no_ve_ni_carga_nada(cliente, bd, escenario) -> None:
    _cfdi_de(cliente, escenario, "alfa", RFC_ALFA)

    listado = cliente.get("/api/cfdis", headers=escenario.como("huerfano"))
    carga = cargar(cliente, escenario.como("huerfano"), xml_cfdi(rfc_emisor=RFC_ALFA))

    assert listado.json() == [] and listado.headers["X-Total-Count"] == "0"
    assert carga.status_code == 403
    assert _filas(bd) == 1


OPERACION = [
    ("get", "/api/excepciones"),
    ("get", "/api/resumen"),
    ("get", "/api/exportar.csv"),
    ("get", "/api/auditoria"),
    ("post", "/api/conciliaciones/1/resolver"),
]


@pytest.mark.parametrize(("metodo", "ruta"), OPERACION)
def test_agencia_no_entra_a_la_operacion(cliente, escenario, metodo: str, ruta: str) -> None:
    kwargs = {"json": {"aceptar": True, "nota": "x"}} if metodo == "post" else {}
    respuesta = getattr(cliente, metodo)(ruta, headers=escenario.como("alfa"), **kwargs)
    assert respuesta.status_code == 403


# --------------------------------------------------------------------------- #
# Analista y administrador
# --------------------------------------------------------------------------- #


def test_analista_ve_todas_las_agencias(cliente, escenario) -> None:
    ids = {
        _cfdi_de(cliente, escenario, "alfa", RFC_ALFA)["id"],
        _cfdi_de(cliente, escenario, "beta", RFC_BETA)["id"],
    }

    respuesta = cliente.get("/api/cfdis", headers=escenario.como("analista"))

    assert {c["id"] for c in respuesta.json()} == ids
    ajeno = cliente.get(f"/api/cfdis/{min(ids)}/xml", headers=escenario.como("analista"))
    assert ajeno.status_code == 200 and ajeno.text.startswith("<?xml")


def test_analista_carga_a_nombre_de_una_agencia_del_padron(cliente, escenario) -> None:
    cfdi = _cfdi_de(cliente, escenario, "analista", RFC_BETA)
    assert cfdi["agencia_id"] == escenario.agencia_beta.id


def test_analista_no_carga_cfdis_de_un_emisor_fuera_del_padron(cliente, bd, escenario) -> None:
    respuesta = cargar(cliente, escenario.como("analista"), xml_cfdi(rfc_emisor="ZZZ991231ZZ9"))
    assert respuesta.status_code == 422
    assert respuesta.json()["detail"]["reglas_fallidas"][0]["regla"] == "EMISOR_REGISTRADO"
    assert _filas(bd) == 0                       # sin agencia no hay dónde archivarlo


@pytest.mark.parametrize(("metodo", "ruta"), OPERACION[:4])
def test_analista_y_admin_operan(cliente, escenario, metodo: str, ruta: str) -> None:
    for quien in ("analista", "admin"):
        assert getattr(cliente, metodo)(ruta, headers=escenario.como(quien)).status_code == 200


def test_admin_no_carga_comprobantes(cliente, bd, escenario) -> None:
    respuesta = cargar(cliente, escenario.como("admin"), xml_cfdi(rfc_emisor=RFC_ALFA))
    assert respuesta.status_code == 403
    assert _filas(bd) == 0


# --------------------------------------------------------------------------- #
# Identidad
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "encabezado",
    [None, "Bearer", "Basic dXNlcjpwYXNz", "Bearer no.es.token"],
)
def test_sin_token_valido_responde_401(cliente, escenario, encabezado) -> None:
    headers = {"Authorization": encabezado} if encabezado else {}
    assert cliente.get("/api/cfdis", headers=headers).status_code == 401


def test_token_con_firma_alterada_responde_401(cliente, escenario) -> None:
    encabezado, cuerpo, firma = escenario.token("alfa").split(".")
    alterado = f"{encabezado}.{cuerpo}.{firma[:-2]}{'AA' if firma[-2:] != 'AA' else 'BB'}"
    respuesta = cliente.get("/api/cfdis", headers={"Authorization": f"Bearer {alterado}"})
    assert respuesta.status_code == 401


def test_token_expirado_responde_401(cliente, escenario) -> None:
    usuario = escenario.usuarios["analista"]
    vencido = crear_token(usuario.id, usuario.rol, None, minutos=-1)
    assert cliente.get("/api/cfdis", headers={"Authorization": f"Bearer {vencido}"}).status_code == 401


def test_usuario_inactivo_responde_401_aunque_su_token_sea_valido(cliente, escenario) -> None:
    assert cliente.get("/api/cfdis", headers=escenario.como("inactivo")).status_code == 401


def test_el_rol_se_relee_de_la_base_en_cada_peticion(cliente, bd, escenario) -> None:
    token = escenario.como("analista")                  # emitido cuando era ANALISTA
    assert cliente.get("/api/resumen", headers=token).status_code == 200

    usuario = bd.get(Usuario, escenario.usuarios["analista"].id)
    usuario.rol = Rol.AGENCIA
    bd.commit()

    assert cliente.get("/api/resumen", headers=token).status_code == 403


def test_login_correcto_entrega_un_token_que_sirve(cliente, escenario) -> None:
    respuesta = cliente.post("/api/auth/login", json={"email": "ALFA@prueba.dev ", "password": PASSWORD})

    assert respuesta.status_code == 200
    token = respuesta.json()["access_token"]
    yo = cliente.get("/api/auth/yo", headers={"Authorization": f"Bearer {token}"})
    assert yo.json()["email"] == "alfa@prueba.dev" and yo.json()["rol"] == "AGENCIA"


def test_login_fallido_no_distingue_la_causa_y_queda_en_bitacora(cliente, bd, escenario) -> None:
    clave_mala = cliente.post("/api/auth/login", json={"email": "alfa@prueba.dev", "password": "otra"})
    inexistente = cliente.post("/api/auth/login", json={"email": "nadie@prueba.dev", "password": "otra"})
    inactivo = cliente.post("/api/auth/login", json={"email": "inactivo@prueba.dev", "password": PASSWORD})

    for respuesta in (clave_mala, inexistente, inactivo):
        assert respuesta.status_code == 401
        assert respuesta.json() == clave_mala.json()
    bd.expire_all()
    fallidos = bd.scalar(select(func.count()).select_from(EventoAuditoria)
                         .where(EventoAuditoria.accion == "LOGIN_FALLIDO"))
    assert fallidos == 3
