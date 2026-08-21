"""Validación fiscal de CFDI 4.0 para FiscalMatch.

Dos responsabilidades separadas a propósito:

1. `parsear_cfdi` — **fidelidad**: extrae del XML lo que el documento dice, sin
   corregirlo ni normalizarlo. Sólo falla (con `ErrorParseoCfdi`) cuando el
   documento es ilegible: XML mal formado, nodo raíz equivocado o un importe
   que no es un decimal.
2. `validar` — **juicio**: aplica las reglas fiscales sobre lo ya extraído y
   devuelve un veredicto POR REGLA. Nunca lanza: un CFDI malo no es un error
   del programa, es un hallazgo que hay que registrar en la bitácora.

Todo importe se convierte a CENTAVOS enteros con `Decimal`. Nunca `float`:
en materia fiscal un centavo de diferencia es un hallazgo, y `0.1 + 0.2` en
binario no da `0.3`.

Sin dependencias externas: `xml.etree.ElementTree` de la biblioteca estándar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------- #
# Constantes del estándar
# --------------------------------------------------------------------------- #

NS_CFDI = "http://www.sat.gob.mx/cfd/4"
NS_TFD = "http://www.sat.gob.mx/TimbreFiscalDigital"

#: Mapa de espacios de nombres oficial del CFDI 4.0.
NS: dict[str, str] = {"cfdi": NS_CFDI, "tfd": NS_TFD}

VERSION_ESPERADA = "4.0"
MONEDA_ESPERADA = "MXN"
DIGITOS_NO_CERTIFICADO = 20

# UUID del timbre: 8-4-4-4-12 hexadecimal.
RE_UUID = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
# RFC mexicano: 3 letras (moral) o 4 (física) + fecha AAMMDD + homoclave.
RE_RFC = re.compile(r"^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$")
# Número de certificado del SAT: exactamente 20 dígitos.
RE_NO_CERTIFICADO = re.compile(r"^[0-9]{20}$")

#: Identificadores de las reglas, en el orden en que se evalúan y se reportan.
REGLAS: tuple[str, ...] = (
    "ESTRUCTURA_CFDI40",
    "UUID_PRESENTE",
    "UUID_NO_DUPLICADO",
    "RFC_EMISOR_VALIDO",
    "RFC_RECEPTOR_ESPERADO",
    "EMISOR_REGISTRADO",
    "TOTALES_COHERENTES",
    "MONEDA_MXN",
    "IMPORTE_POSITIVO",
    "SELLO_PRESENTE",
    "CERTIFICADO_VIGENTE",
)


class ErrorParseoCfdi(ValueError):
    """El XML no pudo leerse como un CFDI. El mensaje dice exactamente qué falló."""


# --------------------------------------------------------------------------- #
# Estructuras de datos
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CfdiParseado:
    """Datos extraídos del XML tal como vienen. Los importes ya en centavos."""

    uuid: str
    rfc_emisor: str
    rfc_receptor: str
    serie: str | None
    folio: str | None
    fecha_emision: date
    subtotal_centavos: int
    impuestos_centavos: int
    total_centavos: int
    moneda: str
    no_certificado: str


@dataclass(frozen=True, slots=True)
class ResultadoRegla:
    """Veredicto de UNA regla de validación fiscal."""

    regla: str
    aprobada: bool
    detalle: str


# --------------------------------------------------------------------------- #
# Utilidades de parseo
# --------------------------------------------------------------------------- #


def _nombre_local(etiqueta: str) -> str:
    """Devuelve el nombre del elemento sin su espacio de nombres."""
    return etiqueta.rsplit("}", 1)[-1]


def _raiz(xml_texto: str) -> ET.Element:
    """Devuelve el nodo `cfdi:Comprobante` o lanza `ErrorParseoCfdi`."""
    if not (xml_texto or "").strip():
        raise ErrorParseoCfdi("El XML viene vacío.")
    try:
        raiz = ET.fromstring(xml_texto)
    except ET.ParseError as exc:
        raise ErrorParseoCfdi(f"El XML está mal formado: {exc}.") from exc
    if _nombre_local(raiz.tag) != "Comprobante":
        raise ErrorParseoCfdi(
            f"El nodo raíz es '{_nombre_local(raiz.tag)}' y se esperaba 'cfdi:Comprobante'."
        )
    return raiz


def _hijo(padre: ET.Element, nombre: str) -> ET.Element | None:
    """Hijo directo por nombre local (tolerante al espacio de nombres declarado)."""
    encontrado = padre.find(f"cfdi:{nombre}", NS)
    if encontrado is not None:
        return encontrado
    for hijo in padre:
        if _nombre_local(hijo.tag) == nombre:
            return hijo
    return None


def _descendiente(raiz: ET.Element, nombre: str) -> ET.Element | None:
    """Primer descendiente por nombre local (el timbre vive dentro de Complemento)."""
    for elemento in raiz.iter():
        if _nombre_local(elemento.tag) == nombre:
            return elemento
    return None


def _atributo(elemento: ET.Element, nombre: str) -> str:
    """Atributo ya recortado; cadena vacía si no está."""
    return (elemento.get(nombre) or "").strip()


def _opcional(valor: str) -> str | None:
    """Serie y Folio son opcionales en el CFDI: vacío se guarda como NULL."""
    return valor or None


def a_centavos(valor: str, *, campo: str) -> int:
    """Convierte un importe decimal en texto ("1234.56") a centavos enteros.

    Usa `Decimal` de principio a fin. Jamás `float`: `float("0.1") + float("0.2")`
    da 0.30000000000000004 y eso, acumulado sobre un periodo, es una diferencia
    que nadie puede explicar en una auditoría.
    """
    texto = (valor or "").strip()
    if not texto:
        raise ErrorParseoCfdi(f"El atributo '{campo}' viene vacío y es obligatorio.")
    try:
        importe = Decimal(texto)
    except InvalidOperation as exc:
        raise ErrorParseoCfdi(
            f"El atributo '{campo}' no es un importe decimal válido: '{texto}'."
        ) from exc
    if not importe.is_finite():
        raise ErrorParseoCfdi(f"El atributo '{campo}' no es un importe finito: '{texto}'.")
    return int(importe.scaleb(2).to_integral_value(ROUND_HALF_UP))


def _a_fecha(valor: str) -> date:
    """La Fecha del CFDI es ISO 8601 ("2026-08-15T12:30:00"); nos quedamos con el día."""
    texto = (valor or "").strip()
    if not texto:
        raise ErrorParseoCfdi("El atributo 'Fecha' viene vacío y es obligatorio.")
    normalizada = texto[:-1] + "+00:00" if texto.endswith("Z") else texto
    try:
        return datetime.fromisoformat(normalizada).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(texto[:10])
    except ValueError as exc:
        raise ErrorParseoCfdi(
            f"El atributo 'Fecha' no es una fecha ISO 8601 válida: '{texto}'."
        ) from exc


# --------------------------------------------------------------------------- #
# Parseo
# --------------------------------------------------------------------------- #


def parsear_cfdi(xml_texto: str) -> CfdiParseado:
    """Extrae del XML los datos del CFDI. No juzga: eso le toca a `validar`.

    Lo que **sí** hace fallar el parseo (con `ErrorParseoCfdi`): XML mal formado,
    nodo raíz distinto de `cfdi:Comprobante`, falta de `cfdi:Emisor`/`cfdi:Receptor`,
    o un importe/fecha ilegible.

    Lo que **no** hace fallar el parseo (lo reporta después una regla): versión
    distinta de 4.0, ausencia de timbre, RFC con formato inválido, moneda
    extranjera o números de certificado raros.
    """
    raiz = _raiz(xml_texto)

    emisor = _hijo(raiz, "Emisor")
    if emisor is None:
        raise ErrorParseoCfdi("El comprobante no trae el nodo 'cfdi:Emisor'.")
    receptor = _hijo(raiz, "Receptor")
    if receptor is None:
        raise ErrorParseoCfdi("El comprobante no trae el nodo 'cfdi:Receptor'.")

    # El timbre puede faltar (CFDI sin timbrar): se reporta como regla, no como error.
    timbre = _descendiente(raiz, "TimbreFiscalDigital")
    uuid = _atributo(timbre, "UUID") if timbre is not None else ""

    # Impuestos trasladados: nodo hijo directo del Comprobante. Si no está, 0.
    nodo_impuestos = _hijo(raiz, "Impuestos")
    if nodo_impuestos is None or not _atributo(nodo_impuestos, "TotalImpuestosTrasladados"):
        impuestos_centavos = 0
    else:
        impuestos_centavos = a_centavos(
            _atributo(nodo_impuestos, "TotalImpuestosTrasladados"),
            campo="Impuestos@TotalImpuestosTrasladados",
        )

    return CfdiParseado(
        uuid=uuid,
        rfc_emisor=_atributo(emisor, "Rfc"),
        rfc_receptor=_atributo(receptor, "Rfc"),
        serie=_opcional(_atributo(raiz, "Serie")),
        folio=_opcional(_atributo(raiz, "Folio")),
        fecha_emision=_a_fecha(_atributo(raiz, "Fecha")),
        subtotal_centavos=a_centavos(_atributo(raiz, "SubTotal"), campo="SubTotal"),
        impuestos_centavos=impuestos_centavos,
        total_centavos=a_centavos(_atributo(raiz, "Total"), campo="Total"),
        moneda=_atributo(raiz, "Moneda"),
        no_certificado=_atributo(raiz, "NoCertificado"),
    )


# --------------------------------------------------------------------------- #
# Validación
# --------------------------------------------------------------------------- #


def _pesos(centavos: int) -> str:
    """Formatea centavos como pesos para el detalle legible del hallazgo."""
    signo = "-" if centavos < 0 else ""
    entero, resto = divmod(abs(centavos), 100)
    return f"{signo}{entero:,}.{resto:02d}"


def _version_del_xml(xml_texto: str) -> str:
    """Versión declarada en el Comprobante; cadena vacía si el XML no se pudo leer."""
    try:
        return _atributo(_raiz(xml_texto), "Version")
    except ErrorParseoCfdi:
        return ""


def validar(
    parseado: CfdiParseado,
    *,
    xml_texto: str,
    rfc_receptor_esperado: str,
    rfcs_agencias_registradas: set[str],
    uuids_existentes: set[str],
) -> list[ResultadoRegla]:
    """Evalúa TODAS las reglas fiscales y devuelve un resultado por cada una.

    No corta al primer fallo: el analista necesita ver el cuadro completo del
    CFDI en un solo paso, no descubrir un problema por reintento.

    Los RFC y el UUID se comparan sin distinguir mayúsculas (el SAT los emite en
    mayúsculas, pero un catálogo cargado a mano no siempre respeta eso).
    """
    resultados: list[ResultadoRegla] = []

    # El sello y el certificado viven en el XML crudo, no en el modelo parseado.
    try:
        raiz: ET.Element | None = _raiz(xml_texto)
        motivo_ilegible = ""
    except ErrorParseoCfdi as exc:
        raiz = None
        motivo_ilegible = str(exc)

    # --- ESTRUCTURA_CFDI40 -------------------------------------------------- #
    version = _version_del_xml(xml_texto)
    resultados.append(
        ResultadoRegla(
            regla="ESTRUCTURA_CFDI40",
            aprobada=version == VERSION_ESPERADA,
            detalle=(
                f"Version={VERSION_ESPERADA}."
                if version == VERSION_ESPERADA
                else f"Se esperaba Version='{VERSION_ESPERADA}' y se encontró "
                f"'{version or '(ausente)'}'. {motivo_ilegible}".strip()
            ),
        )
    )

    # --- UUID_PRESENTE ------------------------------------------------------ #
    uuid = parseado.uuid.strip()
    uuid_bien_formado = bool(RE_UUID.match(uuid))
    resultados.append(
        ResultadoRegla(
            regla="UUID_PRESENTE",
            aprobada=uuid_bien_formado,
            detalle=(
                f"UUID del timbre fiscal: {uuid}."
                if uuid_bien_formado
                else (
                    "El comprobante no trae UUID en 'tfd:TimbreFiscalDigital' (¿sin timbrar?)."
                    if not uuid
                    else f"El UUID '{uuid}' no cumple el formato 8-4-4-4-12 hexadecimal."
                )
            ),
        )
    )

    # --- UUID_NO_DUPLICADO -------------------------------------------------- #
    conocidos = {existente.strip().upper() for existente in uuids_existentes}
    duplicado = bool(uuid) and uuid.upper() in conocidos
    resultados.append(
        ResultadoRegla(
            regla="UUID_NO_DUPLICADO",
            aprobada=not duplicado,
            detalle=(
                f"El UUID '{uuid}' ya fue cargado antes: el CFDI está duplicado."
                if duplicado
                else "El UUID no había sido recibido antes."
            ),
        )
    )

    # --- RFC_EMISOR_VALIDO -------------------------------------------------- #
    rfc_emisor = parseado.rfc_emisor.strip()
    emisor_valido = bool(RE_RFC.match(rfc_emisor))
    resultados.append(
        ResultadoRegla(
            regla="RFC_EMISOR_VALIDO",
            aprobada=emisor_valido,
            detalle=(
                f"RFC del emisor: {rfc_emisor}."
                if emisor_valido
                else f"El RFC del emisor '{rfc_emisor or '(ausente)'}' no cumple el formato "
                "de persona moral (3 letras) ni física (4 letras) + AAMMDD + homoclave."
            ),
        )
    )

    # --- RFC_RECEPTOR_ESPERADO ---------------------------------------------- #
    rfc_receptor = parseado.rfc_receptor.strip()
    receptor_correcto = rfc_receptor.upper() == rfc_receptor_esperado.strip().upper()
    resultados.append(
        ResultadoRegla(
            regla="RFC_RECEPTOR_ESPERADO",
            aprobada=receptor_correcto,
            detalle=(
                f"El comprobante está emitido a {rfc_receptor_esperado}."
                if receptor_correcto
                else f"El CFDI está emitido a '{rfc_receptor or '(ausente)'}' y debía estar "
                f"a nombre de '{rfc_receptor_esperado}'."
            ),
        )
    )

    # --- EMISOR_REGISTRADO -------------------------------------------------- #
    registradas = {rfc.strip().upper() for rfc in rfcs_agencias_registradas}
    registrado = bool(rfc_emisor) and rfc_emisor.upper() in registradas
    resultados.append(
        ResultadoRegla(
            regla="EMISOR_REGISTRADO",
            aprobada=registrado,
            detalle=(
                f"La agencia {rfc_emisor} está dada de alta y activa."
                if registrado
                else f"El emisor '{rfc_emisor or '(ausente)'}' no corresponde a ninguna agencia "
                "registrada en el padrón."
            ),
        )
    )

    # --- TOTALES_COHERENTES ------------------------------------------------- #
    suma = parseado.subtotal_centavos + parseado.impuestos_centavos
    coherentes = suma == parseado.total_centavos
    resultados.append(
        ResultadoRegla(
            regla="TOTALES_COHERENTES",
            aprobada=coherentes,
            detalle=(
                f"SubTotal {_pesos(parseado.subtotal_centavos)} + impuestos "
                f"{_pesos(parseado.impuestos_centavos)} = Total {_pesos(parseado.total_centavos)}."
                if coherentes
                else f"SubTotal {_pesos(parseado.subtotal_centavos)} + impuestos "
                f"{_pesos(parseado.impuestos_centavos)} = {_pesos(suma)}, pero el Total "
                f"declarado es {_pesos(parseado.total_centavos)} "
                f"(diferencia de {parseado.total_centavos - suma} centavos)."
            ),
        )
    )

    # --- MONEDA_MXN --------------------------------------------------------- #
    moneda = parseado.moneda.strip().upper()
    resultados.append(
        ResultadoRegla(
            regla="MONEDA_MXN",
            aprobada=moneda == MONEDA_ESPERADA,
            detalle=(
                "El comprobante está en pesos mexicanos."
                if moneda == MONEDA_ESPERADA
                else f"La moneda es '{moneda or '(ausente)'}' y las comisiones se liquidan "
                f"únicamente en {MONEDA_ESPERADA}."
            ),
        )
    )

    # --- IMPORTE_POSITIVO --------------------------------------------------- #
    positivo = parseado.total_centavos > 0
    resultados.append(
        ResultadoRegla(
            regla="IMPORTE_POSITIVO",
            aprobada=positivo,
            detalle=(
                f"Total del comprobante: {_pesos(parseado.total_centavos)}."
                if positivo
                else f"El Total es {_pesos(parseado.total_centavos)} y debe ser mayor a cero."
            ),
        )
    )

    # --- SELLO_PRESENTE ----------------------------------------------------- #
    sello = _atributo(raiz, "Sello") if raiz is not None else ""
    certificado = _atributo(raiz, "Certificado") if raiz is not None else ""
    sellado = bool(sello) and bool(certificado)
    if sellado:
        detalle_sello = "El comprobante trae Sello y Certificado."
    elif raiz is None:
        detalle_sello = f"No se pudo leer el XML para revisar el sello. {motivo_ilegible}"
    else:
        faltantes = [
            nombre
            for nombre, valor in (("Sello", sello), ("Certificado", certificado))
            if not valor
        ]
        detalle_sello = f"El Comprobante no trae {' ni '.join(faltantes)}."
    resultados.append(
        ResultadoRegla(regla="SELLO_PRESENTE", aprobada=sellado, detalle=detalle_sello)
    )

    # --- CERTIFICADO_VIGENTE ------------------------------------------------ #
    # SIMULACIÓN: aquí sólo se comprueba la FORMA del número de certificado
    # (20 dígitos). La vigencia real —que el certificado exista, no esté revocado
    # y cubriera la fecha de emisión— sólo puede resolverla una consulta al SAT,
    # que este módulo deliberadamente no hace para no depender de la red.
    no_certificado = parseado.no_certificado.strip()
    forma_correcta = bool(RE_NO_CERTIFICADO.match(no_certificado))
    resultados.append(
        ResultadoRegla(
            regla="CERTIFICADO_VIGENTE",
            aprobada=forma_correcta,
            detalle=(
                f"NoCertificado {no_certificado} tiene los {DIGITOS_NO_CERTIFICADO} dígitos "
                "esperados. SIMULACIÓN: la vigencia real exige consultar al SAT; aquí sólo se "
                "verifica el formato."
                if forma_correcta
                else f"El NoCertificado '{no_certificado or '(ausente)'}' no tiene "
                f"{DIGITOS_NO_CERTIFICADO} dígitos. SIMULACIÓN: la vigencia real exige "
                "consultar al SAT; aquí sólo se verifica el formato."
            ),
        )
    )

    return resultados


def aprobado(resultados: list[ResultadoRegla]) -> bool:
    """True sólo si TODAS las reglas quedaron aprobadas.

    Una lista vacía devuelve False a propósito: "no se evaluó nada" no es
    "pasó la validación". Fallar abierto en un control fiscal es un hallazgo.
    """
    return bool(resultados) and all(resultado.aprobada for resultado in resultados)
