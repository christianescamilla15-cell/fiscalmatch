"""Modelo de dominio de FiscalMatch.

REGLA TRANSVERSAL: todo importe se guarda en CENTAVOS como entero (`*_centavos`).
Nunca float: en materia fiscal un centavo de diferencia es un hallazgo.
"""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Rol(str, enum.Enum):
    AGENCIA = "AGENCIA"      # sólo ve y carga lo suyo
    ANALISTA = "ANALISTA"    # ve todo, resuelve excepciones
    ADMIN = "ADMIN"


class EstadoCfdi(str, enum.Enum):
    RECIBIDO = "RECIBIDO"
    VALIDADO = "VALIDADO"
    RECHAZADO = "RECHAZADO"      # no pasó validación fiscal
    CONCILIADO = "CONCILIADO"
    EN_EXCEPCION = "EN_EXCEPCION"


class ResultadoMatch(str, enum.Enum):
    MATCH = "MATCH"
    PARCIAL = "PARCIAL"
    NO_MATCH = "NO_MATCH"


class MetodoMatch(str, enum.Enum):
    AUTOMATICO = "AUTOMATICO"
    MANUAL = "MANUAL"


class Agencia(Base):
    __tablename__ = "agencias"
    id: Mapped[int] = mapped_column(primary_key=True)
    rfc: Mapped[str] = mapped_column(String(13), unique=True, index=True)
    razon_social: Mapped[str] = mapped_column(String(200))
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    usuarios: Mapped[list[Usuario]] = relationship(back_populates="agencia")
    cfdis: Mapped[list[Cfdi]] = relationship(back_populates="agencia")


class Usuario(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nombre: Mapped[str] = mapped_column(String(200))
    rol: Mapped[Rol] = mapped_column(Enum(Rol), default=Rol.AGENCIA)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    agencia_id: Mapped[int | None] = mapped_column(ForeignKey("agencias.id"), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agencia: Mapped[Agencia | None] = relationship(back_populates="usuarios")


class Cfdi(Base):
    """CFDI de comisión emitido por una agencia."""
    __tablename__ = "cfdis"
    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    rfc_emisor: Mapped[str] = mapped_column(String(13), index=True)
    rfc_receptor: Mapped[str] = mapped_column(String(13))
    serie: Mapped[str | None] = mapped_column(String(25), nullable=True)
    folio: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fecha_emision: Mapped[date] = mapped_column(Date, index=True)
    periodo: Mapped[str] = mapped_column(String(10), index=True)  # "2026-08-P1"
    subtotal_centavos: Mapped[int] = mapped_column(Integer)
    impuestos_centavos: Mapped[int] = mapped_column(Integer, default=0)
    total_centavos: Mapped[int] = mapped_column(Integer)
    moneda: Mapped[str] = mapped_column(String(3), default="MXN")
    no_certificado: Mapped[str | None] = mapped_column(String(20), nullable=True)
    xml_original: Mapped[str] = mapped_column(Text)
    nombre_archivo: Mapped[str] = mapped_column(String(255))
    estado: Mapped[EstadoCfdi] = mapped_column(Enum(EstadoCfdi), default=EstadoCfdi.RECIBIDO, index=True)
    agencia_id: Mapped[int] = mapped_column(ForeignKey("agencias.id"), index=True)
    cargado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    cargado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agencia: Mapped[Agencia] = relationship(back_populates="cfdis")
    validaciones: Mapped[list[Validacion]] = relationship(back_populates="cfdi", cascade="all, delete-orphan")
    conciliacion: Mapped[Conciliacion | None] = relationship(back_populates="cfdi", uselist=False, cascade="all, delete-orphan")


class Validacion(Base):
    """Resultado de UNA regla de validación fiscal sobre un CFDI."""
    __tablename__ = "validaciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    cfdi_id: Mapped[int] = mapped_column(ForeignKey("cfdis.id"), index=True)
    regla: Mapped[str] = mapped_column(String(60))       # ej. "TOTALES_COHERENTES"
    aprobada: Mapped[bool] = mapped_column(Boolean)
    detalle: Mapped[str] = mapped_column(String(500), default="")
    evaluada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cfdi: Mapped[Cfdi] = relationship(back_populates="validaciones")


class ReporteVenta(Base):
    """Comisión devengada según el sistema de ventas — la contraparte del match."""
    __tablename__ = "reportes_venta"
    __table_args__ = (UniqueConstraint("periodo", "rfc_agencia", name="uq_periodo_agencia"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    periodo: Mapped[str] = mapped_column(String(10), index=True)
    rfc_agencia: Mapped[str] = mapped_column(String(13), index=True)
    comision_centavos: Mapped[int] = mapped_column(Integer)
    boletos: Mapped[int] = mapped_column(Integer, default=0)
    referencia: Mapped[str] = mapped_column(String(60), default="")
    cargado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conciliacion(Base):
    """Veredicto del motor: CFDI ↔ reporte de venta."""
    __tablename__ = "conciliaciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    cfdi_id: Mapped[int] = mapped_column(ForeignKey("cfdis.id"), unique=True, index=True)
    reporte_id: Mapped[int | None] = mapped_column(ForeignKey("reportes_venta.id"), nullable=True)
    resultado: Mapped[ResultadoMatch] = mapped_column(Enum(ResultadoMatch), index=True)
    diferencia_centavos: Mapped[int] = mapped_column(Integer, default=0)
    motivo: Mapped[str] = mapped_column(String(300), default="")
    metodo: Mapped[MetodoMatch] = mapped_column(Enum(MetodoMatch), default=MetodoMatch.AUTOMATICO)
    resuelta_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    nota_resolucion: Mapped[str | None] = mapped_column(String(500), nullable=True)
    conciliada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cfdi: Mapped[Cfdi] = relationship(back_populates="conciliacion")


class EventoAuditoria(Base):
    """Bitácora SOX: quién hizo qué, cuándo y sobre qué entidad."""
    __tablename__ = "eventos_auditoria"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(200), index=True)
    accion: Mapped[str] = mapped_column(String(60), index=True)
    entidad: Mapped[str] = mapped_column(String(40))
    entidad_id: Mapped[str] = mapped_column(String(40))
    detalle: Mapped[str] = mapped_column(Text, default="")
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
