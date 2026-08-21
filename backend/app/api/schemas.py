"""Esquemas de entrada/salida de la API (Pydantic v2).

Regla transversal: el dinero viaja SIEMPRE como entero de centavos. Los campos
`*_formateado` son puro adorno de presentación y se calculan con aritmética
entera —nunca con float— para que la vista no pueda introducir un redondeo.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.domain.models import EstadoCfdi, MetodoMatch, ResultadoMatch, Rol


def formatear_centavos(centavos: int | None) -> str | None:
    """Convierte centavos a texto contable ("123456" → "1,234.56").

    Todo se calcula con enteros: la parte entera por división entera y los
    centavos por módulo.
    """
    if centavos is None:
        return None
    signo = "-" if centavos < 0 else ""
    absoluto = abs(centavos)
    return f"{signo}{absoluto // 100:,}.{absoluto % 100:02d}"


class _Desde(BaseModel):
    """Base para los esquemas que se hidratan desde entidades ORM."""

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Identidad
# --------------------------------------------------------------------------- #


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class AgenciaOut(_Desde):
    id: int
    rfc: str
    razon_social: str
    activa: bool


class UsuarioOut(_Desde):
    id: int
    email: str
    nombre: str
    rol: Rol
    activo: bool
    agencia_id: int | None = None
    agencia: AgenciaOut | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expira_en_minutos: int
    usuario: UsuarioOut


# --------------------------------------------------------------------------- #
# CFDI, validación y conciliación
# --------------------------------------------------------------------------- #


class ValidacionOut(_Desde):
    id: int
    regla: str
    aprobada: bool
    detalle: str = ""
    evaluada_en: datetime


class ReglaFallidaOut(BaseModel):
    """Detalle devuelto al rechazar una carga (no se persiste tal cual)."""

    regla: str
    detalle: str = ""


class ConciliacionOut(_Desde):
    id: int
    cfdi_id: int
    reporte_id: int | None = None
    resultado: ResultadoMatch
    diferencia_centavos: int = 0
    motivo: str = ""
    metodo: MetodoMatch
    resuelta_por_id: int | None = None
    nota_resolucion: str | None = None
    conciliada_en: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def diferencia_formateada(self) -> str:
        return formatear_centavos(self.diferencia_centavos) or "0.00"


class ReporteVentaOut(_Desde):
    id: int
    periodo: str
    rfc_agencia: str
    comision_centavos: int
    boletos: int = 0
    referencia: str = ""
    cargado_en: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def comision_formateada(self) -> str:
        return formatear_centavos(self.comision_centavos) or "0.00"


class CfdiOut(_Desde):
    """Vista de lista: el CFDI con el veredicto de su conciliación."""

    id: int
    uuid: str
    rfc_emisor: str
    rfc_receptor: str
    serie: str | None = None
    folio: str | None = None
    fecha_emision: date
    periodo: str
    subtotal_centavos: int
    impuestos_centavos: int = 0
    total_centavos: int
    moneda: str = "MXN"
    nombre_archivo: str
    estado: EstadoCfdi
    agencia_id: int
    cargado_por_id: int | None = None
    cargado_en: datetime
    conciliacion: ConciliacionOut | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_formateado(self) -> str:
        return formatear_centavos(self.total_centavos) or "0.00"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resultado(self) -> ResultadoMatch | None:
        """Veredicto del motor, o `None` si el CFDI aún no se concilió."""
        return self.conciliacion.resultado if self.conciliacion else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def diferencia_centavos(self) -> int | None:
        return self.conciliacion.diferencia_centavos if self.conciliacion else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def diferencia_formateada(self) -> str | None:
        return formatear_centavos(self.diferencia_centavos)


class CfdiDetalleOut(CfdiOut):
    """Vista de detalle: agrega el desglose regla por regla."""

    validaciones: list[ValidacionOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Operación
# --------------------------------------------------------------------------- #


class ResolverIn(BaseModel):
    """Resolución manual de una excepción por parte de un analista."""

    aceptar: bool
    nota: str = Field(default="", max_length=500)


class EventoAuditoriaOut(_Desde):
    id: int
    actor: str
    accion: str
    entidad: str
    entidad_id: str
    detalle: str = ""
    ocurrido_en: datetime


class ResumenOut(BaseModel):
    """Tablero agregado: conteos por estado y por veredicto, más totales."""

    periodo: str | None = None
    total_cfdis: int = 0
    por_estado: dict[str, int] = Field(default_factory=dict)
    por_resultado: dict[str, int] = Field(default_factory=dict)

    total_facturado_centavos: int = 0
    total_conciliado_centavos: int = 0
    total_reportado_centavos: int = 0
    diferencia_total_centavos: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_facturado_formateado(self) -> str:
        return formatear_centavos(self.total_facturado_centavos) or "0.00"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_conciliado_formateado(self) -> str:
        return formatear_centavos(self.total_conciliado_centavos) or "0.00"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_reportado_formateado(self) -> str:
        return formatear_centavos(self.total_reportado_centavos) or "0.00"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def diferencia_total_formateada(self) -> str:
        return formatear_centavos(self.diferencia_total_centavos) or "0.00"
