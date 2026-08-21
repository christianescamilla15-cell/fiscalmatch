"""Pruebas del motor PURO de conciliación: sin base de datos, sin FastAPI.

Todos los importes están en CENTAVOS enteros.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Permite `pytest tests/...` desde cualquier directorio: la raíz importable es `backend/`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.models import ResultadoMatch  # noqa: E402
from app.services.conciliacion import (  # noqa: E402
    CandidatoReporte,
    conciliar,
    pesos,
)

TOL_CENTAVOS = 100        # ±$1.00 MXN
TOL_PORCENTAJE = 0.005    # ±0.5 %
PERIODO = "2026-08-P1"
RFC = "AVT950101QX3"


def _candidato(
    id_: int,
    comision_centavos: int,
    *,
    periodo: str = PERIODO,
    rfc: str = RFC,
    boletos: int = 12,
) -> CandidatoReporte:
    return CandidatoReporte(
        id=id_,
        periodo=periodo,
        rfc_agencia=rfc,
        comision_centavos=comision_centavos,
        boletos=boletos,
    )


def _conciliar(total_centavos: int, candidatos: list[CandidatoReporte], *, periodo: str = PERIODO):
    return conciliar(
        total_centavos,
        periodo,
        RFC,
        candidatos,
        tolerancia_centavos=TOL_CENTAVOS,
        tolerancia_porcentaje=TOL_PORCENTAJE,
    )


def test_match_exacto():
    """Mismo importe al centavo: MATCH sin diferencia."""
    veredicto = _conciliar(500_000, [_candidato(7, 500_000)])

    assert veredicto.resultado is ResultadoMatch.MATCH
    assert veredicto.reporte_id == 7
    assert veredicto.diferencia_centavos == 0
    assert "exacta" in veredicto.motivo


def test_dentro_de_tolerancia_absoluta():
    """Comisión chica: manda la tolerancia absoluta ($1.00), no la porcentual ($0.50)."""
    comision = 10_000            # $100.00 → 0.5 % = 50 centavos
    veredicto = _conciliar(comision + 80, [_candidato(1, comision)])

    assert veredicto.resultado is ResultadoMatch.MATCH
    assert veredicto.diferencia_centavos == 80
    assert "tolerancia" in veredicto.motivo
    assert pesos(TOL_CENTAVOS) in veredicto.motivo   # el umbral aplicado fue el absoluto


def test_dentro_de_tolerancia_porcentual_importe_grande():
    """Comisión grande: 0.5 % ($500.00) supera con creces la tolerancia absoluta."""
    comision = 10_000_000        # $100,000.00 → 0.5 % = 50,000 centavos
    veredicto = _conciliar(comision + 30_000, [_candidato(3, comision)])

    assert veredicto.resultado is ResultadoMatch.MATCH
    assert veredicto.diferencia_centavos == 30_000
    assert pesos(50_000) in veredicto.motivo         # el umbral aplicado fue el porcentual


def test_fuera_de_tolerancia_es_parcial():
    """$120.00 de más contra un umbral de $25.00: cae en excepción como PARCIAL."""
    comision = 500_000           # $5,000.00 → 0.5 % = 2,500 centavos
    veredicto = _conciliar(512_000, [_candidato(9, comision)])

    assert veredicto.resultado is ResultadoMatch.PARCIAL
    assert veredicto.reporte_id == 9
    assert veredicto.diferencia_centavos == 12_000
    assert "$120.00 MXN" in veredicto.motivo
    assert "$25.00 MXN" in veredicto.motivo


def test_sin_candidatos_es_no_match():
    """No hay reporte de venta de esa agencia: no hay contra qué conciliar."""
    veredicto = _conciliar(500_000, [])

    assert veredicto.resultado is ResultadoMatch.NO_MATCH
    assert veredicto.reporte_id is None
    assert veredicto.diferencia_centavos == 0
    assert RFC in veredicto.motivo
    assert PERIODO in veredicto.motivo


def test_periodo_distinto_no_matchea():
    """El importe cuadra, pero es de otro periodo: NO_MATCH, no MATCH."""
    veredicto = _conciliar(500_000, [_candidato(4, 500_000, periodo="2026-07-P2")])

    assert veredicto.resultado is ResultadoMatch.NO_MATCH
    assert veredicto.reporte_id is None


def test_rfc_distinto_no_matchea():
    """El importe y el periodo cuadran, pero el reporte es de otra agencia."""
    veredicto = _conciliar(500_000, [_candidato(5, 500_000, rfc="OTR880202LM7")])

    assert veredicto.resultado is ResultadoMatch.NO_MATCH
    assert veredicto.reporte_id is None


def test_varios_candidatos_toma_el_mas_cercano():
    """Caso defensivo: se elige el de menor diferencia absoluta y se dice en el motivo."""
    candidatos = [_candidato(11, 500_000), _candidato(12, 505_000)]
    veredicto = _conciliar(504_000, candidatos)

    assert veredicto.reporte_id == 12                # |−1,000| < |4,000|
    assert veredicto.diferencia_centavos == -1_000
    assert veredicto.resultado is ResultadoMatch.MATCH   # umbral = 0.5 % de 505,000 = 2,525
    assert "más cercano" in veredicto.motivo
    assert "2 reportes" in veredicto.motivo


def test_diferencia_negativa_conserva_el_signo():
    """CFDI por debajo del reporte: la diferencia se reporta NEGATIVA."""
    veredicto = _conciliar(490_000, [_candidato(20, 500_000)])

    assert veredicto.resultado is ResultadoMatch.PARCIAL
    assert veredicto.diferencia_centavos == -10_000
    assert "-$100.00 MXN" in veredicto.motivo


def test_pesos_formatea_con_separador_de_miles_y_signo():
    assert pesos(0) == "$0.00 MXN"
    assert pesos(5) == "$0.05 MXN"
    assert pesos(123_456) == "$1,234.56 MXN"
    assert pesos(-123_456) == "-$1,234.56 MXN"
