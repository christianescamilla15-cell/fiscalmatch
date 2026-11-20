"""Reenvío idempotente: el mismo CFDI (mismo UUID) cargado dos veces no duplica ni cambia nada.

El UUID del timbre es la llave fiscal del comprobante. Un reenvío —por reintento del
portal, doble clic o porque la agencia lo vuelve a subir— responde 409 y deja intactos el
CFDI, su desglose de reglas, su conciliación y la bitácora. Un CFDI corregido lleva otro
UUID (el original se cancela ante el SAT), así que un rechazado no se «rescata» reenviándolo.
"""
from __future__ import annotations

import threading

from sqlalchemy import func, select

from app.domain.models import Cfdi, Conciliacion, EventoAuditoria, ReporteVenta, Validacion

from apoyo import RFC_ALFA, cargar, nuevo_uuid, total_de, xml_cfdi

PERIODO = "2026-09-P1"


def _conteos(bd) -> dict[str, int]:
    bd.expire_all()
    contar = lambda modelo: bd.scalar(select(func.count()).select_from(modelo))  # noqa: E731
    return {
        "cfdis": contar(Cfdi),
        "validaciones": contar(Validacion),
        "conciliaciones": contar(Conciliacion),
        "cargas_auditadas": bd.scalar(select(func.count()).select_from(EventoAuditoria)
                                      .where(EventoAuditoria.accion == "CARGA_CFDI")),
    }


def _foto_conciliacion(bd) -> tuple:
    bd.expire_all()
    c = bd.scalars(select(Conciliacion)).one()
    return (c.id, c.cfdi_id, c.resultado, c.diferencia_centavos, c.motivo, c.metodo, c.conciliada_en)


def _con_reporte(bd, subtotal: int, desviacion: int = 0) -> None:
    bd.add(ReporteVenta(periodo=PERIODO, rfc_agencia=RFC_ALFA,
                        comision_centavos=total_de(subtotal) - desviacion))
    bd.commit()


def test_reenviar_el_mismo_cfdi_responde_409_y_no_cambia_nada(cliente, bd, escenario) -> None:
    _con_reporte(bd, 1_000_000, desviacion=30_000)
    xml = xml_cfdi(subtotal_centavos=1_000_000)
    primera = cargar(cliente, escenario.como("alfa"), xml)
    assert primera.status_code == 201
    antes, foto = _conteos(bd), _foto_conciliacion(bd)

    segunda = cargar(cliente, escenario.como("alfa"), xml, nombre="otra-vez.xml")

    assert segunda.status_code == 409
    assert "ya fue cargado" in segunda.json()["detail"]
    assert _conteos(bd) == antes
    assert antes == {"cfdis": 1, "validaciones": 11, "conciliaciones": 1, "cargas_auditadas": 1}
    assert _foto_conciliacion(bd) == foto


def test_reenviar_con_el_uuid_en_minusculas_es_el_mismo_cfdi(cliente, bd, escenario) -> None:
    """Antes se archivaba como un segundo CFDI (RECHAZADO por UUID_NO_DUPLICADO) en vez de 409."""
    _con_reporte(bd, 1_000_000)
    uuid = nuevo_uuid()
    assert cargar(cliente, escenario.como("alfa"), xml_cfdi(uuid=uuid)).status_code == 201

    en_minusculas = xml_cfdi(uuid=uuid.lower())
    assert f'UUID="{uuid.lower()}"' in en_minusculas

    reenvio = cargar(cliente, escenario.como("alfa"), en_minusculas)

    assert reenvio.status_code == 409
    assert _conteos(bd)["cfdis"] == 1
    guardado = bd.scalars(select(Cfdi.uuid)).one()
    assert guardado == uuid.upper()


def test_un_cfdi_rechazado_no_se_rescata_reenviandolo(cliente, bd, escenario) -> None:
    uuid = nuevo_uuid()
    rechazo = cargar(cliente, escenario.como("alfa"), xml_cfdi(uuid=uuid, rfc_receptor="XEXX010101000"))
    assert rechazo.status_code == 422
    assert rechazo.json()["detail"]["estado"] == "RECHAZADO"

    corregido = cargar(cliente, escenario.como("alfa"), xml_cfdi(uuid=uuid))

    assert corregido.status_code == 409
    bd.expire_all()
    assert [c.estado.value for c in bd.scalars(select(Cfdi))] == ["RECHAZADO"]
    assert _conteos(bd)["conciliaciones"] == 0


def test_reenvio_concurrente_del_mismo_uuid_deja_una_sola_fila(postgres, cliente, bd, escenario) -> None:
    """Dos cargas simultáneas: la llave única de PostgreSQL decide y la perdedora recibe 409."""
    _con_reporte(bd, 1_000_000)
    xml = xml_cfdi(subtotal_centavos=1_000_000)
    encabezados = escenario.como("alfa")
    codigos: list[int] = []
    salida = threading.Barrier(4)

    def subir() -> None:
        salida.wait()
        codigos.append(cargar(cliente, encabezados, xml).status_code)

    hilos = [threading.Thread(target=subir) for _ in range(4)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)

    assert sorted(codigos) == [201, 409, 409, 409]
    assert _conteos(bd) == {"cfdis": 1, "validaciones": 11, "conciliaciones": 1, "cargas_auditadas": 1}
