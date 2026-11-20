"""Generador de CFDIs 4.0 de PRUEBA para FiscalMatch.

Todo lo que produce este módulo es FICTICIO: los RFC, los sellos, los certificados y el
PAC que "timbra" son de relleno y no corresponden a ningún contribuyente real. Su único
propósito es ejercitar el validador fiscal y el motor de conciliación con comprobantes
bien formados.

REGLA TRANSVERSAL: los importes entran en CENTAVOS (enteros) y se formatean con `Decimal`.
Nunca se usa `float` para dinero.
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "construir_cfdi_xml",
    "calcular_iva_centavos",
    "formatear_pesos",
    "TASA_IVA",
]

# --------------------------------------------------------------------------------------
# Constantes del comprobante
# --------------------------------------------------------------------------------------

NS_CFDI_40 = "http://www.sat.gob.mx/cfd/4"
NS_CFDI_33 = "http://www.sat.gob.mx/cfd/3"
NS_TFD = "http://www.sat.gob.mx/TimbreFiscalDigital"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

TASA_IVA = Decimal("0.160000")

# Longitudes reales del sello (344 chars) y del certificado; ambas múltiplo de 4 para que
# el base64 de relleno salga sin caracteres de padding.
_LARGO_SELLO = 344
_LARGO_CERTIFICADO = 1372

# PAC ficticio: no es el RFC de ningún proveedor de certificación real.
_RFC_PAC_FICTICIO = "PAC010101AB9"

_CENTAVO = Decimal("0.01")
_ESCAPES_ATRIBUTO = {'"': "&quot;", "'": "&apos;", "\n": "&#10;", "\t": "&#9;"}


# --------------------------------------------------------------------------------------
# Utilidades de dinero
# --------------------------------------------------------------------------------------

def formatear_pesos(centavos: int) -> str:
    """Convierte centavos enteros a la cadena con 2 decimales que exige el CFDI.

    >>> formatear_pesos(812450)
    '8124.50'
    """
    if isinstance(centavos, bool) or not isinstance(centavos, int):
        raise TypeError("Los importes deben ser enteros en centavos, nunca float.")
    return f"{(Decimal(centavos) / Decimal(100)).quantize(_CENTAVO):f}"


def calcular_iva_centavos(subtotal_centavos: int, tasa: Decimal = TASA_IVA) -> int:
    """IVA trasladado, en centavos enteros, redondeado a la mitad hacia arriba."""
    if isinstance(subtotal_centavos, bool) or not isinstance(subtotal_centavos, int):
        raise TypeError("El subtotal debe ser un entero en centavos, nunca float.")
    bruto = Decimal(subtotal_centavos) * tasa
    return int(bruto.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------------------
# Utilidades de serialización
# --------------------------------------------------------------------------------------

def _escapar(valor: object) -> str:
    """Escapa un valor para usarlo dentro de un atributo XML entrecomillado."""
    texto = str(valor)
    texto = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for crudo, entidad in _ESCAPES_ATRIBUTO.items():
        texto = texto.replace(crudo, entidad)
    return texto


def _atributos(pares: dict[str, object | None]) -> str:
    """Serializa un diccionario de atributos; omite los que vengan en None."""
    return " ".join(
        f'{nombre}="{_escapar(valor)}"' for nombre, valor in pares.items() if valor is not None
    )


def _relleno_base64(semilla: str, longitud: int) -> str:
    """Base64 determinista de relleno, del largo pedido, para Sello y Certificado.

    No es criptografía: es material de relleno reproducible para que dos siembras con la
    misma semilla produzcan exactamente el mismo XML.
    """
    if longitud % 4:
        raise ValueError("La longitud del relleno base64 debe ser múltiplo de 4.")
    crudos_necesarios = longitud // 4 * 3
    bloques = bytearray()
    contador = 0
    while len(bloques) < crudos_necesarios:
        bloques += hashlib.sha256(f"{semilla}#{contador}".encode()).digest()
        contador += 1
    return base64.b64encode(bytes(bloques[:crudos_necesarios])).decode("ascii")


def _normalizar_fecha(fecha_iso: str) -> datetime:
    """Acepta 'AAAA-MM-DD' o 'AAAA-MM-DDTHH:MM:SS' y devuelve un datetime sin zona."""
    texto = fecha_iso.strip()
    if len(texto) == 10:
        texto += "T12:00:00"
    momento = datetime.fromisoformat(texto)
    return momento.replace(tzinfo=None)


def _sello_cfdi(momento: datetime) -> str:
    return momento.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------------------
# Constructor del comprobante
# --------------------------------------------------------------------------------------

def construir_cfdi_xml(
    *,
    uuid_str: str,
    rfc_emisor: str,
    nombre_emisor: str,
    rfc_receptor: str,
    serie: str,
    folio: str,
    fecha_iso: str,
    subtotal_centavos: int,
    impuestos_centavos: int,
    total_centavos: int,
    no_certificado: str,
    version: str = "4.0",
    moneda: str = "MXN",
    nombre_receptor: str = "FISCALMATCH SA DE CV",
    descripcion: str = "Comisión por intermediación en venta de servicios de viaje",
    uso_cfdi: str = "G03",
    lugar_expedicion: str = "06600",
    regimen_emisor: str = "601",
    regimen_receptor: str = "601",
    metodo_pago: str = "PUE",
    forma_pago: str = "03",
) -> str:
    """Devuelve el XML de un CFDI de comisión, bien formado y con timbre fiscal.

    Los tres importes se reciben por separado y se escriben tal cual: eso permite generar
    a propósito comprobantes con totales INCOHERENTES (`total != subtotal + impuestos`)
    para probar el rechazo del validador.

    `version` acepta "4.0" (namespace `cfd/4`, el caso normal) y "3.3" (namespace `cfd/3`,
    para probar que el validador rechaza versiones fuera de vigencia).
    """
    for etiqueta, valor in (
        ("subtotal_centavos", subtotal_centavos),
        ("impuestos_centavos", impuestos_centavos),
        ("total_centavos", total_centavos),
    ):
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise TypeError(f"{etiqueta} debe ser un entero en centavos, nunca float.")

    es_40 = version.startswith("4")
    ns_cfdi = NS_CFDI_40 if es_40 else NS_CFDI_33
    xsd = (
        f"{ns_cfdi} http://www.sat.gob.mx/sitio_internet/cfd/"
        f"{'4/cfdv40.xsd' if es_40 else '3/cfdv33.xsd'}"
    )

    emitido = _normalizar_fecha(fecha_iso)
    timbrado = emitido + timedelta(minutes=3)
    uuid_normalizado = uuid_str.upper()

    subtotal = formatear_pesos(subtotal_centavos)
    impuestos = formatear_pesos(impuestos_centavos)
    total = formatear_pesos(total_centavos)

    sello = _relleno_base64(f"sello|{uuid_normalizado}", _LARGO_SELLO)
    sello_sat = _relleno_base64(f"sat|{uuid_normalizado}", _LARGO_SELLO)
    certificado = _relleno_base64(f"cert|{rfc_emisor}|{no_certificado}", _LARGO_CERTIFICADO)
    # Determinista a propósito: `hash()` de Python varía entre procesos.
    _digito_sat = int(hashlib.sha256(uuid_normalizado.encode("utf-8")).hexdigest()[:6], 16) % 1000
    no_certificado_sat = f"30001000000400002{_digito_sat:03d}"

    comprobante: dict[str, object | None] = {
        "xmlns:cfdi": ns_cfdi,
        "xmlns:tfd": NS_TFD,
        "xmlns:xsi": NS_XSI,
        "xsi:schemaLocation": f"{xsd} {NS_TFD} "
        "http://www.sat.gob.mx/sitio_internet/cfd/TimbreFiscalDigital/TimbreFiscalDigitalv11.xsd",
        "Version": version,
        "Serie": serie or None,
        "Folio": folio or None,
        "Fecha": _sello_cfdi(emitido),
        "Sello": sello,
        "NoCertificado": no_certificado,
        "Certificado": certificado,
        "FormaPago": forma_pago,
        "MetodoPago": metodo_pago,
        "SubTotal": subtotal,
        "Moneda": moneda,
        "Total": total,
        "TipoDeComprobante": "I",
        # Exportacion sólo existe a partir de la 4.0.
        "Exportacion": "01" if es_40 else None,
        "LugarExpedicion": lugar_expedicion,
    }

    receptor: dict[str, object | None] = {
        "Rfc": rfc_receptor,
        "Nombre": nombre_receptor,
        # Estos dos atributos nacieron con la 4.0.
        "DomicilioFiscalReceptor": lugar_expedicion if es_40 else None,
        "RegimenFiscalReceptor": regimen_receptor if es_40 else None,
        "UsoCFDI": uso_cfdi,
    }

    concepto: dict[str, object | None] = {
        "ClaveProdServ": "80141600",
        "Cantidad": "1",
        "ClaveUnidad": "E48",
        "Unidad": "Servicio",
        "Descripcion": descripcion,
        "ValorUnitario": subtotal,
        "Importe": subtotal,
        "ObjetoImp": "02" if es_40 else None,
    }

    traslado: dict[str, object | None] = {
        "Base": subtotal,
        "Impuesto": "002",
        "TipoFactor": "Tasa",
        "TasaOCuota": f"{TASA_IVA:f}",
        "Importe": impuestos,
    }

    timbre: dict[str, object | None] = {
        "xsi:schemaLocation": f"{NS_TFD} "
        "http://www.sat.gob.mx/sitio_internet/cfd/TimbreFiscalDigital/TimbreFiscalDigitalv11.xsd",
        "Version": "1.1",
        "UUID": uuid_normalizado,
        "FechaTimbrado": _sello_cfdi(timbrado),
        "RfcProvCertif": _RFC_PAC_FICTICIO,
        "SelloCFD": sello,
        "NoCertificadoSAT": no_certificado_sat,
        "SelloSAT": sello_sat,
    }

    emisor = {"Rfc": rfc_emisor, "Nombre": nombre_emisor, "RegimenFiscal": regimen_emisor}

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<cfdi:Comprobante {_atributos(comprobante)}>\n"
        f"  <cfdi:Emisor {_atributos(emisor)}/>\n"
        f"  <cfdi:Receptor {_atributos(receptor)}/>\n"
        "  <cfdi:Conceptos>\n"
        f"    <cfdi:Concepto {_atributos(concepto)}>\n"
        "      <cfdi:Impuestos>\n"
        "        <cfdi:Traslados>\n"
        f"          <cfdi:Traslado {_atributos(traslado)}/>\n"
        "        </cfdi:Traslados>\n"
        "      </cfdi:Impuestos>\n"
        "    </cfdi:Concepto>\n"
        "  </cfdi:Conceptos>\n"
        f'  <cfdi:Impuestos TotalImpuestosTrasladados="{impuestos}">\n'
        "    <cfdi:Traslados>\n"
        f"      <cfdi:Traslado {_atributos(traslado)}/>\n"
        "    </cfdi:Traslados>\n"
        "  </cfdi:Impuestos>\n"
        "  <cfdi:Complemento>\n"
        f"    <tfd:TimbreFiscalDigital {_atributos(timbre)}/>\n"
        "  </cfdi:Complemento>\n"
        "</cfdi:Comprobante>\n"
    )


# --------------------------------------------------------------------------------------
# Ejemplo ejecutable
# --------------------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from xml.etree import ElementTree

    _subtotal = 812_450                       # $8,124.50
    _impuestos = calcular_iva_centavos(_subtotal)
    _ejemplo = construir_cfdi_xml(
        uuid_str="3f9c1b52-7d64-4a18-9c0b-2e5a71d84c60",
        rfc_emisor="VMO150312QJ4",
        nombre_emisor="VIAJES MONTEALBAN SA DE CV",
        rfc_receptor="FMA240101AB1",
        serie="A",
        folio="1024",
        fecha_iso="2026-07-11",
        subtotal_centavos=_subtotal,
        impuestos_centavos=_impuestos,
        total_centavos=_subtotal + _impuestos,
        no_certificado="30001000000500003416",
    )

    # Prueba de humo: si el XML no estuviera bien formado, esto revienta.
    ElementTree.fromstring(_ejemplo.encode("utf-8"))
    print(_ejemplo, end="")
    print("ok · CFDI 4.0 de ejemplo bien formado", file=sys.stderr)
