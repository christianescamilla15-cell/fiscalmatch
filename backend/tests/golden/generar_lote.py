"""Genera el lote golden 2026-09 (N08b): CFDIs sintéticos, reporte de venta y resultado esperado.

    python backend/tests/golden/generar_lote.py            # reescribe los archivos del lote
    python backend/tests/golden/generar_lote.py --revisar  # sólo dice si los versionados están al día

El resultado esperado NO sale del código que se prueba (`app.services.*`): sale del GUION, donde
cada caso declara a mano su veredicto, y este script lo contrasta con su PROPIA lectura de las
reglas documentadas en docs/reglas.md (aritmética Decimal escrita aparte). Si el guion y esa
segunda lectura no coinciden, el script se detiene: el oráculo no se escribe con dudas.

Todo es ficticio: RFC de prueba, sellos de relleno y UUID derivados del nombre del caso.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import uuid as uuidlib
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

CARPETA = Path(__file__).resolve().parent / "lote_2026_09"
RAIZ_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RAIZ_REPO / "seed"))

from generar_cfdi import construir_cfdi_xml  # noqa: E402

# --------------------------------------------------------------------------- #
# Parámetros de la corrida (los mismos que fija tests/conftest.py)
# --------------------------------------------------------------------------- #

RFC_RECEPTOR = "FMA240101AB1"
RFC_RECEPTOR_AJENO = "XEXX010101000"
TOLERANCIA_CENTAVOS = Decimal(100)
TOLERANCIA_PORCENTAJE = Decimal("0.005")
TASA_IVA = Decimal("0.16")
ZONA = ZoneInfo("America/Mexico_City")
_NS = uuidlib.uuid5(uuidlib.NAMESPACE_DNS, "golden.fiscalmatch.dev")

AGENCIAS: dict[str, tuple[str, str]] = {
    "A": ("GAL010101AA1", "AGENCIA GOLDEN ALFA SA DE CV"),
    "B": ("GBE020202BB2", "AGENCIA GOLDEN BETA SA DE CV"),
    "C": ("GCE030303CC3", "AGENCIA GOLDEN GAMA SA DE CV"),
    "D": ("GDE040404DD4", "AGENCIA GOLDEN DELTA SA DE CV"),
    "E": ("GEP050505EE5", "AGENCIA GOLDEN EPSILON SA DE CV"),
}


@dataclass(frozen=True)
class Caso:
    clave: str
    agencia: str
    fecha: str                      # tal cual va en el XML
    subtotal_centavos: int
    #: comisión del reporte = total del CFDI − desviación; None ⇒ no hay reporte del periodo
    desviacion_centavos: int | None
    resultado: str                  # MATCH | PARCIAL | NO_MATCH | "" (rechazado, no se concilia)
    caso: str
    defecto: str | None = None


# El GUION: el veredicto de cada fila está declarado a mano.
GUION: tuple[Caso, ...] = (
    Caso("G01", "A", "2026-09-03T10:00:00", 1_000_000, 0, "MATCH", "coincidencia exacta"),
    Caso("G02", "B", "2026-09-15T23:59:59", 12_931, 100, "MATCH",
         "borde absoluto inclusivo (+$1.00) en el último segundo de P1"),
    Caso("G03", "C", "2026-09-10T09:00:00", 12_931, 101, "PARCIAL", "un centavo arriba del borde absoluto"),
    Caso("G04", "D", "2026-09-12T10:00:00", 17_241_379, 99_502, "MATCH",
         "borde porcentual inclusivo: 0.5 % de $199,004.98 = 99,502.49 → 99,502"),
    Caso("G05", "A", "2026-09-16T00:00:00", 17_241_379, 99_503, "PARCIAL",
         "un centavo arriba del borde porcentual, en el primer segundo de P2"),
    Caso("G06", "B", "2026-09-20T12:00:00", 502_414, 2_900, "PARCIAL",
         "umbral truncado: 0.5 % de $5,799.00 = 2,899.5 → 2,899 (redondeando sería MATCH)"),
    Caso("G07", "C", "2026-09-30T23:00:00", 502_414, 2_800, "MATCH", "dentro de la tolerancia porcentual"),
    Caso("G08", "D", "2026-09-25T08:00:00", 250_000, -90_000, "PARCIAL",
         "factura de menos (diferencia negativa)"),
    Caso("G09", "A", "2026-10-05T10:00:00", 300_000, None, "NO_MATCH", "periodo sin reporte de venta"),
    Caso("G10", "E", "2026-10-01T05:00:00Z", 86_207, 0, "MATCH",
         "Fecha en UTC: 30-sep 23:00 en México ⇒ concilia en 2026-09-P2, no en octubre"),
    Caso("G11", "A", "2026-09-04T10:00:00", 500_000, 0, "", "rechazado: Total = SubTotal + IVA + 1 centavo",
         defecto="TOTALES"),
    Caso("G12", "B", "2026-09-05T10:00:00", 400_000, 0, "", "rechazado: emitido a otro receptor",
         defecto="RECEPTOR"),
    Caso("G13", "C", "2026-09-18T10:00:00", 350_000, 0, "", "rechazado: CFDI 3.3", defecto="VERSION"),
    Caso("G14", "D", "2026-09-19T10:00:00", 450_000, 0, "", "rechazado: moneda USD", defecto="MONEDA"),
    Caso("G15", "E", "2026-09-09T10:00:00", 200_000, None, "NO_MATCH", "agencia sin reporte en P1"),
    Caso("G16", "C", "2026-10-06T10:00:00", 2_586_206_897, 0, "MATCH",
         "importe mayor a INT32 ($30,000,000.01)"),
)

#: Control negativo 1: el resultado esperado alterado a propósito (qué debe detectar la comparación).
ALTERACIONES_ESPERADO = {"distintas": {"G04": ["resultado", "estado"], "G06": ["total"]},
                         "sobrantes": ["G09"], "faltantes": ["G01"]}
#: Control negativo 2: el reporte de venta de G07 baja $30.00 ⇒ la diferencia excede el umbral.
ALTERACION_REPORTE = ("G07", -3_000)


# --------------------------------------------------------------------------- #
# Segunda lectura de las reglas (independiente de app.services)
# --------------------------------------------------------------------------- #


def uuid_de(clave: str) -> str:
    return str(uuidlib.uuid5(_NS, clave)).upper()


def iva(subtotal: int) -> int:
    return int((Decimal(subtotal) * TASA_IVA).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def periodo_esperado(fecha: str) -> str:
    momento = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
    if momento.tzinfo is not None:
        momento = momento.astimezone(ZONA)
    return f"{momento.year:04d}-{momento.month:02d}-P{1 if momento.day <= 15 else 2}"


def total_del_caso(caso: Caso) -> int:
    return caso.subtotal_centavos + iva(caso.subtotal_centavos) + (1 if caso.defecto == "TOTALES" else 0)


def comision_del_caso(caso: Caso) -> int | None:
    if caso.defecto or caso.desviacion_centavos is None:
        return None
    return total_del_caso(caso) - caso.desviacion_centavos


def dictamen(total: int, comision: int | None) -> tuple[str, int]:
    """(resultado, diferencia) según docs/reglas.md C1–C5, escrito aquí sin reutilizar el motor."""
    if comision is None:
        return "NO_MATCH", 0
    diferencia = total - comision
    umbral = max(TOLERANCIA_CENTAVOS,
                 (Decimal(abs(comision)) * TOLERANCIA_PORCENTAJE).to_integral_value(rounding=ROUND_DOWN))
    return ("MATCH" if abs(diferencia) <= umbral else "PARCIAL"), diferencia


def pesos(centavos: int | None) -> str:
    return "" if centavos is None else f"{Decimal(centavos) / 100:.2f}"


# --------------------------------------------------------------------------- #
# Construcción de los archivos
# --------------------------------------------------------------------------- #


def xml_del_caso(caso: Caso) -> str:
    rfc, razon = AGENCIAS[caso.agencia]
    subtotal = caso.subtotal_centavos
    sin_zona = caso.fecha[:19]
    xml = construir_cfdi_xml(
        uuid_str=uuid_de(caso.clave),
        rfc_emisor=rfc,
        nombre_emisor=razon,
        rfc_receptor=RFC_RECEPTOR_AJENO if caso.defecto == "RECEPTOR" else RFC_RECEPTOR,
        serie="G",
        folio=caso.clave[1:],
        fecha_iso=sin_zona,
        subtotal_centavos=subtotal,
        impuestos_centavos=iva(subtotal),
        total_centavos=total_del_caso(caso),
        no_certificado="30001000000500009901",
        version="3.3" if caso.defecto == "VERSION" else "4.0",
        moneda="USD" if caso.defecto == "MONEDA" else "MXN",
        descripcion=f"Comisión del lote golden · {caso.caso}",
    )
    if caso.fecha != sin_zona:
        xml = xml.replace(f' Fecha="{sin_zona}"', f' Fecha="{caso.fecha}"', 1)
    return xml


def fila_esperada(caso: Caso, comision: int | None) -> dict[str, str]:
    total = total_del_caso(caso)
    rfc = AGENCIAS[caso.agencia][0]
    if caso.defecto:
        resultado, diferencia, estado = "", None, "RECHAZADO"
    else:
        resultado, diferencia = dictamen(total, comision)
        estado = "CONCILIADO" if resultado == "MATCH" else "EN_EXCEPCION"
    return {"uuid": uuid_de(caso.clave), "rfc_emisor": rfc, "periodo": periodo_esperado(caso.fecha),
            "total": pesos(total), "resultado": resultado, "diferencia": pesos(diferencia), "estado": estado}


COLUMNAS = ["uuid", "rfc_emisor", "periodo", "total", "resultado", "diferencia", "estado"]


def huella(fila: dict[str, str]) -> str:
    """Hash por fila con llave estable (el UUID va primero): sha256 de los campos unidos por «|»."""
    return hashlib.sha256("|".join(fila[c] for c in COLUMNAS).encode("utf-8")).hexdigest()


def cifras_control(filas: list[dict[str, str]]) -> dict:
    """Conteo, sumas en Decimal (nunca float) y hash del lote independiente del orden."""
    por_estado: dict[str, int] = {}
    por_resultado: dict[str, int] = {}
    for fila in filas:
        por_estado[fila["estado"]] = por_estado.get(fila["estado"], 0) + 1
        clave = fila["resultado"] or "(sin conciliar)"
        por_resultado[clave] = por_resultado.get(clave, 0) + 1
    return {
        "conteo": len(filas),
        "suma_total": f"{sum((Decimal(f['total']) for f in filas), Decimal(0)):.2f}",
        "suma_diferencia": f"{sum((Decimal(f['diferencia'] or 0) for f in filas), Decimal(0)):.2f}",
        "por_estado": dict(sorted(por_estado.items())),
        "por_resultado": dict(sorted(por_resultado.items())),
        "hash_lote": hashlib.sha256("\n".join(sorted(huella(f) for f in filas)).encode()).hexdigest(),
        "definicion_hash": "sha256('|'.join(uuid, rfc_emisor, periodo, total, resultado, diferencia, "
                           "estado)); importes con 2 decimales y vacío si no hay; hash_lote = sha256 de "
                           "las huellas ordenadas unidas por salto de línea",
    }


def csv_de(filas: list[dict[str, str]], columnas: list[str]) -> str:
    salida = io.StringIO()
    escritor = csv.DictWriter(salida, fieldnames=columnas, lineterminator="\n")
    escritor.writeheader()
    escritor.writerows(filas)
    return salida.getvalue()


def reporte_csv(comisiones: dict[str, int]) -> str:
    filas = []
    for caso in GUION:
        if caso.clave in comisiones:
            comision = comisiones[caso.clave]
            filas.append({"periodo": periodo_esperado(caso.fecha), "rfc_agencia": AGENCIAS[caso.agencia][0],
                          "comision": pesos(comision), "boletos": str(max(1, comision // 42_500))})
    return csv_de(filas, ["periodo", "rfc_agencia", "comision", "boletos"])


def construir() -> dict[str, str]:
    """{ruta relativa a lote_2026_09/: contenido} de todo el lote."""
    archivos: dict[str, str] = {}
    comisiones = {c.clave: comision_del_caso(c) for c in GUION if comision_del_caso(c) is not None}

    esperadas = []
    for caso in GUION:
        fila = fila_esperada(caso, comisiones.get(caso.clave))
        if fila["resultado"] != caso.resultado:
            raise SystemExit(f"{caso.clave}: el guion dice {caso.resultado or 'RECHAZADO'} y la "
                             f"lectura de las reglas da {fila['resultado'] or 'RECHAZADO'}; "
                             "no se escribe el oráculo.")
        esperadas.append(fila)
        rfc = AGENCIAS[caso.agencia][0]
        archivos[f"cfdis/{caso.clave}-{rfc}.xml"] = xml_del_caso(caso)

    archivos["agencias.csv"] = csv_de(
        [{"clave": k, "rfc": rfc, "razon_social": razon} for k, (rfc, razon) in AGENCIAS.items()],
        ["clave", "rfc", "razon_social"])
    archivos["reporte_venta.csv"] = reporte_csv(comisiones)
    archivos["esperado.csv"] = csv_de(esperadas, COLUMNAS)
    cifras = json.dumps(cifras_control(esperadas), indent=2, ensure_ascii=False)
    archivos["cifras_control.json"] = cifras + "\n"
    archivos["guion.csv"] = csv_de(
        [{"clave": c.clave, "uuid": uuid_de(c.clave), "agencia": AGENCIAS[c.agencia][0], "fecha": c.fecha,
          "resultado": c.resultado or "RECHAZADO", "caso": c.caso} for c in GUION],
        ["clave", "uuid", "agencia", "fecha", "resultado", "caso"])

    # Control negativo 1: esperado alterado.
    por_clave = {c.clave: dict(f) for c, f in zip(GUION, esperadas, strict=True)}
    alteradas = []
    for caso in GUION:
        fila = dict(por_clave[caso.clave])
        if caso.clave in ALTERACIONES_ESPERADO["sobrantes"]:
            continue                                      # el golden «olvida» la fila
        if caso.clave == "G04":
            fila["resultado"], fila["estado"] = "PARCIAL", "EN_EXCEPCION"
        if caso.clave == "G06":
            fila["total"] = pesos(int(Decimal(fila["total"]) * 100) + 1)
        alteradas.append(fila)
        if caso.clave in ALTERACIONES_ESPERADO["faltantes"]:
            alteradas.append(dict(fila))                  # el golden pide la fila DOS veces
    archivos["control_negativo/esperado_alterado.csv"] = csv_de(alteradas, COLUMNAS)
    archivos["control_negativo/alteraciones.json"] = json.dumps({
        "distintas": {uuid_de(k): v for k, v in ALTERACIONES_ESPERADO["distintas"].items()},
        "sobrantes": [uuid_de(k) for k in ALTERACIONES_ESPERADO["sobrantes"]],
        "faltantes": [uuid_de(k) for k in ALTERACIONES_ESPERADO["faltantes"]],
        "reporte_alterado": {"uuid": uuid_de(ALTERACION_REPORTE[0]),
                             "campos": ["resultado", "diferencia", "estado"]},
    }, indent=2, ensure_ascii=False) + "\n"

    # Control negativo 2: reporte de venta alterado.
    clave, ajuste = ALTERACION_REPORTE
    alterado = dict(comisiones)
    alterado[clave] += ajuste
    archivos["control_negativo/reporte_venta_alterado.csv"] = reporte_csv(alterado)
    return archivos


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--revisar", action="store_true", help="no escribe; sale con 1 si algo difiere")
    args = parser.parse_args()
    archivos = construir()
    distintos = [r for r, c in archivos.items()
                 if not (CARPETA / r).is_file() or (CARPETA / r).read_text(encoding="utf-8") != c]
    if args.revisar:
        print(f"{len(archivos)} archivos · {len(distintos)} distintos de lo versionado: {distintos}")
        return 1 if distintos else 0
    for ruta, contenido in archivos.items():
        destino = CARPETA / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido, encoding="utf-8")
    print(f"{len(archivos)} archivos escritos en {CARPETA} ({len(distintos)} cambiaron)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
