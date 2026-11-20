"""Bordes de fechas: corte de quincena, fin de mes y de año, y zona horaria de México.

Criterio (docs/reglas.md, sección «Periodo»): la Fecha del CFDI sin zona es hora local del
lugar de expedición (Anexo 20) y se toma tal cual; si declara zona, el instante se lleva a
America/Mexico_City antes de quedarse con el día. México (salvo la franja fronteriza) no
tiene horario de verano desde el 30-oct-2022: abril y agosto son UTC-6 igual que enero.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.domain.models import ReporteVenta
from app.services.conciliacion import periodo_de
from app.services.validacion import parsear_cfdi

from apoyo import RFC_ALFA, cargar, total_de, xml_cfdi


@pytest.mark.parametrize(
    ("fecha", "dia", "periodo"),
    [
        # Sin zona (lo normal): el día tal cual, sin conversiones.
        ("2026-08-15T23:59:59", date(2026, 8, 15), "2026-08-P1"),   # último segundo de P1
        ("2026-08-16T00:00:00", date(2026, 8, 16), "2026-08-P2"),   # primer segundo de P2
        ("2026-08-31T23:59:59", date(2026, 8, 31), "2026-08-P2"),
        ("2028-02-29T12:00:00", date(2028, 2, 29), "2028-02-P2"),   # bisiesto
        # Con zona: se convierte a hora de México antes de cortar la quincena.
        ("2026-08-16T05:59:59Z", date(2026, 8, 15), "2026-08-P1"),  # 23:59:59 del 15 en CDMX
        ("2026-08-16T06:00:00Z", date(2026, 8, 16), "2026-08-P2"),
        ("2026-08-16T00:30:00-05:00", date(2026, 8, 15), "2026-08-P1"),
        ("2026-08-01T05:00:00Z", date(2026, 7, 31), "2026-07-P2"),   # cambia de mes
        ("2027-01-01T05:59:59Z", date(2026, 12, 31), "2026-12-P2"),  # cambia de año
        # Sin horario de verano: en abril CDMX sigue en UTC-6 (con DST serían las 00:30 del 16).
        ("2026-04-16T05:30:00Z", date(2026, 4, 15), "2026-04-P1"),
    ],
)
def test_fecha_de_emision_y_quincena_en_hora_de_mexico(fecha: str, dia: date, periodo: str) -> None:
    parseado = parsear_cfdi(xml_cfdi(fecha=fecha))
    assert parseado.fecha_emision == dia
    assert periodo_de(parseado.fecha_emision) == periodo


@pytest.mark.parametrize(
    ("dia", "periodo"),
    [(date(2026, 9, 1), "2026-09-P1"), (date(2026, 9, 15), "2026-09-P1"),
     (date(2026, 9, 16), "2026-09-P2"), (date(2026, 9, 30), "2026-09-P2"),
     (date(2026, 2, 28), "2026-02-P2"), (date(2026, 12, 31), "2026-12-P2")],
)
def test_periodo_quincenal(dia: date, periodo: str) -> None:
    assert periodo_de(dia) == periodo


def test_cfdi_con_fecha_utc_concilia_contra_la_quincena_de_mexico(cliente, bd, escenario) -> None:
    """16-sep 05:30 UTC es 15-sep 23:30 en México: concilia contra el reporte de P1, no el de P2."""
    subtotal = 1_000_000
    bd.add_all([
        ReporteVenta(periodo="2026-09-P1", rfc_agencia=RFC_ALFA, comision_centavos=total_de(subtotal)),
        ReporteVenta(periodo="2026-09-P2", rfc_agencia=RFC_ALFA, comision_centavos=1),
    ])
    bd.commit()

    respuesta = cargar(cliente, escenario.como("alfa"),
                       xml_cfdi(subtotal_centavos=subtotal, fecha="2026-09-16T05:30:00Z"))

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["fecha_emision"] == "2026-09-15"
    assert cuerpo["periodo"] == "2026-09-P1"
    assert cuerpo["resultado"] == "MATCH"
