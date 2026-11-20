"""E2E de la API por rol: el flujo completo de una quincena, por HTTP y desde el login.

No hay navegador (el portal se prueba con `npm run build`; un E2E de pantallas con Playwright
queda pendiente): aquí se ejerce la API real con su base de datos, como lo haría el portal.

    agencia Alfa ─login─▶ carga 2 CFDIs (MATCH y PARCIAL) ─▶ sólo ve los suyos
    agencia Beta ─login─▶ carga 1 CFDI sin reporte (NO_MATCH)
    analista ────login─▶ cola de excepciones ─▶ acepta el PARCIAL ─▶ tablero ─▶ exporta CSV
    bitácora: quién hizo qué, comparada como multiconjunto (actor, acción)
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from app.domain.models import ReporteVenta

from apoyo import RFC_ALFA, RFC_BETA, cargar, total_de, xml_cfdi
from conftest import PASSWORD
from paridad import Diferencias, comparar, leer_csv

PERIODO = "2026-09-P1"


def _login(cliente, email: str) -> dict[str, str]:
    respuesta = cliente.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert respuesta.status_code == 200, respuesta.text
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


def _pesos(centavos: int) -> str:
    return f"{Decimal(centavos) / 100:.2f}"


def test_quincena_completa_por_rol(cliente, bd, escenario) -> None:
    sub_match, sub_parcial, sub_sin_reporte = 1_000_000, 500_000, 250_000
    # Un reporte por agencia y quincena: el primer CFDI de Alfa cuadra exacto y el segundo,
    # comparado contra la misma comisión, queda PARCIAL. Beta no tiene reporte (NO_MATCH).
    bd.add(ReporteVenta(periodo=PERIODO, rfc_agencia=RFC_ALFA, comision_centavos=total_de(sub_match)))
    bd.commit()

    # --- Agencias ------------------------------------------------------------------
    alfa = _login(cliente, "alfa@prueba.dev")
    beta = _login(cliente, "beta@prueba.dev")
    respuestas = [
        cargar(cliente, alfa, xml_cfdi(rfc_emisor=RFC_ALFA, subtotal_centavos=sub_match)),
        cargar(cliente, alfa, xml_cfdi(rfc_emisor=RFC_ALFA, subtotal_centavos=sub_parcial,
                                       fecha="2026-09-10T12:00:00")),
        cargar(cliente, beta, xml_cfdi(rfc_emisor=RFC_BETA, subtotal_centavos=sub_sin_reporte)),
    ]
    assert [r.status_code for r in respuestas] == [201, 201, 201]
    match, parcial, sin_reporte = (r.json() for r in respuestas)
    assert [c["resultado"] for c in (match, parcial, sin_reporte)] == ["MATCH", "PARCIAL", "NO_MATCH"]

    propios = cliente.get("/api/cfdis", headers=alfa).json()
    assert sorted(c["uuid"] for c in propios) == sorted([match["uuid"], parcial["uuid"]])
    assert cliente.get("/api/excepciones", headers=alfa).status_code == 403

    # --- Analista ------------------------------------------------------------------
    analista = _login(cliente, "analista@prueba.dev")
    cola = cliente.get("/api/excepciones", headers=analista).json()
    assert Counter((c["uuid"], c["resultado"]) for c in cola) == Counter(
        {(parcial["uuid"], "PARCIAL"): 1, (sin_reporte["uuid"], "NO_MATCH"): 1})

    resuelta = cliente.post(f"/api/conciliaciones/{parcial['conciliacion']['id']}/resolver",
                            json={"aceptar": True, "nota": "ajuste autorizado"}, headers=analista)
    assert resuelta.status_code == 200 and resuelta.json()["metodo"] == "MANUAL"

    resumen = cliente.get("/api/resumen", headers=analista).json()
    assert resumen["total_cfdis"] == 3
    assert resumen["por_estado"] == {"RECIBIDO": 0, "VALIDADO": 0, "RECHAZADO": 0,
                                     "CONCILIADO": 2, "EN_EXCEPCION": 1}
    facturado = sum(map(total_de, (sub_match, sub_parcial, sub_sin_reporte)))
    assert resumen["total_facturado_centavos"] == facturado
    assert resumen["total_conciliado_centavos"] == total_de(sub_match) + total_de(sub_parcial)

    exportado = cliente.get("/api/exportar.csv", headers=analista)
    assert exportado.status_code == 200
    assert exportado.headers["content-type"].startswith("text/csv")
    diferencia_parcial = total_de(sub_parcial) - total_de(sub_match)
    esperado = [
        (match["uuid"], RFC_ALFA, PERIODO, _pesos(total_de(sub_match)), "MATCH", "0.00", "CONCILIADO"),
        (parcial["uuid"], RFC_ALFA, PERIODO, _pesos(total_de(sub_parcial)), "MATCH",
         _pesos(diferencia_parcial), "CONCILIADO"),
        (sin_reporte["uuid"], RFC_BETA, PERIODO, _pesos(total_de(sub_sin_reporte)), "NO_MATCH", "0.00",
         "EN_EXCEPCION"),
    ]
    assert comparar(esperado, leer_csv(exportado.text)) == Diferencias()

    # --- Bitácora ------------------------------------------------------------------
    eventos = cliente.get("/api/auditoria", headers=analista).json()
    assert Counter((e["actor"], e["accion"]) for e in eventos) == Counter({
        ("alfa@prueba.dev", "LOGIN"): 1, ("beta@prueba.dev", "LOGIN"): 1, ("analista@prueba.dev", "LOGIN"): 1,
        ("alfa@prueba.dev", "CARGA_CFDI"): 2, ("beta@prueba.dev", "CARGA_CFDI"): 1,
        ("analista@prueba.dev", "RESOLUCION_MANUAL"): 1, ("analista@prueba.dev", "EXPORTA_CSV"): 1,
    })
    assert eventos[0]["accion"] == "EXPORTA_CSV"            # del más reciente al más antiguo
