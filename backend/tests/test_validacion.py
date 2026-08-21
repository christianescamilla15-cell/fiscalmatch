"""Pruebas de la validación fiscal de CFDI 4.0. Sin base de datos.

Los XML se arman con `construir_cfdi`, un helper local: las pruebas no deben
depender del seed ni de archivos en disco, porque entonces dejan de probar la
regla y empiezan a probar el dato de ejemplo.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.validacion import (
    CfdiParseado,
    ErrorParseoCfdi,
    ResultadoRegla,
    a_centavos,
    aprobado,
    parsear_cfdi,
    validar,
)

# --------------------------------------------------------------------------- #
# Datos de referencia
# --------------------------------------------------------------------------- #

RFC_RECEPTOR = "FMA240101AB1"          # la empresa que recibe las facturas de comisión
RFC_AGENCIA = "AVI850101QW7"           # agencia dada de alta en el padrón
UUID_VALIDO = "A1B2C3D4-1234-4A5B-8C9D-0123456789AB"
NO_CERTIFICADO = "30001000000400000001"  # 20 dígitos
SELLO = "c2VsbG9EZVBydWViYQ=="
CERTIFICADO = "Y2VydGlmaWNhZG9EZVBydWViYQ=="

AGENCIAS_REGISTRADAS = {RFC_AGENCIA}
SIN_UUIDS_PREVIOS: set[str] = set()


def construir_cfdi(
    *,
    version: str = "4.0",
    serie: str | None = "A",
    folio: str | None = "1024",
    fecha: str = "2026-08-15T12:30:00",
    subtotal: str = "1000.00",
    impuestos: str | None = "160.00",
    total: str = "1160.00",
    moneda: str | None = "MXN",
    no_certificado: str | None = NO_CERTIFICADO,
    sello: str | None = SELLO,
    certificado: str | None = CERTIFICADO,
    rfc_emisor: str = RFC_AGENCIA,
    rfc_receptor: str = RFC_RECEPTOR,
    uuid: str | None = UUID_VALIDO,
) -> str:
    """Arma un CFDI 4.0 de comisión. Cada parámetro permite romper UNA sola cosa.

    `None` en un atributo opcional lo omite del XML; `None` en `uuid` omite el
    complemento del timbre completo.
    """

    def atributo(nombre: str, valor: str | None) -> str:
        return "" if valor is None else f' {nombre}="{valor}"'

    encabezado = (
        "<cfdi:Comprobante"
        ' xmlns:cfdi="http://www.sat.gob.mx/cfd/4"'
        ' xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"'
        f'{atributo("Version", version)}'
        f'{atributo("Serie", serie)}'
        f'{atributo("Folio", folio)}'
        f'{atributo("Fecha", fecha)}'
        f'{atributo("Sello", sello)}'
        f'{atributo("NoCertificado", no_certificado)}'
        f'{atributo("Certificado", certificado)}'
        f'{atributo("SubTotal", subtotal)}'
        f'{atributo("Moneda", moneda)}'
        f'{atributo("Total", total)}'
        ' TipoDeComprobante="I" Exportacion="01" MetodoPago="PUE"'
        ' FormaPago="03" LugarExpedicion="01000">'
    )
    cuerpo = [
        f'<cfdi:Emisor Rfc="{rfc_emisor}" Nombre="Agencia de Viajes Demo"'
        ' RegimenFiscal="601"/>',
        f'<cfdi:Receptor Rfc="{rfc_receptor}" Nombre="Receptor Demo"'
        ' DomicilioFiscalReceptor="01000" RegimenFiscalReceptor="601"'
        ' UsoCFDI="G03"/>',
        "<cfdi:Conceptos>"
        '<cfdi:Concepto ClaveProdServ="80131500" Cantidad="1" ClaveUnidad="E48"'
        ' Descripcion="Comisión por venta de boletos" ObjetoImp="02"'
        f' ValorUnitario="{subtotal}" Importe="{subtotal}"/>'
        "</cfdi:Conceptos>",
    ]
    if impuestos is not None:
        cuerpo.append(f'<cfdi:Impuestos TotalImpuestosTrasladados="{impuestos}"/>')
    if uuid is not None:
        cuerpo.append(
            "<cfdi:Complemento>"
            f'<tfd:TimbreFiscalDigital Version="1.1" UUID="{uuid}"'
            f' FechaTimbrado="{fecha}" RfcProvCertif="SAT970701NN3"/>'
            "</cfdi:Complemento>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        + encabezado
        + "".join(cuerpo)
        + "</cfdi:Comprobante>"
    )


def evaluar(xml_texto: str, *, uuids_existentes: set[str] | None = None) -> dict[str, ResultadoRegla]:
    """Parsea y valida, devolviendo los resultados indexados por identificador de regla."""
    parseado = parsear_cfdi(xml_texto)
    resultados = validar(
        parseado,
        xml_texto=xml_texto,
        rfc_receptor_esperado=RFC_RECEPTOR,
        rfcs_agencias_registradas=AGENCIAS_REGISTRADAS,
        uuids_existentes=uuids_existentes if uuids_existentes is not None else SIN_UUIDS_PREVIOS,
    )
    return {resultado.regla: resultado for resultado in resultados}


def solo_falla(reglas: dict[str, ResultadoRegla], regla: str) -> None:
    """Afirma que falló exactamente la regla indicada y ninguna otra.

    Esto es lo que evita el falso positivo clásico: una prueba que pasa porque
    el CFDI está roto por otro lado.
    """
    fallidas = sorted(nombre for nombre, r in reglas.items() if not r.aprobada)
    assert fallidas == [regla], f"Se esperaba que sólo fallara {regla}, fallaron {fallidas}"
    assert reglas[regla].detalle, "Toda regla fallida debe explicar por qué"


# --------------------------------------------------------------------------- #
# El camino feliz
# --------------------------------------------------------------------------- #


def test_cfdi_valido_pasa_todas_las_reglas() -> None:
    reglas = evaluar(construir_cfdi())
    assert all(r.aprobada for r in reglas.values()), [
        (r.regla, r.detalle) for r in reglas.values() if not r.aprobada
    ]
    assert aprobado(list(reglas.values())) is True


def test_parseo_extrae_los_campos_del_comprobante() -> None:
    parseado = parsear_cfdi(construir_cfdi())
    assert parseado == CfdiParseado(
        uuid=UUID_VALIDO,
        rfc_emisor=RFC_AGENCIA,
        rfc_receptor=RFC_RECEPTOR,
        serie="A",
        folio="1024",
        fecha_emision=date(2026, 8, 15),
        subtotal_centavos=100_000,
        impuestos_centavos=16_000,
        total_centavos=116_000,
        moneda="MXN",
        no_certificado=NO_CERTIFICADO,
    )


def test_serie_y_folio_ausentes_quedan_en_none() -> None:
    parseado = parsear_cfdi(construir_cfdi(serie=None, folio=None))
    assert parseado.serie is None
    assert parseado.folio is None


def test_impuestos_ausentes_valen_cero() -> None:
    # Un CFDI de comisión exento: sin nodo cfdi:Impuestos, el total es el subtotal.
    parseado = parsear_cfdi(construir_cfdi(impuestos=None, subtotal="500.00", total="500.00"))
    assert parseado.impuestos_centavos == 0
    assert parseado.subtotal_centavos == parseado.total_centavos == 50_000


def test_se_evaluan_las_once_reglas_sin_cortar_al_primer_fallo() -> None:
    # CFDI roto por TODOS lados: aun así deben venir los 11 veredictos.
    xml_texto = construir_cfdi(
        version="3.3",
        uuid=None,
        rfc_emisor="XXX",
        rfc_receptor="OTR990101XY0",
        subtotal="100.00",
        impuestos="16.00",
        total="999.00",
        moneda="USD",
        sello=None,
        certificado=None,
        no_certificado="123",
    )
    reglas = evaluar(xml_texto)
    assert sorted(reglas) == sorted(
        [
            "CERTIFICADO_VIGENTE",
            "EMISOR_REGISTRADO",
            "ESTRUCTURA_CFDI40",
            "IMPORTE_POSITIVO",
            "MONEDA_MXN",
            "RFC_EMISOR_VALIDO",
            "RFC_RECEPTOR_ESPERADO",
            "SELLO_PRESENTE",
            "TOTALES_COHERENTES",
            "UUID_NO_DUPLICADO",
            "UUID_PRESENTE",
        ]
    )
    # Sólo IMPORTE_POSITIVO y UUID_NO_DUPLICADO sobreviven a este desastre.
    assert reglas["IMPORTE_POSITIVO"].aprobada is True
    assert reglas["UUID_NO_DUPLICADO"].aprobada is True
    assert aprobado(list(reglas.values())) is False


# --------------------------------------------------------------------------- #
# Una prueba por regla que falla
# --------------------------------------------------------------------------- #


def test_estructura_cfdi40_falla_con_version_anterior() -> None:
    reglas = evaluar(construir_cfdi(version="3.3"))
    solo_falla(reglas, "ESTRUCTURA_CFDI40")
    assert "3.3" in reglas["ESTRUCTURA_CFDI40"].detalle


def test_uuid_presente_falla_sin_timbre() -> None:
    reglas = evaluar(construir_cfdi(uuid=None))
    solo_falla(reglas, "UUID_PRESENTE")


def test_uuid_presente_falla_con_formato_incorrecto() -> None:
    reglas = evaluar(construir_cfdi(uuid="1234-5678"))
    solo_falla(reglas, "UUID_PRESENTE")
    assert "8-4-4-4-12" in reglas["UUID_PRESENTE"].detalle


def test_uuid_no_duplicado_falla_si_ya_fue_cargado() -> None:
    reglas = evaluar(construir_cfdi(), uuids_existentes={UUID_VALIDO})
    solo_falla(reglas, "UUID_NO_DUPLICADO")


def test_uuid_duplicado_se_detecta_sin_importar_mayusculas() -> None:
    reglas = evaluar(construir_cfdi(), uuids_existentes={UUID_VALIDO.lower()})
    solo_falla(reglas, "UUID_NO_DUPLICADO")


def test_rfc_emisor_valido_falla_con_rfc_malformado() -> None:
    reglas = evaluar(construir_cfdi(rfc_emisor="AV1850101QW7"))  # dígito donde va letra
    # El RFC inválido tampoco está en el padrón: ambas reglas deben fallar.
    assert reglas["RFC_EMISOR_VALIDO"].aprobada is False
    assert reglas["EMISOR_REGISTRADO"].aprobada is False


def test_rfc_receptor_esperado_falla_si_la_factura_es_para_otro() -> None:
    reglas = evaluar(construir_cfdi(rfc_receptor="OTR990101XY0"))
    solo_falla(reglas, "RFC_RECEPTOR_ESPERADO")
    assert RFC_RECEPTOR in reglas["RFC_RECEPTOR_ESPERADO"].detalle


def test_emisor_registrado_falla_con_agencia_fuera_del_padron() -> None:
    # RFC bien formado pero no dado de alta: falla SÓLO EMISOR_REGISTRADO.
    reglas = evaluar(construir_cfdi(rfc_emisor="ZZZ010101AA9"))
    solo_falla(reglas, "EMISOR_REGISTRADO")


def test_totales_coherentes_falla_cuando_no_suma() -> None:
    reglas = evaluar(construir_cfdi(subtotal="1000.00", impuestos="160.00", total="1160.01"))
    solo_falla(reglas, "TOTALES_COHERENTES")
    assert "1 centavos" in reglas["TOTALES_COHERENTES"].detalle


def test_moneda_mxn_falla_con_divisa_extranjera() -> None:
    reglas = evaluar(construir_cfdi(moneda="USD"))
    solo_falla(reglas, "MONEDA_MXN")


def test_importe_positivo_falla_con_total_en_cero() -> None:
    reglas = evaluar(construir_cfdi(subtotal="0.00", impuestos="0.00", total="0.00"))
    solo_falla(reglas, "IMPORTE_POSITIVO")


def test_importe_positivo_falla_con_total_negativo() -> None:
    reglas = evaluar(construir_cfdi(subtotal="-100.00", impuestos="0.00", total="-100.00"))
    solo_falla(reglas, "IMPORTE_POSITIVO")


def test_sello_presente_falla_sin_sello() -> None:
    reglas = evaluar(construir_cfdi(sello=None))
    solo_falla(reglas, "SELLO_PRESENTE")
    assert "Sello" in reglas["SELLO_PRESENTE"].detalle


def test_sello_presente_falla_con_certificado_vacio() -> None:
    reglas = evaluar(construir_cfdi(certificado=""))
    solo_falla(reglas, "SELLO_PRESENTE")
    assert "Certificado" in reglas["SELLO_PRESENTE"].detalle


def test_certificado_vigente_falla_si_no_trae_20_digitos() -> None:
    reglas = evaluar(construir_cfdi(no_certificado="3000100000040000"))  # 16 dígitos
    solo_falla(reglas, "CERTIFICADO_VIGENTE")


def test_certificado_vigente_declara_que_es_una_simulacion() -> None:
    # El detalle debe advertir que la vigencia REAL exige consultar al SAT:
    # un aprobado aquí no es un certificado verificado.
    reglas = evaluar(construir_cfdi())
    detalle = reglas["CERTIFICADO_VIGENTE"].detalle
    assert "SIMULACIÓN" in detalle
    assert "SAT" in detalle


# --------------------------------------------------------------------------- #
# Dinero: centavos enteros, jamás float
# --------------------------------------------------------------------------- #


def test_importes_decimales_se_convierten_a_centavos_exactos() -> None:
    assert a_centavos("1234.56", campo="Total") == 123_456
    assert a_centavos("0.1", campo="Total") == 10
    assert a_centavos("0.2", campo="Total") == 20
    assert a_centavos("0.3", campo="Total") == 30
    # El clásico error de binario: 0.1 + 0.2 != 0.3 en float. En centavos, sí.
    assert a_centavos("0.1", campo="a") + a_centavos("0.2", campo="b") == a_centavos("0.3", campo="c")
    assert 0.1 + 0.2 != 0.3  # la razón por la que este módulo no usa float


def test_conversion_a_centavos_no_pasa_por_float() -> None:
    # 8,675,309.55 no es representable exactamente en binario; con float el
    # redondeo se pierde. Con Decimal el resultado es exacto.
    assert a_centavos("8675309.55", campo="Total") == 867_530_955
    assert a_centavos(str(Decimal("19.99") * 3), campo="Total") == 5_997


def test_conversion_a_centavos_redondea_medio_hacia_arriba() -> None:
    # ROUND_HALF_UP: la tercera decimal no debe truncarse en silencio.
    assert a_centavos("1.005", campo="Total") == 101
    assert a_centavos("1.004", campo="Total") == 100
    assert a_centavos("-1.005", campo="Total") == -101


def test_cfdi_con_decimales_conserva_los_centavos() -> None:
    parseado = parsear_cfdi(
        construir_cfdi(subtotal="1234.56", impuestos="197.53", total="1432.09")
    )
    assert parseado.subtotal_centavos == 123_456
    assert parseado.impuestos_centavos == 19_753
    assert parseado.total_centavos == 143_209
    assert parseado.subtotal_centavos + parseado.impuestos_centavos == parseado.total_centavos


# --------------------------------------------------------------------------- #
# Errores de parseo (el XML es ilegible, no es un hallazgo fiscal)
# --------------------------------------------------------------------------- #


def test_parseo_falla_con_xml_mal_formado() -> None:
    with pytest.raises(ErrorParseoCfdi, match="mal formado"):
        parsear_cfdi("<cfdi:Comprobante><sin cerrar>")


def test_parseo_falla_con_xml_vacio() -> None:
    with pytest.raises(ErrorParseoCfdi, match="vacío"):
        parsear_cfdi("   ")


def test_parseo_falla_si_la_raiz_no_es_comprobante() -> None:
    with pytest.raises(ErrorParseoCfdi, match="Comprobante"):
        parsear_cfdi('<?xml version="1.0"?><Factura Version="4.0"/>')


def test_parseo_falla_sin_nodo_emisor() -> None:
    xml_texto = construir_cfdi().replace(
        f'<cfdi:Emisor Rfc="{RFC_AGENCIA}" Nombre="Agencia de Viajes Demo"'
        ' RegimenFiscal="601"/>',
        "",
    )
    with pytest.raises(ErrorParseoCfdi, match="cfdi:Emisor"):
        parsear_cfdi(xml_texto)


def test_parseo_falla_con_importe_no_numerico() -> None:
    with pytest.raises(ErrorParseoCfdi, match="Total"):
        parsear_cfdi(construir_cfdi(total="mil ciento sesenta"))


def test_parseo_falla_con_fecha_invalida() -> None:
    with pytest.raises(ErrorParseoCfdi, match="Fecha"):
        parsear_cfdi(construir_cfdi(fecha="15/08/2026"))


# --------------------------------------------------------------------------- #
# Helper `aprobado`
# --------------------------------------------------------------------------- #


def test_aprobado_exige_que_todas_pasen() -> None:
    todas_bien = [
        ResultadoRegla(regla="A", aprobada=True, detalle=""),
        ResultadoRegla(regla="B", aprobada=True, detalle=""),
    ]
    una_mal = todas_bien + [ResultadoRegla(regla="C", aprobada=False, detalle="no")]
    assert aprobado(todas_bien) is True
    assert aprobado(una_mal) is False


def test_aprobado_no_falla_abierto_con_lista_vacia() -> None:
    # "No se evaluó nada" NO es "pasó la validación".
    assert aprobado([]) is False
