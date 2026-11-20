"""Ayudantes compartidos por las pruebas que pasan por la API y la base de datos.

Los CFDIs se arman con el generador sintético del repo (`seed/generar_cfdi.py`): RFC,
sellos y certificados de relleno, nada de un contribuyente real. Cada parámetro permite
romper UNA cosa del comprobante.
"""
from __future__ import annotations

import sys
import uuid as uuidlib
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ_REPO / "seed"))

from generar_cfdi import calcular_iva_centavos, construir_cfdi_xml  # noqa: E402

#: RFC de la empresa receptora con el que corre la suite (se fuerza en conftest.py).
RFC_RECEPTOR = "FMA240101AB1"
RFC_ALFA = "AAA010101AA1"
RFC_BETA = "BBB020202BB2"
NO_CERTIFICADO = "30001000000500003401"


def nuevo_uuid() -> str:
    return str(uuidlib.uuid4()).upper()


def xml_cfdi(
    *,
    rfc_emisor: str = RFC_ALFA,
    subtotal_centavos: int = 1_000_000,
    impuestos_centavos: int | None = None,
    total_centavos: int | None = None,
    fecha: str = "2026-09-03T10:00:00",
    uuid: str | None = None,
    rfc_receptor: str = RFC_RECEPTOR,
    version: str = "4.0",
    moneda: str = "MXN",
    folio: str = "1",
) -> str:
    """CFDI 4.0 sintético. Por omisión es válido y cuadra: total = subtotal + IVA 16 %.

    `fecha` admite zona ("...Z", "-05:00") y `uuid` puede venir en minúsculas: el generador
    escribe la fecha sin zona y el UUID en mayúsculas, así que ambos se sustituyen en el XML
    ya armado para probar cómo se interpretan.
    """
    uuid = uuid or nuevo_uuid()
    impuestos = calcular_iva_centavos(subtotal_centavos) if impuestos_centavos is None else impuestos_centavos
    total = subtotal_centavos + impuestos if total_centavos is None else total_centavos
    sin_zona = fecha[:19]
    xml = construir_cfdi_xml(
        uuid_str=uuid,
        rfc_emisor=rfc_emisor,
        nombre_emisor="AGENCIA DE PRUEBA SA DE CV",
        rfc_receptor=rfc_receptor,
        serie="A",
        folio=folio,
        fecha_iso=sin_zona,
        subtotal_centavos=subtotal_centavos,
        impuestos_centavos=impuestos,
        total_centavos=total,
        no_certificado=NO_CERTIFICADO,
        version=version,
        moneda=moneda,
    )
    if fecha != sin_zona:
        xml = xml.replace(f' Fecha="{sin_zona}"', f' Fecha="{fecha}"', 1)
    if uuid != uuid.upper():
        xml = xml.replace(f' UUID="{uuid.upper()}"', f' UUID="{uuid}"', 1)
    return xml


def total_de(subtotal_centavos: int) -> int:
    """Total que tendrá `xml_cfdi(subtotal_centavos=...)` por omisión."""
    return subtotal_centavos + calcular_iva_centavos(subtotal_centavos)


def cargar(cliente, encabezados: dict[str, str], xml: str, nombre: str = "cfdi.xml"):
    """POST /api/cfdis con el XML como archivo (multipart), igual que el portal."""
    return cliente.post(
        "/api/cfdis",
        files={"archivo": (nombre, xml.encode("utf-8"), "application/xml")},
        headers=encabezados,
    )
