"""Máquina de estados del CFDI: transiciones válidas e INVÁLIDAS (docs/reglas.md, «Estados»).

    carga ──▶ VALIDADO ──conciliar──▶ CONCILIADO (MATCH) | EN_EXCEPCION (PARCIAL, NO_MATCH)
         └──▶ RECHAZADO (terminal)
    EN_EXCEPCION ──resolver(aceptar)──▶ CONCILIADO · ──resolver(no aceptar)──▶ EN_EXCEPCION

Lo inválido se prueba en el servicio (lanza `TransicionInvalida` sin tocar nada) y en el
endpoint (409, sin cambios y sin evento de auditoría).
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.domain.models import (
    Cfdi,
    Conciliacion,
    EstadoCfdi,
    EventoAuditoria,
    MetodoMatch,
    ReporteVenta,
    ResultadoMatch,
)
from app.services.conciliacion import (
    ORIGENES_PERMITIDOS,
    TransicionInvalida,
    conciliar_cfdi,
    exigir_estado,
    resolver_manual,
)

from apoyo import RFC_ALFA, cargar, total_de, xml_cfdi

E = EstadoCfdi
TOL = {"tolerancia_centavos": 100, "tolerancia_porcentaje": 0.005}
PERIODO = "2026-09-P1"


# --------------------------------------------------------------------------- #
# Tabla de transiciones (sin base de datos)
# --------------------------------------------------------------------------- #


def test_cada_operacion_declara_estados_de_origen_existentes() -> None:
    assert set(ORIGENES_PERMITIDOS) == {"conciliar", "resolver"}
    for origenes in ORIGENES_PERMITIDOS.values():
        assert origenes and origenes <= set(EstadoCfdi)


@pytest.mark.parametrize(
    ("operacion", "estado", "permitida"),
    [
        ("conciliar", E.VALIDADO, True),
        ("conciliar", E.CONCILIADO, True),     # re-conciliación del motor
        ("conciliar", E.EN_EXCEPCION, True),   # p. ej. llegó tarde el reporte de venta
        ("conciliar", E.RECHAZADO, False),     # terminal: no pasó la validación fiscal
        ("conciliar", E.RECIBIDO, False),      # aún sin dictamen fiscal
        ("resolver", E.EN_EXCEPCION, True),
        ("resolver", E.CONCILIADO, False),     # no se «des-concilia» a mano
        ("resolver", E.VALIDADO, False),
        ("resolver", E.RECHAZADO, False),
        ("resolver", E.RECIBIDO, False),
    ],
)
def test_transiciones_por_operacion(operacion: str, estado: EstadoCfdi, permitida: bool) -> None:
    cfdi = Cfdi(uuid="X", estado=estado)
    if permitida:
        exigir_estado(cfdi, operacion)
    else:
        with pytest.raises(TransicionInvalida, match=estado.value):
            exigir_estado(cfdi, operacion)


# --------------------------------------------------------------------------- #
# Servicio sobre la base de datos
# --------------------------------------------------------------------------- #


def _cfdi(bd, escenario, *, estado: EstadoCfdi, total: int = 1_160_000, uuid: str = "U-1") -> Cfdi:
    cfdi = Cfdi(
        uuid=uuid,
        rfc_emisor=RFC_ALFA,
        rfc_receptor="FMA240101AB1",
        fecha_emision=date(2026, 9, 3),
        periodo=PERIODO,
        subtotal_centavos=1_000_000,
        impuestos_centavos=total - 1_000_000,
        total_centavos=total,
        xml_original="<cfdi/>",
        nombre_archivo="x.xml",
        estado=estado,
        agencia_id=escenario.agencia_alfa.id,
    )
    bd.add(cfdi)
    bd.flush()
    return cfdi


def _reporte(bd, comision: int) -> ReporteVenta:
    reporte = ReporteVenta(periodo=PERIODO, rfc_agencia=RFC_ALFA, comision_centavos=comision)
    bd.add(reporte)
    bd.flush()
    return reporte


@pytest.mark.parametrize("estado", [E.RECHAZADO, E.RECIBIDO])
def test_conciliar_sin_validacion_aprobada_es_transicion_invalida(bd, escenario, estado) -> None:
    """Antes, un CFDI RECHAZADO con reporte que cuadraba terminaba CONCILIADO."""
    cfdi = _cfdi(bd, escenario, estado=estado)
    _reporte(bd, cfdi.total_centavos)

    with pytest.raises(TransicionInvalida):
        conciliar_cfdi(bd, cfdi, **TOL)

    assert cfdi.estado is estado
    assert bd.scalar(select(func.count()).select_from(Conciliacion)) == 0


@pytest.mark.parametrize(
    ("comision", "estado_final", "resultado"),
    [
        (1_160_000, E.CONCILIADO, ResultadoMatch.MATCH),
        (1_000_000, E.EN_EXCEPCION, ResultadoMatch.PARCIAL),
        (None, E.EN_EXCEPCION, ResultadoMatch.NO_MATCH),
    ],
)
def test_validado_se_dictamina_segun_el_veredicto(bd, escenario, comision, estado_final, resultado) -> None:
    cfdi = _cfdi(bd, escenario, estado=E.VALIDADO)
    if comision is not None:
        _reporte(bd, comision)

    conciliacion = conciliar_cfdi(bd, cfdi, **TOL)

    assert conciliacion.resultado is resultado
    assert cfdi.estado is estado_final
    assert conciliacion.metodo is MetodoMatch.AUTOMATICO


def test_reconciliar_cuando_llega_el_reporte_limpia_la_resolucion_manual(bd, escenario) -> None:
    """EN_EXCEPCION → CONCILIADO por el motor: el veredicto vuelve a ser automático."""
    cfdi = _cfdi(bd, escenario, estado=E.VALIDADO)
    conciliacion = conciliar_cfdi(bd, cfdi, **TOL)                  # sin reporte: NO_MATCH
    analista = escenario.usuarios["analista"].id
    resolver_manual(bd, conciliacion, usuario_id=analista, nota="espera reporte", aceptar=False)
    assert cfdi.estado is E.EN_EXCEPCION and conciliacion.metodo is MetodoMatch.MANUAL

    _reporte(bd, cfdi.total_centavos)
    otra = conciliar_cfdi(bd, cfdi, **TOL)

    assert otra.id == conciliacion.id                               # reemplaza, no duplica
    assert otra.resultado is ResultadoMatch.MATCH
    assert otra.metodo is MetodoMatch.AUTOMATICO
    assert otra.resuelta_por_id is None and otra.nota_resolucion is None
    assert cfdi.estado is E.CONCILIADO


def test_resolver_un_cfdi_conciliado_es_transicion_invalida(bd, escenario) -> None:
    """Antes, «no aceptar» sobre un MATCH lo regresaba a la cola con resultado MATCH."""
    cfdi = _cfdi(bd, escenario, estado=E.VALIDADO)
    _reporte(bd, cfdi.total_centavos)
    conciliacion = conciliar_cfdi(bd, cfdi, **TOL)

    with pytest.raises(TransicionInvalida):
        resolver_manual(bd, conciliacion, usuario_id=escenario.usuarios["analista"].id,
                        nota="no", aceptar=False)

    assert cfdi.estado is E.CONCILIADO
    assert conciliacion.metodo is MetodoMatch.AUTOMATICO
    assert conciliacion.resuelta_por_id is None


# --------------------------------------------------------------------------- #
# Endpoint del analista
# --------------------------------------------------------------------------- #


def _cargar_con_reporte(cliente, bd, escenario, *, desviacion: int) -> dict:
    """Carga un CFDI de Alfa cuyo reporte de venta difiere `desviacion` centavos."""
    subtotal = 1_000_000
    bd.add(ReporteVenta(periodo=PERIODO, rfc_agencia=RFC_ALFA,
                        comision_centavos=total_de(subtotal) - desviacion))
    bd.commit()
    respuesta = cargar(cliente, escenario.como("alfa"), xml_cfdi(subtotal_centavos=subtotal))
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _resolver(cliente, escenario, conciliacion_id: int, aceptar: bool, quien: str = "analista"):
    return cliente.post(
        f"/api/conciliaciones/{conciliacion_id}/resolver",
        json={"aceptar": aceptar, "nota": "revisado contra contrato"},
        headers=escenario.como(quien),
    )


def _eventos(bd, accion: str) -> int:
    bd.expire_all()
    consulta = select(func.count()).select_from(EventoAuditoria).where(EventoAuditoria.accion == accion)
    return bd.scalar(consulta)


def test_resolver_aceptando_cierra_la_excepcion(cliente, bd, escenario) -> None:
    cfdi = _cargar_con_reporte(cliente, bd, escenario, desviacion=50_000)
    assert cfdi["estado"] == "EN_EXCEPCION" and cfdi["resultado"] == "PARCIAL"

    respuesta = _resolver(cliente, escenario, cfdi["conciliacion"]["id"], aceptar=True)

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["resultado"] == "MATCH" and cuerpo["metodo"] == "MANUAL"
    assert cuerpo["resuelta_por_id"] == escenario.usuarios["analista"].id
    detalle = cliente.get(f"/api/cfdis/{cfdi['id']}", headers=escenario.como("analista")).json()
    assert detalle["estado"] == "CONCILIADO"
    assert _eventos(bd, "RESOLUCION_MANUAL") == 1


def test_resolver_sin_aceptar_deja_el_cfdi_en_la_cola(cliente, bd, escenario) -> None:
    cfdi = _cargar_con_reporte(cliente, bd, escenario, desviacion=50_000)

    respuesta = _resolver(cliente, escenario, cfdi["conciliacion"]["id"], aceptar=False)

    assert respuesta.status_code == 200
    assert respuesta.json()["resultado"] == "PARCIAL"                  # respeta al motor
    assert respuesta.json()["metodo"] == "MANUAL"
    cola = cliente.get("/api/excepciones", headers=escenario.como("analista")).json()
    assert [c["id"] for c in cola] == [cfdi["id"]]


def test_resolver_un_cfdi_conciliado_responde_409_sin_cambios_ni_auditoria(cliente, bd, escenario) -> None:
    cfdi = _cargar_con_reporte(cliente, bd, escenario, desviacion=0)
    assert cfdi["estado"] == "CONCILIADO"

    respuesta = _resolver(cliente, escenario, cfdi["conciliacion"]["id"], aceptar=False)

    assert respuesta.status_code == 409
    assert "CONCILIADO" in respuesta.json()["detail"]
    detalle = cliente.get(f"/api/cfdis/{cfdi['id']}", headers=escenario.como("analista")).json()
    assert detalle["estado"] == "CONCILIADO"
    assert detalle["conciliacion"]["metodo"] == "AUTOMATICO"
    assert _eventos(bd, "RESOLUCION_MANUAL") == 0


def test_una_excepcion_ya_aceptada_no_se_reabre(cliente, bd, escenario) -> None:
    cfdi = _cargar_con_reporte(cliente, bd, escenario, desviacion=50_000)
    conciliacion_id = cfdi["conciliacion"]["id"]
    assert _resolver(cliente, escenario, conciliacion_id, aceptar=True).status_code == 200

    segunda = _resolver(cliente, escenario, conciliacion_id, aceptar=False)

    assert segunda.status_code == 409
    assert _eventos(bd, "RESOLUCION_MANUAL") == 1


def test_resolver_una_conciliacion_inexistente_responde_404(cliente, escenario) -> None:
    assert _resolver(cliente, escenario, 999_999, aceptar=True).status_code == 404
