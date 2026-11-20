"""Carga, archivo del rechazo y consulta (docs/reglas.md, secciones «Carga» y «Operación»).

* Sólo se aceptan archivos .xml no vacíos e interpretables como CFDI (si no, 400 y no se archiva).
* Un CFDI que no pasa la validación fiscal SÍ se archiva (RECHAZADO) con su desglose regla por
  regla y su evento de bitácora: la evidencia de lo que no cuadró es parte del expediente.
* El XML original se devuelve byte por byte; el listado filtra y cuenta sin paginar.
* El tablero trae todas las llaves aunque valgan cero.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.domain.models import Cfdi, EventoAuditoria, ReporteVenta

from apoyo import RFC_ALFA, cargar, total_de, xml_cfdi


def _filas(bd) -> int:
    bd.expire_all()
    return bd.scalar(select(func.count()).select_from(Cfdi))


@pytest.mark.parametrize(
    ("nombre", "contenido", "fragmento"),
    [
        ("cfdi.pdf", xml_cfdi().encode(), "Sólo se aceptan archivos .xml"),
        ("vacio.xml", b"   \n", "vacío"),
        ("roto.xml", b"<cfdi:Comprobante", "XML no interpretable como CFDI"),
        ("otro.xml", b"<?xml version='1.0'?><Factura/>", "cfdi:Comprobante"),
    ],
)
def test_carga_de_un_archivo_inservible_responde_400_y_no_archiva(cliente, bd, escenario, nombre, contenido,
                                                                  fragmento) -> None:
    respuesta = cliente.post("/api/cfdis", files={"archivo": (nombre, contenido, "application/xml")},
                             headers=escenario.como("alfa"))
    assert respuesta.status_code == 400
    assert fragmento in respuesta.json()["detail"]
    assert _filas(bd) == 0


def test_un_cfdi_rechazado_se_archiva_con_su_desglose_y_su_bitacora(cliente, bd, escenario) -> None:
    respuesta = cargar(cliente, escenario.como("alfa"), xml_cfdi(moneda="USD", version="3.3"))

    assert respuesta.status_code == 422
    detalle = respuesta.json()["detail"]
    assert detalle["estado"] == "RECHAZADO"
    assert sorted(r["regla"] for r in detalle["reglas_fallidas"]) == ["ESTRUCTURA_CFDI40", "MONEDA_MXN"]
    archivado = cliente.get(f"/api/cfdis/{detalle['cfdi_id']}", headers=escenario.como("alfa")).json()
    assert archivado["estado"] == "RECHAZADO" and archivado["conciliacion"] is None
    assert len(archivado["validaciones"]) == 11
    reprobadas = {v["regla"] for v in archivado["validaciones"] if not v["aprobada"]}
    assert reprobadas == {"ESTRUCTURA_CFDI40", "MONEDA_MXN"}
    bd.expire_all()
    evento = bd.scalars(select(EventoAuditoria).where(EventoAuditoria.accion == "CARGA_CFDI")).one()
    assert "rechazado" in evento.detalle and evento.actor == "alfa@prueba.dev"


def test_el_xml_original_se_devuelve_byte_por_byte(cliente, escenario) -> None:
    xml = xml_cfdi()
    cfdi = cargar(cliente, escenario.como("alfa"), xml, nombre="A-1.xml").json()

    respuesta = cliente.get(f"/api/cfdis/{cfdi['id']}/xml", headers=escenario.como("alfa"))

    assert respuesta.status_code == 200
    assert respuesta.text == xml
    assert respuesta.headers["content-type"].startswith("application/xml")
    assert 'filename="A-1.xml"' in respuesta.headers["content-disposition"]


def test_un_xml_en_latin1_se_acepta(cliente, escenario) -> None:
    """Hay sistemas de facturación que aún guardan en Latin-1: los acentos no deben tumbar la carga."""
    xml = xml_cfdi().replace('encoding="UTF-8"', 'encoding="ISO-8859-1"')
    contenido = xml.encode("latin-1")
    assert "ó".encode("latin-1") in contenido              # de verdad no es UTF-8

    respuesta = cliente.post("/api/cfdis", files={"archivo": ("latin1.xml", contenido, "application/xml")},
                             headers=escenario.como("alfa"))

    assert respuesta.status_code == 201, respuesta.text


def test_el_listado_filtra_por_estado_periodo_y_resultado(cliente, bd, escenario) -> None:
    bd.add(ReporteVenta(periodo="2026-09-P1", rfc_agencia=RFC_ALFA, comision_centavos=total_de(1_000_000)))
    bd.commit()
    como = escenario.como("analista")
    match = cargar(cliente, como, xml_cfdi(subtotal_centavos=1_000_000)).json()
    sin_reporte = cargar(cliente, como, xml_cfdi(fecha="2026-09-20T10:00:00")).json()
    cargar(cliente, como, xml_cfdi(rfc_receptor="XEXX010101000"))           # rechazado

    def ids(consulta: str) -> tuple[list[int], str]:
        r = cliente.get(f"/api/cfdis?{consulta}", headers=como)
        return sorted(c["id"] for c in r.json()), r.headers["X-Total-Count"]

    assert ids("estado=CONCILIADO") == ([match["id"]], "1")
    assert ids("periodo=2026-09-P2") == ([sin_reporte["id"]], "1")
    assert ids("resultado=NO_MATCH") == ([sin_reporte["id"]], "1")
    assert ids("estado=RECHAZADO")[1] == "1"
    assert ids("limit=1")[1] == "3"                         # el conteo no se pagina


def test_el_tablero_trae_todas_las_llaves_aunque_valgan_cero(cliente, escenario) -> None:
    resumen = cliente.get("/api/resumen?periodo=2026-09-P1", headers=escenario.como("analista")).json()
    assert resumen["por_estado"] == dict.fromkeys(
        ["RECIBIDO", "VALIDADO", "RECHAZADO", "CONCILIADO", "EN_EXCEPCION"], 0)
    assert resumen["por_resultado"] == {"MATCH": 0, "PARCIAL": 0, "NO_MATCH": 0}
    assert resumen["total_facturado_centavos"] == 0 and resumen["periodo"] == "2026-09-P1"
