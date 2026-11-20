"""Bordes de importes: tolerancias inclusivas, umbral en Decimal truncado e importes > INT32.

Criterio documentado en docs/reglas.md (sección «Importes y redondeo»):

* Importes del XML → centavos con `Decimal` y ROUND_HALF_UP, cada atributo por separado.
* Umbral = max(tolerancia absoluta, comisión × porcentaje), con el porcentaje en `Decimal`
  y el producto TRUNCADO a centavos (ROUND_DOWN). Ambos bordes son inclusivos (|dif| ≤ umbral).
* Los importes se guardan como BIGINT: un CFDI de más de $21,474,836.47 es legal.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.models import ReporteVenta, ResultadoMatch
from app.services.conciliacion import CandidatoReporte, conciliar, umbral_centavos
from app.services.validacion import parsear_cfdi, validar

from apoyo import RFC_ALFA, RFC_RECEPTOR, cargar, total_de, xml_cfdi

PERIODO = "2026-09-P1"


def _veredicto(total: int, comision: int, *, porcentaje: float | Decimal = 0.005, absoluta: int = 100):
    candidato = CandidatoReporte(id=1, periodo=PERIODO, rfc_agencia=RFC_ALFA,
                                 comision_centavos=comision, boletos=1)
    return conciliar(total, PERIODO, RFC_ALFA, [candidato],
                     tolerancia_centavos=absoluta, tolerancia_porcentaje=porcentaje)


def test_umbral_porcentual_se_calcula_en_decimal_y_no_en_float() -> None:
    """21000 × 0.009 = 189 exactos; en float da 188.99999999999997 y truncaba a 188."""
    assert umbral_centavos(21_000, 100, 0.009) == 189
    assert _veredicto(21_000 + 189, 21_000, porcentaje=0.009).resultado is ResultadoMatch.MATCH
    assert _veredicto(21_000 + 190, 21_000, porcentaje=0.009).resultado is ResultadoMatch.PARCIAL


def test_umbral_porcentual_se_trunca_a_centavos() -> None:
    """0.5 % de $5,799.00 = 2,899.5 centavos → 2,899 (ROUND_DOWN, nunca más generoso)."""
    assert umbral_centavos(579_900, 100, 0.005) == 2_899
    assert _veredicto(579_900 + 2_899, 579_900).resultado is ResultadoMatch.MATCH
    assert _veredicto(579_900 + 2_900, 579_900).resultado is ResultadoMatch.PARCIAL


@pytest.mark.parametrize(("diferencia", "resultado"), [(100, "MATCH"), (-100, "MATCH"),
                                                        (101, "PARCIAL"), (-101, "PARCIAL")])
def test_tolerancia_absoluta_es_inclusiva(diferencia: int, resultado: str) -> None:
    """Comisión chica ($149.00): el 0.5 % (74 centavos) no supera el mínimo de $1.00."""
    assert _veredicto(14_900 + diferencia, 14_900).resultado is ResultadoMatch(resultado)


@pytest.mark.parametrize(("diferencia", "resultado"), [(100_000, "MATCH"), (-100_000, "MATCH"),
                                                        (100_001, "PARCIAL"), (-100_001, "PARCIAL")])
def test_tolerancia_porcentual_es_inclusiva(diferencia: int, resultado: str) -> None:
    """Comisión de $200,000.00: el 0.5 % ($1,000.00) manda sobre el mínimo absoluto."""
    assert _veredicto(20_000_000 + diferencia, 20_000_000).resultado is ResultadoMatch(resultado)


def test_porcentaje_como_decimal_o_float_da_el_mismo_umbral() -> None:
    for comision in (0, 1, 14_900, 579_900, 21_000, 20_000_000, 3_000_000_001):
        assert umbral_centavos(comision, 100, 0.005) == umbral_centavos(comision, 100, Decimal("0.005"))


@pytest.mark.parametrize(
    ("subtotal", "impuestos", "total", "coherente"),
    [
        ("1000.005", "160.00", "1160.005", True),     # 100001 + 16000 = 116001 (HALF_UP en cada uno)
        ("1000.004", "160.004", "1160.008", False),   # 100000 + 16000 ≠ 116001: no se suma antes de redondear
        ("1000.00", "160.00", "1160.00", True),
    ],
)
def test_cada_importe_del_xml_se_redondea_por_separado(subtotal, impuestos, total, coherente) -> None:
    xml = (xml_cfdi(subtotal_centavos=100_000, impuestos_centavos=16_000)
           .replace('SubTotal="1000.00"', f'SubTotal="{subtotal}"')
           .replace('TotalImpuestosTrasladados="160.00"', f'TotalImpuestosTrasladados="{impuestos}"')
           .replace('Total="1160.00"', f'Total="{total}"'))
    parseado = parsear_cfdi(xml)
    resultados = validar(parseado, xml_texto=xml, rfc_receptor_esperado=RFC_RECEPTOR,
                         rfcs_agencias_registradas={RFC_ALFA}, uuids_existentes=set())
    reglas = {r.regla: r.aprobada for r in resultados}
    assert reglas["TOTALES_COHERENTES"] is coherente


def test_cfdi_mayor_a_int32_se_guarda_y_concilia(cliente, bd, escenario) -> None:
    """$30,000,000.01: en PostgreSQL con INTEGER la carga reventaba con 500 (NumericValueOutOfRange)."""
    subtotal = 2_586_206_897
    total = total_de(subtotal)
    assert total > 2**31 - 1
    bd.add(ReporteVenta(periodo=PERIODO, rfc_agencia=RFC_ALFA, comision_centavos=total))
    bd.commit()

    respuesta = cargar(cliente, escenario.como("alfa"), xml_cfdi(subtotal_centavos=subtotal))

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["total_centavos"] == total
    assert cuerpo["resultado"] == "MATCH"
    resumen = cliente.get("/api/resumen", headers=escenario.como("analista")).json()
    assert resumen["total_facturado_centavos"] == total
    assert resumen["total_reportado_centavos"] == total
