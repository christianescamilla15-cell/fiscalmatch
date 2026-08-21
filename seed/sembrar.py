"""Siembra la base de FiscalMatch con un juego de datos DIDÁCTICO.

Deja la BD en un estado coherente y verificable: agencias, usuarios, reportes de venta y
24 CFDIs generados que —al pasar por la validación fiscal y el motor de conciliación
REALES, los mismos que usa la API— aterrizan en una mezcla deliberada de resultados:

    ~55 %  MATCH      (exacto o dentro de tolerancia)
    ~20 %  PARCIAL    (diferencias de entre 50 y 900 pesos)
    ~15 %  NO_MATCH   (quincena sin reporte de venta)
    ~10 %  RECHAZADO  (totales incoherentes · RFC receptor equivocado · Version 3.3)

No hay reglas "de mentiras": el guion sólo elige los NÚMEROS, y quien dictamina es
`app.services.validacion` y `app.services.conciliacion`. Si esos servicios cambian de
criterio, la siembra lo detecta y lo avisa en lugar de fingir que todo cuadró.

Uso:
    python seed/sembrar.py             # --reset (por defecto): borra y vuelve a sembrar
    python seed/sembrar.py --no-reset  # sólo siembra si la base está vacía

La cadena de conexión sale de `app.core.config.get_settings()`; para apuntar a otra base
exporta `FM_DATABASE_URL` antes de correr.

REGLA TRANSVERSAL: todo importe se maneja en CENTAVOS enteros. Nunca float.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

# --- importable desde el repo sin instalar nada -----------------------------------------
_RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(_RAIZ))                       # seed/
sys.path.insert(0, str(_RAIZ.parent / "backend"))    # backend/  → paquete `app`

from generar_cfdi import calcular_iva_centavos, construir_cfdi_xml  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.domain.models import (  # noqa: E402
    Agencia,
    Base,
    Cfdi,
    Conciliacion,
    EstadoCfdi,
    EventoAuditoria,
    MetodoMatch,
    ReporteVenta,
    ResultadoMatch,
    Rol,
    Usuario,
    Validacion,
)


def _faltante(que: str, exc: Exception) -> SystemExit:
    """La siembra NO improvisa reglas: si falta una pieza real, se detiene y lo dice.

    Sembrar con criterios propios dejaría la base incoherente con la aplicación, que es
    justo lo contrario de lo que sirve una base de demostración.
    """
    return SystemExit(f"No pude importar {que}. La siembra necesita el servicio real.\nDetalle: {exc}")


try:
    from app.core.seguridad import hash_password  # noqa: E402
except ImportError as exc:  # pragma: no cover
    raise _faltante("`app.core.seguridad.hash_password`", exc) from exc

try:
    from app.services.validacion import aprobado, parsear_cfdi, validar  # noqa: E402
except ImportError as exc:  # pragma: no cover
    raise _faltante("la validación fiscal de `app.services.validacion`", exc) from exc

try:
    from app.services.conciliacion import conciliar_cfdi, resolver_manual  # noqa: E402
except ImportError as exc:  # pragma: no cover
    raise _faltante("el motor de `app.services.conciliacion`", exc) from exc


# ========================================================================================
# Catálogo de datos sembrados
# ========================================================================================

PASSWORD_DEMO = "demo1234"

# RFC de persona moral FICTICIOS: 3 letras + 6 dígitos + 3 alfanuméricos de homoclave.
AGENCIAS: tuple[tuple[str, str], ...] = (
    ("VMO150312QJ4", "VIAJES MONTEALBAN SA DE CV"),
    ("ATP081124RB7", "AGENCIA TURISTICA DEL PACIFICO SA DE CV"),
    ("CVB170908HN3", "CORPORATIVO DE VIAJES BOREAL SA DE CV"),
    ("OTM110527LX9", "OPERADORA TURISTICA MERIDIANA SA DE CV"),
    ("GVA030716PD5", "GRUPO DE VIAJES ALTAMAR SA DE CV"),
    ("SVC190402TF6", "SERVICIOS DE VIAJE CUMBRE SAPI DE CV"),
)

CONTACTOS_AGENCIA: tuple[str, ...] = (
    "Mariana Zepeda",
    "Rodrigo Cantú",
    "Paulina Esquivel",
    "Ernesto Villarreal",
    "Sofía Nájera",
    "Gerardo Peñaloza",
)

# Quincenas CON reporte de venta.
PERIODOS: tuple[str, ...] = ("2026-06-P2", "2026-07-P1", "2026-07-P2")
# Quincena SIN reporte de venta: es lo que produce los NO_MATCH.
PERIODO_SIN_REPORTE = "2026-08-P1"

_PRIMER_DIA: dict[str, date] = {
    "2026-06-P2": date(2026, 6, 18),
    "2026-07-P1": date(2026, 7, 6),
    "2026-07-P2": date(2026, 7, 20),
    PERIODO_SIN_REPORTE: date(2026, 8, 5),
}

# Defectos inyectados a propósito para que la validación fiscal RECHACE el comprobante.
DEFECTO_TOTALES = "TOTALES_INCOHERENTES"
DEFECTO_RECEPTOR = "RFC_RECEPTOR_EQUIVOCADO"
DEFECTO_VERSION = "VERSION_33"

# Otra empresa, también ficticia: el CFDI se emitió a quien no era.
RFC_RECEPTOR_EQUIVOCADO = "GTM180203QP7"
# Descuadre del comprobante defectuoso: $150.00 de más en el Total.
DESCUADRE_CENTAVOS = 15_000

_NS_SEMILLA = uuid.uuid5(uuid.NAMESPACE_DNS, "seed.fiscalmatch.dev")


@dataclass(frozen=True)
class PlanCfdi:
    """Una fila del guion: qué CFDI se emite y en qué debe terminar."""

    clave: str
    agencia: int                          # índice en AGENCIAS
    periodo: str
    subtotal_centavos: int
    resultado: ResultadoMatch | None      # None ⇒ debe quedar RECHAZADO, sin conciliar
    #: CFDI − reporte de venta. Positivo = la agencia facturó de MÁS que lo devengado.
    desviacion_centavos: int = 0
    defecto: str | None = None
    serie: str = "A"

    @property
    def conciliable(self) -> bool:
        return self.defecto is None


# ---------------------------------------------------------------------------------------
# El guion: 18 CFDIs en la rejilla con reporte + 3 sin reporte + 3 defectuosos = 24
#
# La rejilla es (3 quincenas × 6 agencias) y la tabla de reportes tiene UNIQUE(periodo,
# rfc_agencia): por eso cada celda lleva UN solo CFDI conciliable, y así la desviación de
# cada comprobante se controla de forma independiente.
# ---------------------------------------------------------------------------------------
PLAN: tuple[PlanCfdi, ...] = (
    # --- MATCH exacto (8) ---------------------------------------------------------------
    PlanCfdi("M-EXACTO-01", 2, "2026-06-P2", 1_540_000, ResultadoMatch.MATCH),
    PlanCfdi("M-EXACTO-02", 3, "2026-06-P2", 96_400, ResultadoMatch.MATCH),
    PlanCfdi("M-EXACTO-03", 5, "2026-06-P2", 723_800, ResultadoMatch.MATCH),
    PlanCfdi("M-EXACTO-04", 0, "2026-07-P1", 934_120, ResultadoMatch.MATCH),
    PlanCfdi("M-EXACTO-05", 4, "2026-07-P1", 487_600, ResultadoMatch.MATCH),
    PlanCfdi("M-EXACTO-06", 1, "2026-07-P2", 289_400, ResultadoMatch.MATCH),
    PlanCfdi("M-EXACTO-07", 3, "2026-07-P2", 104_900, ResultadoMatch.MATCH),
    PlanCfdi("M-EXACTO-08", 5, "2026-07-P2", 745_900, ResultadoMatch.MATCH),
    # --- MATCH dentro de tolerancia: |dif| ≤ 100 centavos, el mínimo absoluto (5) --------
    PlanCfdi("M-TOLER-01", 0, "2026-06-P2", 812_450, ResultadoMatch.MATCH, 35),
    PlanCfdi("M-TOLER-02", 4, "2026-06-P2", 452_300, ResultadoMatch.MATCH, -12),
    PlanCfdi("M-TOLER-03", 1, "2026-07-P1", 301_750, ResultadoMatch.MATCH, -80),
    PlanCfdi("M-TOLER-04", 5, "2026-07-P1", 690_250, ResultadoMatch.MATCH, 64),
    PlanCfdi("M-TOLER-05", 2, "2026-07-P2", 1_498_750, ResultadoMatch.MATCH, 100),
    # --- PARCIAL: diferencias de entre 50 y 900 pesos (5) --------------------------------
    PlanCfdi("P-PARCIAL-01", 1, "2026-06-P2", 268_900, ResultadoMatch.PARCIAL, 12_500),
    PlanCfdi("P-PARCIAL-02", 2, "2026-07-P1", 1_612_300, ResultadoMatch.PARCIAL, -90_000),
    PlanCfdi("P-PARCIAL-03", 3, "2026-07-P1", 118_250, ResultadoMatch.PARCIAL, 8_900),
    PlanCfdi("P-PARCIAL-04", 0, "2026-07-P2", 1_105_300, ResultadoMatch.PARCIAL, -27_350),
    PlanCfdi("P-PARCIAL-05", 4, "2026-07-P2", 510_050, ResultadoMatch.PARCIAL, 45_000),
    # --- NO_MATCH: quincena sin reporte de venta (3) -------------------------------------
    PlanCfdi("N-NOMATCH-01", 0, PERIODO_SIN_REPORTE, 876_300, ResultadoMatch.NO_MATCH),
    PlanCfdi("N-NOMATCH-02", 2, PERIODO_SIN_REPORTE, 1_402_500, ResultadoMatch.NO_MATCH),
    PlanCfdi("N-NOMATCH-03", 5, PERIODO_SIN_REPORTE, 655_400, ResultadoMatch.NO_MATCH),
    # --- RECHAZADOS por validación fiscal (3) -------------------------------------------
    # Van en una quincena con reporte a propósito: se rechazan ANTES de conciliar, así que
    # no compiten por la celda de la rejilla.
    PlanCfdi("R-RECHAZO-01", 0, "2026-07-P2", 415_000, None, defecto=DEFECTO_TOTALES, serie="B"),
    PlanCfdi("R-RECHAZO-02", 1, "2026-07-P2", 238_600, None, defecto=DEFECTO_RECEPTOR, serie="B"),
    PlanCfdi("R-RECHAZO-03", 3, "2026-07-P2", 87_900, None, defecto=DEFECTO_VERSION, serie="B"),
)

#: Contra qué importe del CFDI se calibra la comisión devengada del reporte de venta.
#: `conciliar_cfdi()` compara el TOTAL del comprobante, así que ese es el primer intento;
#: "subtotal" queda como red de seguridad por si el motor cambiara de criterio.
BASES_DE_CALIBRACION: tuple[str, ...] = ("total", "subtotal")


# ========================================================================================
# Utilidades
# ========================================================================================

def _pesos(centavos: int) -> str:
    """Centavos → '$1,234.56' (el signo va al frente). Decimal, nunca float."""
    valor = (Decimal(abs(centavos)) / Decimal(100)).quantize(Decimal("0.01"))
    return f"{'-' if centavos < 0 else ''}${valor:,.2f}"


def _uuid_de(clave: str) -> str:
    """UUID determinista: dos siembras seguidas producen exactamente los mismos folios."""
    return str(uuid.uuid5(_NS_SEMILLA, clave))


def _fecha_de(plan: PlanCfdi) -> date:
    return _PRIMER_DIA[plan.periodo] + timedelta(days=plan.agencia)


def _periodo_de(fecha: date) -> str:
    """Quincena a la que pertenece una fecha. Misma regla que usa `routers_cfdi`."""
    return f"{fecha.year:04d}-{fecha.month:02d}-P{1 if fecha.day <= 15 else 2}"


def _avisar_contrato_periodo() -> None:
    """El modelo declara `periodo` con String(9) y las quincenas miden 10 caracteres."""
    largo = getattr(Cfdi.__table__.c.periodo.type, "length", None)
    necesario = max(len(p) for p in (*PERIODOS, PERIODO_SIN_REPORTE))
    if largo is not None and largo < necesario:
        print(
            f"  AVISO · el modelo declara periodo String({largo}) y '{PERIODOS[0]}' mide "
            f"{necesario}. En PostgreSQL la inserción falla hasta que la columna crezca a "
            f"String({necesario}); en SQLite pasa desapercibido."
        )


# ========================================================================================
# Construcción de las filas
# ========================================================================================

def _limpiar(db) -> None:
    """Borra todo en orden seguro de llaves foráneas."""
    for modelo in (EventoAuditoria, Conciliacion, Validacion, Cfdi, ReporteVenta, Usuario, Agencia):
        db.query(modelo).delete(synchronize_session=False)
    db.commit()
    db.expire_all()


def _crear_agencias(db) -> list[Agencia]:
    agencias = [Agencia(rfc=rfc, razon_social=razon, activa=True) for rfc, razon in AGENCIAS]
    db.add_all(agencias)
    db.flush()
    return agencias


def _crear_usuarios(db, agencias: list[Agencia]) -> dict[str, Usuario]:
    """1 ADMIN + 2 ANALISTA + 1 usuario de rol AGENCIA por cada agencia."""
    clave = hash_password(PASSWORD_DEMO)
    usuarios = [
        Usuario(email="admin@fiscalmatch.dev", nombre="Administrador FiscalMatch",
                rol=Rol.ADMIN, password_hash=clave),
        Usuario(email="analista1@fiscalmatch.dev", nombre="Renata Salcedo",
                rol=Rol.ANALISTA, password_hash=clave),
        Usuario(email="analista2@fiscalmatch.dev", nombre="Iván Berrones",
                rol=Rol.ANALISTA, password_hash=clave),
    ]
    usuarios += [
        Usuario(
            email=f"agencia{indice + 1}@fiscalmatch.dev",
            nombre=CONTACTOS_AGENCIA[indice],
            rol=Rol.AGENCIA,
            password_hash=clave,
            agencia_id=agencia.id,
        )
        for indice, agencia in enumerate(agencias)
    ]
    db.add_all(usuarios)
    db.flush()
    return {usuario.email: usuario for usuario in usuarios}


def _xml_del_plan(plan: PlanCfdi, agencia: Agencia, rfc_receptor_esperado: str, folio: int) -> str:
    """Genera el XML del comprobante.

    Los importes y el receptor se tuercen aquí —no en la base— para que el defecto viaje
    DENTRO del documento y sea el validador real quien lo descubra.
    """
    subtotal = plan.subtotal_centavos
    impuestos = calcular_iva_centavos(subtotal)
    total = subtotal + impuestos
    receptor = rfc_receptor_esperado
    version = "4.0"

    if plan.defecto == DEFECTO_TOTALES:
        total += DESCUADRE_CENTAVOS          # subtotal + impuestos ≠ total
    elif plan.defecto == DEFECTO_RECEPTOR:
        receptor = RFC_RECEPTOR_EQUIVOCADO   # emitido a otra empresa
    elif plan.defecto == DEFECTO_VERSION:
        version = "3.3"                      # versión fuera de vigencia

    fecha = _fecha_de(plan)
    return construir_cfdi_xml(
        uuid_str=_uuid_de(plan.clave),
        rfc_emisor=agencia.rfc,
        nombre_emisor=agencia.razon_social,
        rfc_receptor=receptor,
        serie=plan.serie,
        folio=str(folio),
        fecha_iso=fecha.isoformat(),
        subtotal_centavos=subtotal,
        impuestos_centavos=impuestos,
        total_centavos=total,
        no_certificado=f"3000100000050000{3400 + plan.agencia:04d}",
        version=version,
        descripcion=f"Comisión por intermediación en venta de servicios de viaje · {plan.periodo}",
    )


def _crear_y_validar_cfdis(db, agencias, usuarios, settings) -> dict[str, Cfdi]:
    """Persiste cada CFDI a partir de lo que dice su XML y corre la validación REAL.

    Es el mismo camino que sigue `POST /api/cfdis`: parsear → validar → guardar el
    comprobante con su desglose de reglas, incluso si quedó rechazado.
    """
    rfcs_registrados = {agencia.rfc for agencia in agencias}
    uuids_previos: set[str] = set()
    creados: dict[str, Cfdi] = {}
    folios: dict[int, int] = {}

    for plan in PLAN:
        agencia = agencias[plan.agencia]
        cargador = usuarios[f"agencia{plan.agencia + 1}@fiscalmatch.dev"]
        folio = folios[plan.agencia] = folios.get(plan.agencia, 1000) + 1

        xml = _xml_del_plan(plan, agencia, settings.rfc_receptor_esperado, folio)
        parseado = parsear_cfdi(xml)

        periodo = _periodo_de(parseado.fecha_emision)
        if periodo != plan.periodo:  # el guion y la regla de quincenas deben coincidir
            raise SystemExit(
                f"{plan.clave}: la fecha {parseado.fecha_emision} cae en la quincena "
                f"{periodo} y el guion la puso en {plan.periodo}."
            )

        reglas = validar(
            parseado,
            xml_texto=xml,
            rfc_receptor_esperado=settings.rfc_receptor_esperado,
            rfcs_agencias_registradas=rfcs_registrados,
            uuids_existentes=uuids_previos,
        )
        paso = aprobado(reglas)

        cfdi = Cfdi(
            uuid=parseado.uuid,
            rfc_emisor=parseado.rfc_emisor,
            rfc_receptor=parseado.rfc_receptor,
            serie=parseado.serie,
            folio=parseado.folio,
            fecha_emision=parseado.fecha_emision,
            periodo=periodo,
            subtotal_centavos=parseado.subtotal_centavos,
            impuestos_centavos=parseado.impuestos_centavos,
            total_centavos=parseado.total_centavos,
            moneda=parseado.moneda,
            no_certificado=parseado.no_certificado,
            xml_original=xml,
            nombre_archivo=f"{agencia.rfc}-{plan.serie}{folio}.xml",
            estado=EstadoCfdi.VALIDADO if paso else EstadoCfdi.RECHAZADO,
            agencia_id=agencia.id,
            cargado_por_id=cargador.id,
        )
        db.add(cfdi)
        db.flush()

        for regla in reglas:
            db.add(
                Validacion(
                    cfdi_id=cfdi.id,
                    regla=regla.regla[:60],
                    aprobada=regla.aprobada,
                    detalle=(regla.detalle or "")[:500],
                )
            )

        if paso is not plan.conciliable:
            estado = "pasó" if paso else "fue rechazado"
            raise SystemExit(
                f"{plan.clave}: el guion lo planeó "
                f"{'limpio' if plan.conciliable else 'defectuoso'} pero {estado} la validación."
            )

        uuids_previos.add(parseado.uuid)
        creados[plan.clave] = cfdi

    db.flush()
    return creados


def _sembrar_reportes(db, agencias: list[Agencia], cfdis: dict[str, Cfdi], base: str) -> int:
    """Un reporte de venta por cada (quincena, agencia) de la rejilla: 3 × 6 = 18.

    La comisión devengada se calibra restando la desviación planeada al importe del CFDI,
    de modo que el motor de conciliación produzca exactamente la diferencia buscada.
    """
    por_celda = {
        (plan.periodo, plan.agencia): plan
        for plan in PLAN
        if plan.conciliable and plan.periodo in PERIODOS
    }
    creados = 0
    for periodo in PERIODOS:
        for indice, agencia in enumerate(agencias):
            plan = por_celda.get((periodo, indice))
            if plan is None:
                continue
            cfdi = cfdis[plan.clave]
            referencia = cfdi.total_centavos if base == "total" else cfdi.subtotal_centavos
            comision = referencia - plan.desviacion_centavos
            db.add(
                ReporteVenta(
                    periodo=periodo,
                    rfc_agencia=agencia.rfc,
                    comision_centavos=comision,
                    # Ticket promedio de comisión: ~$425.00 por boleto.
                    boletos=max(1, comision // 42_500),
                    referencia=f"RV-{periodo}-{agencia.rfc}",
                )
            )
            creados += 1
    db.flush()
    return creados


def _conciliar_todos(db, cfdis: dict[str, Cfdi], settings) -> None:
    """Corre el motor real sobre cada CFDI validado. Los rechazados ni se tocan."""
    for cfdi in cfdis.values():
        if cfdi.estado is not EstadoCfdi.VALIDADO:
            continue
        conciliar_cfdi(
            db,
            cfdi,
            tolerancia_centavos=settings.tolerancia_centavos,
            tolerancia_porcentaje=settings.tolerancia_porcentaje,
        )
    db.flush()


def _deshacer_conciliacion(db, cfdis: dict[str, Cfdi]) -> None:
    """Vuelve al punto posterior a la validación para reintentar con otra calibración."""
    db.query(Conciliacion).delete(synchronize_session=False)
    db.query(ReporteVenta).delete(synchronize_session=False)
    db.flush()
    db.expire_all()
    for cfdi in cfdis.values():
        if cfdi.estado in (EstadoCfdi.CONCILIADO, EstadoCfdi.EN_EXCEPCION):
            cfdi.estado = EstadoCfdi.VALIDADO
    db.flush()


def _desviados(db, cfdis: dict[str, Cfdi]) -> list[str]:
    """CFDIs conciliables cuyo veredicto real NO fue el que planeó el guion."""
    fuera: list[str] = []
    for plan in PLAN:
        if not plan.conciliable:
            continue
        conciliacion = db.query(Conciliacion).filter(
            Conciliacion.cfdi_id == cfdis[plan.clave].id
        ).one_or_none()
        obtenido = conciliacion.resultado if conciliacion is not None else None
        if obtenido is not plan.resultado:
            fuera.append(
                f"{plan.clave}: se planeó {plan.resultado.value} y el motor dijo "
                f"{obtenido.value if obtenido else '(sin conciliar)'}"
            )
    return fuera


def _resolver_una_excepcion(db, analista: Usuario) -> Conciliacion | None:
    """Un analista cierra a mano la excepción con mayor sobrefacturación.

    Se usa `resolver_manual()` —el mismo servicio que atiende el endpoint de la cola de
    excepciones— para que la fila quede con método MANUAL, nota y responsable, tal como
    la dejaría una resolución hecha desde el portal.
    """
    candidata = (
        db.query(Conciliacion)
        .filter(Conciliacion.resultado == ResultadoMatch.PARCIAL)
        .order_by(Conciliacion.diferencia_centavos.desc())
        .first()
    )
    if candidata is None:
        return None
    return resolver_manual(
        db,
        candidata,
        usuario_id=analista.id,
        nota=(
            f"Diferencia de {_pesos(candidata.diferencia_centavos)} revisada contra el "
            "contrato de comisiones: corresponde a un ajuste ya autorizado del periodo "
            "anterior. Se acepta y se libera el comprobante."
        ),
        aceptar=True,
    )


def _sembrar_auditoria(
    db,
    cfdis: dict[str, Cfdi],
    usuarios: dict[str, Usuario],
    resuelta: Conciliacion | None,
    reportes: int,
) -> int:
    """Bitácora de ejemplo: sesión, carga, dictamen y resolución de excepción."""
    eventos: list[EventoAuditoria] = [
        EventoAuditoria(
            actor="sistema", accion="SIEMBRA_INICIAL", entidad="BASE", entidad_id="0",
            detalle="Juego de datos de demostración cargado por seed/sembrar.py",
        ),
        EventoAuditoria(
            actor="admin@fiscalmatch.dev", accion="CARGA_REPORTE_VENTA",
            entidad="ReporteVenta", entidad_id="*",
            detalle=f"{reportes} reportes de venta cargados para {', '.join(PERIODOS)}",
        ),
    ]
    for email in ("admin@fiscalmatch.dev", "analista1@fiscalmatch.dev", "agencia1@fiscalmatch.dev"):
        usuario = usuarios[email]
        eventos.append(
            EventoAuditoria(
                actor=email, accion="INICIO_SESION", entidad="Usuario",
                entidad_id=str(usuario.id), detalle=f"Acceso con rol {usuario.rol.value}",
            )
        )

    for plan in PLAN:
        cfdi = cfdis[plan.clave]
        eventos.append(
            EventoAuditoria(
                actor=f"agencia{plan.agencia + 1}@fiscalmatch.dev", accion="CARGA_CFDI",
                entidad="Cfdi", entidad_id=str(cfdi.id),
                detalle=(
                    f"{cfdi.nombre_archivo} · UUID {cfdi.uuid} · quincena {cfdi.periodo} · "
                    f"total {_pesos(cfdi.total_centavos)} · dictamen {cfdi.estado.value}"
                ),
            )
        )

    if resuelta is not None:
        eventos.append(
            EventoAuditoria(
                actor="analista1@fiscalmatch.dev", accion="RESOLUCION_EXCEPCION",
                entidad="Conciliacion", entidad_id=str(resuelta.id),
                detalle=resuelta.nota_resolucion or "",
            )
        )

    db.add_all(eventos)
    db.flush()
    return len(eventos)


# ========================================================================================
# Resumen
# ========================================================================================

def _tabla(titulo: str, conteos: list[tuple[str, int, str]], universo: int) -> None:
    print(f"\n  {titulo}")
    for etiqueta, cantidad, extra in conteos:
        proporcion = f"{cantidad / universo:>6.1%}" if universo else "     -"
        print(f"    {etiqueta:<14} {cantidad:>3}  {proporcion}   {extra}".rstrip())


def _imprimir_resumen(db, base: str, automatico: dict[ResultadoMatch, int]) -> None:
    linea = "─" * 78
    agencias = db.query(Agencia).order_by(Agencia.id).all()
    usuarios = db.query(Usuario).order_by(Usuario.id).all()
    cfdis = db.query(Cfdi).all()
    conciliaciones = db.query(Conciliacion).all()
    total = len(cfdis)

    print(f"\n{linea}\nFISCALMATCH · base sembrada\n{linea}")
    print(f"  Agencias {len(agencias)} · Usuarios {len(usuarios)} · "
          f"Reportes de venta {db.query(ReporteVenta).count()} · CFDIs {total} · "
          f"Validaciones {db.query(Validacion).count()} · "
          f"Eventos de auditoría {db.query(EventoAuditoria).count()}")
    print("  Dictamen: app.services.validacion · Conciliación: app.services.conciliacion")
    print(f"  Comisión devengada calibrada contra el {base.upper()} del CFDI")

    _tabla(
        "CFDIs por estado",
        [(estado.value, sum(1 for c in cfdis if c.estado is estado), "")
         for estado in EstadoCfdi if any(c.estado is estado for c in cfdis)],
        total,
    )

    _tabla(
        f"Veredicto AUTOMÁTICO del motor ({sum(automatico.values())} conciliados; "
        f"los {total - sum(automatico.values())} RECHAZADOS no llegan a conciliación)",
        [(resultado.value, automatico[resultado], "")
         for resultado in ResultadoMatch if automatico.get(resultado)],
        total,
    )

    _tabla(
        "Resultado FINAL (tras la resolución del analista)",
        [
            (
                resultado.value,
                len([c for c in conciliaciones if c.resultado is resultado]),
                f"dif. máx {_pesos(max((abs(c.diferencia_centavos) for c in conciliaciones if c.resultado is resultado), default=0))}",
            )
            for resultado in ResultadoMatch
            if any(c.resultado is resultado for c in conciliaciones)
        ],
        total,
    )

    manuales = [c for c in conciliaciones if c.metodo is MetodoMatch.MANUAL]
    if manuales:
        print(f"\n  Excepciones cerradas a mano: {len(manuales)} "
              f"(método MANUAL, con analista responsable y nota en el expediente)")

    reprobadas = (
        db.query(Cfdi.nombre_archivo, Validacion.regla, Validacion.detalle)
        .join(Validacion, Validacion.cfdi_id == Cfdi.id)
        .filter(Validacion.aprobada.is_(False))
        .order_by(Validacion.cfdi_id)
        .all()
    )
    if reprobadas:
        print(f"\n  Reglas fiscales reprobadas ({len(reprobadas)})")
        for archivo, regla, detalle in reprobadas:
            print(f"    {archivo:<22} {regla:<20} {detalle[:70]}")

    print(f"\n{linea}\nCREDENCIALES · la contraseña de TODOS es: {PASSWORD_DEMO}\n{linea}")
    for usuario in usuarios:
        agencia = next((a for a in agencias if a.id == usuario.agencia_id), None)
        sufijo = f"  →  {agencia.razon_social} ({agencia.rfc})" if agencia else ""
        print(f"  {usuario.rol.value:<9} {usuario.email:<28}{sufijo}")
    print(linea)


# ========================================================================================
# Orquestación
# ========================================================================================

def sembrar(reset: bool = True) -> None:
    settings = get_settings()
    Base.metadata.create_all(engine)
    _avisar_contrato_periodo()

    db = SessionLocal()
    try:
        if db.query(Agencia).count():
            if not reset:
                raise SystemExit(
                    "La base ya tiene agencias y se pidió --no-reset. Corre sin la bandera "
                    "(o con --reset) para borrar y volver a sembrar."
                )
            _limpiar(db)

        agencias = _crear_agencias(db)
        usuarios = _crear_usuarios(db, agencias)
        cfdis = _crear_y_validar_cfdis(db, agencias, usuarios, settings)

        # Calibración: se prueban las bases en orden y se conserva la primera que
        # reproduce el guion completo. Si ninguna lo logra, se avisa en vez de callar.
        elegida, reportes, desviados = BASES_DE_CALIBRACION[0], 0, []
        for base in BASES_DE_CALIBRACION:
            _deshacer_conciliacion(db, cfdis)
            reportes = _sembrar_reportes(db, agencias, cfdis, base)
            _conciliar_todos(db, cfdis, settings)
            elegida, desviados = base, _desviados(db, cfdis)
            if not desviados:
                break

        automatico = {
            resultado: db.query(Conciliacion).filter(Conciliacion.resultado == resultado).count()
            for resultado in ResultadoMatch
        }

        resuelta = _resolver_una_excepcion(db, usuarios["analista1@fiscalmatch.dev"])
        _sembrar_auditoria(db, cfdis, usuarios, resuelta, reportes)
        db.commit()

        if desviados:
            print(f"\n  AVISO · {len(desviados)} CFDIs no cayeron donde los puso el guion; "
                  "el motor real aplica un criterio distinto:")
            for detalle in desviados:
                print(f"    · {detalle}")

        _imprimir_resumen(db, elegida, automatico)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Siembra la base de demostración de FiscalMatch.")
    parser.add_argument(
        "--reset",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Borra todo antes de sembrar (activado por omisión).",
    )
    sembrar(reset=parser.parse_args().reset)


if __name__ == "__main__":
    main()
