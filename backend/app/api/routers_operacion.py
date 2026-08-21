"""Operación del analista: excepciones, resolución manual, tablero, exportación
y bitácora.

Todo este router está reservado a los roles ANALISTA y ADMIN: una agencia ve
sus propios comprobantes, pero nunca la cola de excepciones ni la bitácora.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterator, Sequence
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import selectinload

from app.api.deps import SesionBD, UsuarioActual, registrar_auditoria, requiere_rol
from app.api.schemas import (
    CfdiOut,
    ConciliacionOut,
    EventoAuditoriaOut,
    ResolverIn,
    ResumenOut,
    formatear_centavos,
)
from app.domain.models import (
    Cfdi,
    Conciliacion,
    EstadoCfdi,
    EventoAuditoria,
    ReporteVenta,
    ResultadoMatch,
    Rol,
)
from app.services.conciliacion import resolver_manual

#: La operación es territorio del analista; el administrador entra por herencia.
solo_operacion = requiere_rol(Rol.ANALISTA, Rol.ADMIN)

#: El candado se declara UNA vez, a nivel de router: ninguna ruta puede
#: quedarse sin él por olvido al agregarla.
router = APIRouter(prefix="/api", tags=["operación"], dependencies=[Depends(solo_operacion)])

ENCABEZADOS_CSV = ["uuid", "rfc_emisor", "periodo", "total", "resultado", "diferencia", "estado"]


# --------------------------------------------------------------------------- #
# Cola de excepciones
# --------------------------------------------------------------------------- #


@router.get("/excepciones", response_model=list[CfdiOut], summary="Cola de excepciones")
def listar_excepciones(
    db: SesionBD,
    periodo: str | None = Query(default=None, max_length=10, description='Ej. "2026-08-P1"'),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Cfdi]:
    """CFDIs que el motor no pudo cerrar solo, con su conciliación adjunta."""
    consulta = (
        select(Cfdi)
        .options(selectinload(Cfdi.conciliacion))
        .where(Cfdi.estado == EstadoCfdi.EN_EXCEPCION)
    )
    if periodo:
        consulta = consulta.where(Cfdi.periodo == periodo)
    consulta = consulta.order_by(Cfdi.periodo, Cfdi.id).limit(limit).offset(offset)
    return list(db.scalars(consulta).all())


@router.post(
    "/conciliaciones/{conciliacion_id}/resolver",
    response_model=ConciliacionOut,
    summary="Resolver manualmente una excepción",
)
def resolver_excepcion(
    conciliacion_id: int,
    datos: ResolverIn,
    db: SesionBD,
    usuario: UsuarioActual,
) -> Conciliacion:
    """Cierra una excepción con criterio humano y lo deja asentado en bitácora.

    Aceptar da el CFDI por conciliado; no aceptar respeta el veredicto del motor
    y lo deja en la cola. En ambos casos queda quién resolvió y con qué nota.
    """
    conciliacion = db.get(Conciliacion, conciliacion_id)
    if conciliacion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe la conciliación {conciliacion_id}",
        )

    resolver_manual(
        db,
        conciliacion,
        usuario_id=usuario.id,
        nota=datos.nota,
        aceptar=datos.aceptar,
    )
    registrar_auditoria(
        db,
        usuario,
        "RESOLUCION_MANUAL",
        "Conciliacion",
        conciliacion.id,
        detalle=(
            f"CFDI {conciliacion.cfdi_id} · "
            f"{'aceptada' if datos.aceptar else 'no aceptada'} · "
            f"diferencia {formatear_centavos(conciliacion.diferencia_centavos)} · "
            f"{datos.nota}"
        ),
    )
    db.commit()
    return conciliacion


# --------------------------------------------------------------------------- #
# Tablero
# --------------------------------------------------------------------------- #


@router.get("/resumen", response_model=ResumenOut, summary="Resumen agregado")
def resumen(
    db: SesionBD,
    periodo: str | None = Query(default=None, max_length=10, description='Ej. "2026-08-P1"'),
) -> ResumenOut:
    """Conteos por estado y por veredicto, más los totales del periodo.

    Los conteos siempre traen TODAS las llaves posibles, aunque valgan cero: un
    tablero al que le faltan renglones se lee como si el dato no existiera.
    """
    del_periodo: list[ColumnElement[bool]] = [Cfdi.periodo == periodo] if periodo else []

    por_estado = {estado.value: 0 for estado in EstadoCfdi}
    for estado, cantidad in db.execute(
        select(Cfdi.estado, func.count(Cfdi.id)).where(*del_periodo).group_by(Cfdi.estado)
    ):
        por_estado[EstadoCfdi(estado).value] = cantidad

    por_resultado = {resultado.value: 0 for resultado in ResultadoMatch}
    for resultado, cantidad in db.execute(
        select(Conciliacion.resultado, func.count(Conciliacion.id))
        .join(Cfdi, Cfdi.id == Conciliacion.cfdi_id)
        .where(*del_periodo)
        .group_by(Conciliacion.resultado)
    ):
        por_resultado[ResultadoMatch(resultado).value] = cantidad

    def _suma(columna: Any, *condiciones: ColumnElement[bool]) -> int:
        total = db.scalar(
            select(func.coalesce(func.sum(columna), 0)).where(*del_periodo, *condiciones)
        )
        return int(total or 0)

    diferencia_total = db.scalar(
        select(func.coalesce(func.sum(func.abs(Conciliacion.diferencia_centavos)), 0))
        .select_from(Conciliacion)
        .join(Cfdi, Cfdi.id == Conciliacion.cfdi_id)
        .where(*del_periodo)
    )

    reportado = select(func.coalesce(func.sum(ReporteVenta.comision_centavos), 0))
    if periodo:
        reportado = reportado.where(ReporteVenta.periodo == periodo)

    return ResumenOut(
        periodo=periodo,
        total_cfdis=sum(por_estado.values()),
        por_estado=por_estado,
        por_resultado=por_resultado,
        total_facturado_centavos=_suma(Cfdi.total_centavos),
        total_conciliado_centavos=_suma(Cfdi.total_centavos, Cfdi.estado == EstadoCfdi.CONCILIADO),
        total_reportado_centavos=int(db.scalar(reportado) or 0),
        diferencia_total_centavos=int(diferencia_total or 0),
    )


# --------------------------------------------------------------------------- #
# Exportación
# --------------------------------------------------------------------------- #


def _generar_csv(filas: Sequence[tuple[Cfdi, Conciliacion | None]]) -> Iterator[str]:
    """Emite el CSV por partes a partir de filas YA materializadas.

    Se materializan antes a propósito: el generador corre cuando la sesión de
    base de datos ya pudo haberse cerrado.
    """
    memoria = io.StringIO()
    escritor = csv.writer(memoria, lineterminator="\n")

    def _vaciar() -> str:
        contenido = memoria.getvalue()
        memoria.seek(0)
        memoria.truncate(0)
        return contenido

    yield "\ufeff"  # BOM, para que Excel respete los acentos
    escritor.writerow(ENCABEZADOS_CSV)
    yield _vaciar()

    for cfdi, conciliacion in filas:
        escritor.writerow(
            [
                cfdi.uuid,
                cfdi.rfc_emisor,
                cfdi.periodo,
                formatear_centavos(cfdi.total_centavos),
                conciliacion.resultado.value if conciliacion else "",
                formatear_centavos(conciliacion.diferencia_centavos) if conciliacion else "",
                cfdi.estado.value,
            ]
        )
        yield _vaciar()


@router.get("/exportar.csv", summary="Exportar CFDIs a CSV")
def exportar_csv(
    db: SesionBD,
    usuario: UsuarioActual,
    periodo: str | None = Query(default=None, max_length=10, description='Ej. "2026-08-P1"'),
    estado: EstadoCfdi | None = Query(default=None, description="Filtra por estado"),
) -> StreamingResponse:
    """Descarga el corte de CFDIs con su veredicto de conciliación."""
    consulta = select(Cfdi, Conciliacion).outerjoin(Conciliacion, Conciliacion.cfdi_id == Cfdi.id)
    if periodo:
        consulta = consulta.where(Cfdi.periodo == periodo)
    if estado is not None:
        consulta = consulta.where(Cfdi.estado == estado)
    filas = db.execute(consulta.order_by(Cfdi.periodo, Cfdi.uuid)).all()

    nombre = f"fiscalmatch_{periodo or 'todos'}.csv"
    registrar_auditoria(
        db,
        usuario,
        "EXPORTA_CSV",
        "Cfdi",
        "*",
        detalle=f"{len(filas)} registro(s) · periodo {periodo or 'todos'}",
    )
    db.commit()

    return StreamingResponse(
        _generar_csv(filas),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# --------------------------------------------------------------------------- #
# Bitácora
# --------------------------------------------------------------------------- #


@router.get("/auditoria", response_model=list[EventoAuditoriaOut], summary="Bitácora reciente")
def auditoria(
    db: SesionBD,
    limit: int = Query(default=100, ge=1, le=100, description="Máximo 100 eventos"),
) -> list[EventoAuditoria]:
    """Últimos movimientos registrados, del más reciente al más antiguo.

    El orden lo marca el id, no la marca de tiempo: la bitácora es una tabla de
    sólo-anexar, así que el id es estrictamente monótono, mientras que el reloj
    del host puede corregirse hacia atrás y desordenar el expediente.
    """
    consulta = select(EventoAuditoria).order_by(EventoAuditoria.id.desc()).limit(limit)
    return list(db.scalars(consulta).all())
