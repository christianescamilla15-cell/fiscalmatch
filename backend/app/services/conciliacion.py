"""Motor de conciliación CFDI ↔ reporte de venta.

Dos capas bien separadas:

* `conciliar()` — función PURA: sin base de datos, sin FastAPI, sin reloj.
  Recibe números y devuelve un veredicto. Es la que se prueba a fondo.
* `conciliar_cfdi()` / `resolver_manual()` — orquestación sobre una `Session`.
  Consultan, persisten y mueven el estado del CFDI, pero NO hacen commit:
  la transacción la cierra quien llama.

REGLA TRANSVERSAL: todo importe viaja en CENTAVOS enteros. Nunca float.
La tolerancia porcentual llega de la configuración como número, pero se
convierte a `Decimal` por su representación decimal ("0.005") antes de
multiplicar, y el producto se TRUNCA a centavos (ROUND_DOWN): el umbral
nunca es más generoso que el porcentaje exacto.

Máquina de estados del CFDI (ver docs/reglas.md, sección «Estados»):

    carga ──▶ VALIDADO ──conciliar──▶ CONCILIADO (MATCH)
         └──▶ RECHAZADO               EN_EXCEPCION (PARCIAL / NO_MATCH)
    EN_EXCEPCION ──resolver(aceptar)──▶ CONCILIADO
    EN_EXCEPCION ──resolver(no aceptar)──▶ EN_EXCEPCION
    VALIDADO / CONCILIADO / EN_EXCEPCION ──conciliar──▶ (re-conciliación del motor)

RECHAZADO es terminal (la evidencia se archiva, no se concilia) y sólo una
excepción abierta se resuelve a mano: cualquier otra combinación lanza
`TransicionInvalida`.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Cfdi,
    Conciliacion,
    EstadoCfdi,
    MetodoMatch,
    ReporteVenta,
    ResultadoMatch,
    utcnow,
)

# El campo `motivo` de la tabla admite 300 caracteres.
LARGO_MAX_MOTIVO = 300

#: Estados desde los que cada operación puede partir. Lo que no está aquí es
#: una transición inválida.
ORIGENES_PERMITIDOS: dict[str, frozenset[EstadoCfdi]] = {
    # El motor concilia lo validado y puede re-conciliar lo ya dictaminado
    # (p. ej. cuando llega tarde el reporte de venta del periodo).
    "conciliar": frozenset({EstadoCfdi.VALIDADO, EstadoCfdi.CONCILIADO, EstadoCfdi.EN_EXCEPCION}),
    # El analista sólo resuelve excepciones abiertas: un CFDI conciliado no se
    # «des-concilia» a mano ni se resuelve lo que nunca pasó la validación.
    "resolver": frozenset({EstadoCfdi.EN_EXCEPCION}),
}


class TransicionInvalida(ValueError):
    """El CFDI no está en un estado desde el que se permita la operación pedida."""


def exigir_estado(cfdi: Cfdi, operacion: str) -> None:
    """Lanza `TransicionInvalida` si `operacion` no puede partir del estado actual del CFDI."""
    permitidos = ORIGENES_PERMITIDOS[operacion]
    if cfdi.estado not in permitidos:
        esperados = ", ".join(sorted(estado.value for estado in permitidos))
        raise TransicionInvalida(
            f"No se puede {operacion} el CFDI {cfdi.uuid or cfdi.id}: está en "
            f"{cfdi.estado.value} y la operación sólo parte de {esperados}."
        )


@dataclass(frozen=True)
class CandidatoReporte:
    """Reporte de venta que podría corresponder a un CFDI (vista plana, sin ORM)."""

    id: int
    periodo: str
    rfc_agencia: str
    comision_centavos: int
    boletos: int


@dataclass(frozen=True)
class VeredictoConciliacion:
    """Resultado del motor. `diferencia_centavos` conserva el SIGNO.

    Positiva  → el CFDI factura de MÁS que la comisión devengada.
    Negativa  → el CFDI factura de MENOS que la comisión devengada.
    """

    resultado: ResultadoMatch
    reporte_id: int | None
    diferencia_centavos: int
    motivo: str


def pesos(centavos: int) -> str:
    """Formatea centavos enteros como '$1,234.56 MXN' (el signo va al frente)."""
    signo = "-" if centavos < 0 else ""
    unidades, fraccion = divmod(abs(centavos), 100)
    return f"{signo}${unidades:,}.{fraccion:02d} MXN"


def umbral_centavos(
    comision_centavos: int, tolerancia_centavos: int, tolerancia_porcentaje: float | Decimal
) -> int:
    """Umbral aplicable: el mayor entre la tolerancia absoluta y la porcentual.

    La porcentual se calcula en `Decimal` y se TRUNCA a centavos enteros. Con
    float, `21000 * 0.009` da 188.99999999999997 y el truncado deja un umbral
    de 188 cuando el exacto es 189: un centavo que cambia MATCH por PARCIAL.
    """
    porcentaje = Decimal(str(tolerancia_porcentaje))
    porcentual = int((Decimal(abs(comision_centavos)) * porcentaje).to_integral_value(ROUND_DOWN))
    return max(tolerancia_centavos, porcentual)


def conciliar(
    cfdi_total_centavos: int,
    periodo: str,
    rfc_emisor: str,
    candidatos: list[CandidatoReporte],
    *,
    tolerancia_centavos: int,
    tolerancia_porcentaje: float | Decimal,
) -> VeredictoConciliacion:
    """Compara el total del CFDI contra la comisión devengada del periodo.

    Sólo consideran los candidatos cuyo periodo Y RFC coinciden exactamente
    (se normalizan espacios y mayúsculas, nada más).
    """
    periodo_norm = periodo.strip()
    rfc_norm = rfc_emisor.strip().upper()

    elegibles = [
        c
        for c in candidatos
        if c.periodo.strip() == periodo_norm and c.rfc_agencia.strip().upper() == rfc_norm
    ]

    if not elegibles:
        return VeredictoConciliacion(
            resultado=ResultadoMatch.NO_MATCH,
            reporte_id=None,
            diferencia_centavos=0,
            motivo=(
                f"No existe reporte de venta para la agencia {rfc_norm} "
                f"en el periodo {periodo_norm}: no hay contra qué conciliar."
            ),
        )

    # Defensivo: la tabla tiene un UNIQUE(periodo, rfc_agencia), así que esto no
    # debería pasar. Si pasa, se toma el reporte de menor diferencia absoluta y
    # se deja constancia en el motivo para que el analista lo revise.
    elegido = min(elegibles, key=lambda c: abs(cfdi_total_centavos - c.comision_centavos))
    diferencia = cfdi_total_centavos - elegido.comision_centavos
    umbral = umbral_centavos(elegido.comision_centavos, tolerancia_centavos, tolerancia_porcentaje)

    prefijo = ""
    if len(elegibles) > 1:
        prefijo = (
            f"Se hallaron {len(elegibles)} reportes para {rfc_norm} en {periodo_norm}; "
            f"se tomó el más cercano (reporte {elegido.id}). "
        )

    if abs(diferencia) <= umbral:
        if diferencia == 0:
            detalle = (
                f"Coincidencia exacta contra el reporte {elegido.id} "
                f"por {pesos(elegido.comision_centavos)}."
            )
        else:
            detalle = (
                f"Diferencia de {pesos(diferencia)} contra el reporte {elegido.id} "
                f"({pesos(elegido.comision_centavos)}), dentro de la tolerancia de {pesos(umbral)}."
            )
        resultado = ResultadoMatch.MATCH
    else:
        detalle = (
            f"Diferencia de {pesos(diferencia)} contra el reporte {elegido.id} "
            f"({pesos(elegido.comision_centavos)}); excede la tolerancia de {pesos(umbral)}."
        )
        resultado = ResultadoMatch.PARCIAL

    return VeredictoConciliacion(
        resultado=resultado,
        reporte_id=elegido.id,
        diferencia_centavos=diferencia,
        motivo=(prefijo + detalle)[:LARGO_MAX_MOTIVO],
    )


def conciliar_cfdi(
    db: Session,
    cfdi: Cfdi,
    *,
    tolerancia_centavos: int,
    tolerancia_porcentaje: float | Decimal,
) -> Conciliacion:
    """Concilia un CFDI ya validado y deja el veredicto persistido (sin commit).

    Crea la conciliación o REEMPLAZA la existente: al re-conciliar de forma
    automática se limpia la resolución manual previa, porque el veredicto
    vuelve a ser del motor.

    Lanza `TransicionInvalida` si el CFDI no pasó la validación fiscal
    (RECHAZADO) o aún no se dictamina (RECIBIDO): sin esto, un comprobante
    rechazado podía terminar CONCILIADO.
    """
    exigir_estado(cfdi, "conciliar")
    reportes = (
        db.execute(
            select(ReporteVenta).where(
                ReporteVenta.periodo == cfdi.periodo,
                ReporteVenta.rfc_agencia == cfdi.rfc_emisor,
            )
        )
        .scalars()
        .all()
    )
    candidatos = [
        CandidatoReporte(
            id=r.id,
            periodo=r.periodo,
            rfc_agencia=r.rfc_agencia,
            comision_centavos=r.comision_centavos,
            boletos=r.boletos,
        )
        for r in reportes
    ]

    veredicto = conciliar(
        cfdi.total_centavos,
        cfdi.periodo,
        cfdi.rfc_emisor,
        candidatos,
        tolerancia_centavos=tolerancia_centavos,
        tolerancia_porcentaje=tolerancia_porcentaje,
    )

    conciliacion = cfdi.conciliacion
    if conciliacion is None and cfdi.id is not None:
        conciliacion = db.execute(
            select(Conciliacion).where(Conciliacion.cfdi_id == cfdi.id)
        ).scalar_one_or_none()
    if conciliacion is None:
        # Se enlaza por relación (no por id) para que funcione aun si el CFDI
        # todavía no tiene PK asignada.
        conciliacion = Conciliacion(cfdi=cfdi)

    conciliacion.resultado = veredicto.resultado
    conciliacion.reporte_id = veredicto.reporte_id
    conciliacion.diferencia_centavos = veredicto.diferencia_centavos
    conciliacion.motivo = veredicto.motivo
    conciliacion.metodo = MetodoMatch.AUTOMATICO
    conciliacion.resuelta_por_id = None
    conciliacion.nota_resolucion = None
    conciliacion.conciliada_en = utcnow()

    cfdi.estado = (
        EstadoCfdi.CONCILIADO
        if veredicto.resultado is ResultadoMatch.MATCH
        else EstadoCfdi.EN_EXCEPCION
    )

    db.add(conciliacion)
    db.flush()
    return conciliacion


def resolver_manual(
    db: Session,
    conciliacion: Conciliacion,
    *,
    usuario_id: int,
    nota: str,
    aceptar: bool,
) -> Conciliacion:
    """Cierra una excepción con criterio humano (sin commit).

    `aceptar=True`  → se da por conciliado: resultado MATCH y CFDI CONCILIADO.
    `aceptar=False` → se RESPETA el veredicto del motor y el CFDI se queda en
    excepción; la nota explica por qué no procede.

    En ambos casos queda registrado quién resolvió y con qué nota: es lo que
    después se cruza contra la bitácora de auditoría.

    Lanza `TransicionInvalida` si el CFDI no está EN_EXCEPCION: resolver «no
    aceptar» sobre un CFDI ya conciliado lo regresaba a la cola con resultado
    MATCH, un estado incoherente.
    """
    exigir_estado(conciliacion.cfdi, "resolver")
    conciliacion.metodo = MetodoMatch.MANUAL
    conciliacion.resuelta_por_id = usuario_id
    conciliacion.nota_resolucion = nota
    conciliacion.conciliada_en = utcnow()

    if aceptar:
        conciliacion.resultado = ResultadoMatch.MATCH
        conciliacion.cfdi.estado = EstadoCfdi.CONCILIADO
    else:
        conciliacion.cfdi.estado = EstadoCfdi.EN_EXCEPCION

    db.add(conciliacion)
    db.flush()
    return conciliacion
