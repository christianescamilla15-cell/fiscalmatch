"""Carga y consulta de CFDIs de comisión.

El flujo de alta es: parsear el XML → validarlo fiscalmente → si pasa, guardar
y conciliar contra el reporte de venta del periodo. Un CFDI rechazado también
se persiste con su desglose de reglas: la evidencia de lo que no cuadró es
parte del expediente de auditoría.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import SesionBD, es_de_otra_agencia, registrar_auditoria, requiere_rol
from app.api.schemas import CfdiDetalleOut, CfdiOut
from app.core.config import get_settings
from app.domain.models import (
    Agencia,
    Cfdi,
    Conciliacion,
    EstadoCfdi,
    ResultadoMatch,
    Rol,
    Usuario,
    Validacion,
)
from app.services.conciliacion import conciliar_cfdi, periodo_de
from app.services.validacion import ErrorParseoCfdi, aprobado, parsear_cfdi, validar

router = APIRouter(prefix="/api/cfdis", tags=["cfdis"])

#: Quién puede cargar comprobantes.
puede_cargar = requiere_rol(Rol.AGENCIA, Rol.ANALISTA)
#: Quién puede consultarlos (el alcance de lo que ve cada uno se acota abajo).
puede_consultar = requiere_rol(Rol.AGENCIA, Rol.ANALISTA, Rol.ADMIN)


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #


def _decodificar(contenido: bytes) -> str:
    """Devuelve el XML como texto, tolerando BOM y acentos en Latin-1."""
    for codificacion in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return contenido.decode(codificacion)
        except UnicodeDecodeError:
            continue
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No se pudo leer el archivo: codificación de caracteres desconocida",
    )


def _obtener_cfdi(db: Session, cfdi_id: int, usuario: Usuario) -> Cfdi:
    """Recupera un CFDI aplicando la frontera de visibilidad por agencia."""
    cfdi = db.get(Cfdi, cfdi_id)
    if cfdi is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No existe el CFDI {cfdi_id}"
        )
    if es_de_otra_agencia(usuario, cfdi.agencia_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El comprobante pertenece a otra agencia",
        )
    return cfdi


# --------------------------------------------------------------------------- #
# Alta
# --------------------------------------------------------------------------- #


@router.post(
    "",
    response_model=CfdiDetalleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Cargar un CFDI de comisión",
)
@router.post(
    "/",
    response_model=CfdiDetalleOut,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def cargar_cfdi(
    db: SesionBD,
    archivo: UploadFile = File(..., description="XML del CFDI 4.0"),
    usuario: Usuario = Depends(puede_cargar),
) -> Cfdi:
    """Recibe el XML, lo valida fiscalmente y —si pasa— lo concilia."""
    config = get_settings()
    nombre_archivo = (archivo.filename or "").strip()
    if not nombre_archivo.lower().endswith(".xml"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Sólo se aceptan archivos .xml"
        )

    texto = _decodificar(archivo.file.read())
    if not texto.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo está vacío"
        )

    # 1) Parseo -------------------------------------------------------------
    try:
        parseado = parsear_cfdi(texto)
    except ErrorParseoCfdi as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"XML no interpretable como CFDI: {exc}",
        ) from exc

    rfc_emisor = parseado.rfc_emisor.strip().upper()

    # 2) Frontera por agencia: quien carga a nombre de una agencia sólo puede
    #    cargar SUS propios comprobantes.
    if usuario.rol is Rol.AGENCIA:
        if usuario.agencia is None or usuario.agencia.rfc.strip().upper() != rfc_emisor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El RFC emisor del comprobante no corresponde a su agencia",
            )
        agencia = usuario.agencia
    else:
        agencia = db.scalar(select(Agencia).where(func.upper(Agencia.rfc) == rfc_emisor))
        if agencia is None:
            # Sin agencia no hay dónde archivarlo: se rechaza sin persistir.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "mensaje": "El CFDI no pasó la validación fiscal",
                    "reglas_fallidas": [
                        {
                            "regla": "EMISOR_REGISTRADO",
                            "detalle": f"El RFC {rfc_emisor} no corresponde a ninguna agencia",
                        }
                    ],
                },
            )

    # El UUID es único por diseño fiscal: un duplicado no se archiva, se rechaza.
    # Se normaliza a MAYÚSCULAS (como lo emite el SAT) antes de buscar y de guardar:
    # el mismo folio reenviado en minúsculas es el mismo CFDI, no uno nuevo. Como lo
    # guardado ya está normalizado, la búsqueda usa el índice único tal cual.
    uuid = parseado.uuid.strip().upper()
    if db.scalar(select(Cfdi.id).where(Cfdi.uuid == uuid)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El CFDI con UUID {uuid} ya fue cargado",
        )

    # 3) Validación fiscal --------------------------------------------------
    resultados = validar(
        parseado,
        xml_texto=texto,
        rfc_receptor_esperado=config.rfc_receptor_esperado,
        rfcs_agencias_registradas=set(
            db.scalars(select(Agencia.rfc).where(Agencia.activa.is_(True)))
        ),
        uuids_existentes=set(db.scalars(select(Cfdi.uuid))),
    )
    paso = aprobado(resultados)

    # 4) Persistencia -------------------------------------------------------
    cfdi = Cfdi(
        uuid=uuid,
        rfc_emisor=rfc_emisor,
        rfc_receptor=parseado.rfc_receptor.strip().upper(),
        serie=parseado.serie,
        folio=parseado.folio,
        fecha_emision=parseado.fecha_emision,
        # El periodo se deriva de la fecha del comprobante y no se le pide a quien
        # carga: un dato capturado a mano es un dato que puede no cuadrar.
        periodo=periodo_de(parseado.fecha_emision),
        subtotal_centavos=parseado.subtotal_centavos,
        impuestos_centavos=parseado.impuestos_centavos,
        total_centavos=parseado.total_centavos,
        moneda=parseado.moneda,
        no_certificado=parseado.no_certificado or None,
        xml_original=texto,
        nombre_archivo=nombre_archivo[:255],
        estado=EstadoCfdi.VALIDADO if paso else EstadoCfdi.RECHAZADO,
        agencia_id=agencia.id,
        cargado_por_id=usuario.id,
    )
    db.add(cfdi)
    try:
        db.flush()
    except IntegrityError as exc:  # carrera contra otra carga del mismo UUID
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El CFDI con UUID {uuid} ya fue cargado",
        ) from exc

    for resultado in resultados:
        db.add(
            Validacion(
                cfdi_id=cfdi.id,
                regla=resultado.regla,
                aprobada=resultado.aprobada,
                detalle=resultado.detalle,
            )
        )

    fallidas = [
        {"regla": r.regla, "detalle": r.detalle} for r in resultados if not r.aprobada
    ]
    registrar_auditoria(
        db,
        usuario,
        "CARGA_CFDI",
        "Cfdi",
        cfdi.id,
        detalle=(
            f"{nombre_archivo} · UUID {uuid} · "
            f"{'validado' if paso else 'rechazado'} · {len(fallidas)} regla(s) fallida(s)"
        ),
    )

    # 5) Conciliación automática (sólo si pasó la validación fiscal) --------
    if paso:
        conciliar_cfdi(
            db,
            cfdi,
            tolerancia_centavos=config.tolerancia_centavos,
            tolerancia_porcentaje=config.tolerancia_porcentaje,
        )
    db.commit()

    # 6) Rechazo: el comprobante queda archivado con su evidencia.
    if not paso:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "mensaje": "El CFDI no pasó la validación fiscal",
                "cfdi_id": cfdi.id,
                "uuid": cfdi.uuid,
                "estado": EstadoCfdi.RECHAZADO.value,
                "reglas_fallidas": fallidas,
            },
        )

    return cfdi


# --------------------------------------------------------------------------- #
# Consulta
# --------------------------------------------------------------------------- #


@router.get("", response_model=list[CfdiOut], summary="Listar CFDIs")
@router.get("/", response_model=list[CfdiOut], include_in_schema=False)
def listar_cfdis(
    db: SesionBD,
    respuesta: Response,
    usuario: Usuario = Depends(puede_consultar),
    estado: EstadoCfdi | None = Query(default=None, description="Filtra por estado"),
    periodo: str | None = Query(default=None, max_length=10, description='Ej. "2026-08-P1"'),
    resultado: ResultadoMatch | None = Query(default=None, description="Veredicto de conciliación"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Cfdi]:
    """Lista comprobantes. El rol AGENCIA sólo ve los de su propia agencia.

    El total sin paginar viaja en el encabezado `X-Total-Count`.
    """
    consulta = select(Cfdi).options(selectinload(Cfdi.conciliacion))
    conteo = select(func.count()).select_from(Cfdi)

    filtros = []
    if usuario.rol is Rol.AGENCIA:
        # Sin agencia asignada no ve nada; jamás "todo".
        filtros.append(Cfdi.agencia_id == (usuario.agencia_id or -1))
    if estado is not None:
        filtros.append(Cfdi.estado == estado)
    if periodo:
        filtros.append(Cfdi.periodo == periodo)
    if resultado is not None:
        consulta = consulta.join(Conciliacion, Conciliacion.cfdi_id == Cfdi.id)
        conteo = conteo.join(Conciliacion, Conciliacion.cfdi_id == Cfdi.id)
        filtros.append(Conciliacion.resultado == resultado)

    if filtros:
        consulta = consulta.where(*filtros)
        conteo = conteo.where(*filtros)

    respuesta.headers["X-Total-Count"] = str(db.scalar(conteo) or 0)
    consulta = consulta.order_by(Cfdi.cargado_en.desc(), Cfdi.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(consulta).all())


@router.get("/{cfdi_id}", response_model=CfdiDetalleOut, summary="Detalle de un CFDI")
def detalle_cfdi(
    cfdi_id: int,
    db: SesionBD,
    usuario: Usuario = Depends(puede_consultar),
) -> Cfdi:
    """Comprobante con su desglose regla por regla y su conciliación."""
    return _obtener_cfdi(db, cfdi_id, usuario)


@router.get("/{cfdi_id}/xml", summary="XML original del CFDI")
def xml_cfdi(
    cfdi_id: int,
    db: SesionBD,
    usuario: Usuario = Depends(puede_consultar),
) -> Response:
    """Devuelve el XML tal como se recibió, byte por byte, sin reescribirlo."""
    cfdi = _obtener_cfdi(db, cfdi_id, usuario)
    return Response(
        content=cfdi.xml_original,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{cfdi.nombre_archivo}"'},
    )
