"""Paridad contra un oráculo independiente (N08b): el lote golden 2026-09 de punta a punta.

El lote (`golden/lote_2026_09/`) son 16 CFDIs sintéticos, el reporte de venta de sus agencias y
el resultado esperado escrito por el guion de `golden/generar_lote.py`, que lo contrasta con su
propia lectura de las reglas (no reutiliza `app.services`). La prueba hace lo que haría la
operación real: cada agencia sube sus XML por la API, el analista exporta el corte y el CSV
exportado se compara contra `esperado.csv` por multiconjunto con llave UUID, más las cifras
control de `cifras_control.json` (conteo, sumas en Decimal, hash del lote).

El listón es el CONTROL NEGATIVO: un esperado alterado y un reporte de venta alterado deben
producir exactamente las diferencias sembradas. Si el comparador dijera «todo igual» siempre,
estas pruebas fallarían.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.seguridad import crear_token
from app.domain.models import Agencia, ReporteVenta, Rol, Usuario

from apoyo import cargar
from paridad import COLUMNAS, Diferencias, cifras_control, comparar, leer_csv

GOLDEN = Path(__file__).resolve().parent / "golden"
LOTE = GOLDEN / "lote_2026_09"
CONTROL_NEGATIVO = LOTE / "control_negativo"


def _leer(ruta: Path) -> str:
    return ruta.read_text(encoding="utf-8")


def _cifras_golden() -> dict:
    cifras = json.loads(_leer(LOTE / "cifras_control.json"))
    cifras.pop("definicion_hash")
    return cifras


def _sembrar(bd, reporte: Path) -> dict[str, dict[str, str]]:
    """Agencias, un usuario por agencia y un analista; el reporte de venta va directo a la base
    (no hay endpoint de carga de reportes). Devuelve los encabezados por RFC y del analista."""
    encabezados: dict[str, dict[str, str]] = {}
    with (LOTE / "agencias.csv").open(encoding="utf-8") as archivo:
        for fila in csv.DictReader(archivo):
            agencia = Agencia(rfc=fila["rfc"], razon_social=fila["razon_social"])
            bd.add(agencia)
            bd.flush()
            usuario = Usuario(email=f"{fila['clave'].lower()}@golden.dev", nombre=fila["razon_social"],
                              rol=Rol.AGENCIA, agencia_id=agencia.id, password_hash="sin-acceso")
            bd.add(usuario)
            bd.flush()
            token = crear_token(usuario.id, usuario.rol, agencia.id)
            encabezados[fila["rfc"]] = {"Authorization": f"Bearer {token}"}
    analista = Usuario(email="analista@golden.dev", nombre="Analista", rol=Rol.ANALISTA,
                       password_hash="sin-acceso")
    bd.add(analista)
    with reporte.open(encoding="utf-8") as archivo:
        for fila in csv.DictReader(archivo):
            centavos = Decimal(fila["comision"]) * 100
            assert centavos == centavos.to_integral_value(), "el reporte trae fracciones de centavo"
            bd.add(ReporteVenta(periodo=fila["periodo"], rfc_agencia=fila["rfc_agencia"],
                                comision_centavos=int(centavos), boletos=int(fila["boletos"])))
    bd.commit()
    encabezados["analista"] = {"Authorization": f"Bearer {crear_token(analista.id, analista.rol, None)}"}
    return encabezados


def _subir_lote(cliente, encabezados, *, inverso: bool = False) -> dict[str, int]:
    """Cada agencia sube sus propios XML; devuelve el código HTTP por archivo."""
    xmls = sorted((LOTE / "cfdis").glob("*.xml"), reverse=inverso)
    assert len(xmls) == 16
    codigos = {}
    for xml in xmls:
        rfc = xml.stem.split("-", 1)[1]
        codigos[xml.stem] = cargar(cliente, encabezados[rfc], _leer(xml), nombre=xml.name).status_code
    return codigos


def _exportar(cliente, encabezados) -> list[tuple[str, ...]]:
    respuesta = cliente.get("/api/exportar.csv", headers=encabezados["analista"])
    assert respuesta.status_code == 200
    return leer_csv(respuesta.text)


def _codigos_esperados() -> dict[str, int]:
    with (LOTE / "guion.csv").open(encoding="utf-8") as archivo:
        return {f"{f['clave']}-{f['agencia']}": 422 if f["resultado"] == "RECHAZADO" else 201
                for f in csv.DictReader(archivo)}


def _correr(cliente, bd, reporte: Path = LOTE / "reporte_venta.csv", **kwargs) -> list[tuple[str, ...]]:
    encabezados = _sembrar(bd, reporte)
    assert _subir_lote(cliente, encabezados, **kwargs) == _codigos_esperados()
    return _exportar(cliente, encabezados)


ESPERADO = leer_csv(_leer(LOTE / "esperado.csv"))


# --------------------------------------------------------------------------- #
# El oráculo es coherente consigo mismo y con su guion
# --------------------------------------------------------------------------- #


def test_el_golden_esta_al_dia_con_su_guion() -> None:
    """Nadie editó a mano un archivo del lote: regenerarlo desde el guion da lo mismo, byte a byte."""
    spec = importlib.util.spec_from_file_location("generar_lote", GOLDEN / "generar_lote.py")
    generador = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = generador          # dataclasses resuelve anotaciones por módulo
    spec.loader.exec_module(generador)

    archivos = generador.construir()

    versionados = sorted(p.relative_to(LOTE).as_posix() for p in LOTE.rglob("*") if p.is_file())
    assert versionados == sorted(archivos)
    for ruta, contenido in archivos.items():
        assert _leer(LOTE / ruta) == contenido, f"{ruta} no corresponde al guion"


def test_las_cifras_control_cuadran_con_las_filas_del_golden() -> None:
    assert len(ESPERADO) == 16
    assert cifras_control(ESPERADO) == _cifras_golden()


# --------------------------------------------------------------------------- #
# Paridad del sistema contra el golden
# --------------------------------------------------------------------------- #


def test_paridad_del_lote_contra_el_golden(cliente, bd) -> None:
    obtenido = _correr(cliente, bd)

    assert comparar(ESPERADO, obtenido) == Diferencias()
    assert cifras_control(obtenido) == _cifras_golden()


def test_el_orden_de_carga_no_cambia_el_resultado(cliente, bd) -> None:
    obtenido = _correr(cliente, bd, inverso=True)

    assert comparar(ESPERADO, obtenido) == Diferencias()
    assert cifras_control(obtenido)["hash_lote"] == _cifras_golden()["hash_lote"]


def test_reenviar_el_lote_completo_es_idempotente(cliente, bd) -> None:
    encabezados = _sembrar(bd, LOTE / "reporte_venta.csv")
    _subir_lote(cliente, encabezados)
    primero = _exportar(cliente, encabezados)

    segundo_envio = _subir_lote(cliente, encabezados, inverso=True)

    assert set(segundo_envio.values()) == {409}
    assert comparar(primero, _exportar(cliente, encabezados)) == Diferencias()
    assert comparar(ESPERADO, primero) == Diferencias()


# --------------------------------------------------------------------------- #
# Controles negativos: lo alterado a propósito DEBE detectarse, y nada más
# --------------------------------------------------------------------------- #


def test_control_negativo_un_golden_alterado_se_detecta(cliente, bd) -> None:
    sembrado = json.loads(_leer(CONTROL_NEGATIVO / "alteraciones.json"))
    alterado = leer_csv(_leer(CONTROL_NEGATIVO / "esperado_alterado.csv"))
    obtenido = _correr(cliente, bd)

    diferencias = comparar(alterado, obtenido)

    assert diferencias.distintas == sembrado["distintas"]
    assert diferencias.sobrantes == sembrado["sobrantes"]
    assert diferencias.faltantes == sembrado["faltantes"]
    assert cifras_control(alterado) != cifras_control(obtenido)


def test_control_negativo_un_reporte_de_venta_alterado_se_detecta(cliente, bd) -> None:
    sembrado = json.loads(_leer(CONTROL_NEGATIVO / "alteraciones.json"))["reporte_alterado"]

    obtenido = _correr(cliente, bd, reporte=CONTROL_NEGATIVO / "reporte_venta_alterado.csv")

    diferencias = comparar(ESPERADO, obtenido)
    assert diferencias == Diferencias(distintas={sembrado["uuid"]: sembrado["campos"]})
    assert cifras_control(obtenido)["suma_diferencia"] != _cifras_golden()["suma_diferencia"]


# --------------------------------------------------------------------------- #
# El comparador mismo
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("orden", ["igual", "inverso"])
def test_el_comparador_es_por_multiconjunto_y_no_posicional(orden: str) -> None:
    obtenido = ESPERADO if orden == "igual" else list(reversed(ESPERADO))
    assert comparar(ESPERADO, obtenido) == Diferencias()
    duplicado = obtenido + [obtenido[0]]
    assert comparar(ESPERADO, duplicado) == Diferencias(sobrantes=[obtenido[0][0]])


def test_el_comparador_normaliza_separador_de_miles_bom_y_mayusculas() -> None:
    fila = dict(zip(COLUMNAS, ESPERADO[3], strict=True))
    exportado = ("﻿" + ",".join(COLUMNAS) + "\n"
                 + f"{fila['uuid'].lower()},{fila['rfc_emisor']},{fila['periodo']},\"200,000.00\","
                   f"{fila['resultado']},{fila['diferencia']},{fila['estado']}\n")
    assert leer_csv(exportado) == [ESPERADO[3]]
