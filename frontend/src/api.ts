/**
 * Cliente HTTP de FiscalMatch.
 *
 * El token JWT vive en localStorage y viaja en `Authorization: Bearer ...`.
 * El ROL sale del propio token (claim `rol`), no de un estado aparte: asi el
 * portal no puede quedar mostrando la vista de analista con un token de agencia.
 * El token NO trae nombre ni correo, asi que el perfil de pantalla se toma de
 * la respuesta del login (`usuario`) y se refresca contra `/auth/yo`.
 *
 * Superficie de API verificada contra los routers del backend:
 *   POST /api/auth/login              -> TokenOut { access_token, usuario, ... }
 *   GET  /api/auth/yo                 -> UsuarioOut
 *   POST /api/cfdis     (multipart)   -> CfdiDetalleOut   campo `archivo`
 *   GET  /api/cfdis                   -> CfdiOut[]        (AGENCIA solo ve lo suyo)
 *   GET  /api/cfdis/{id}              -> CfdiDetalleOut
 *
 * Superficie del panel de analista. Los esquemas ya existen (`ResumenOut`,
 * `ResolverIn`, `EventoAuditoriaOut`) pero el router aun no se publica, asi que
 * cada lectura degrada con dignidad si responde 404:
 *   GET  /api/conciliaciones/resumen        -> ResumenOut          | se calcula en el navegador
 *   POST /api/conciliaciones/{id}/resolver  -> ConciliacionOut     | sin respaldo: no se adivina una mutacion
 *   GET  /api/conciliaciones/export.csv     -> text/csv            | se arma en el navegador
 *   GET  /api/auditoria                     -> EventoAuditoriaOut[]| sin respaldo: es la fuente de verdad
 *
 * La cola de excepciones NO usa endpoint propio: sale de `GET /cfdis?estado=EN_EXCEPCION`,
 * que ya devuelve la conciliacion anidada.
 *
 * REGLA: el dinero llega y se compara SIEMPRE en centavos enteros. La division
 * entre 100 ocurre unicamente al pintar la pantalla.
 */

export const BASE_API: string = import.meta.env.VITE_API_BASE ?? '/api'

const CLAVE_TOKEN = 'fiscalmatch.token'
const CLAVE_PERFIL = 'fiscalmatch.perfil'

/** Evento que dispara el cliente cuando la API responde 401. */
export const EVENTO_SESION_EXPIRADA = 'fiscalmatch:sesion-expirada'

/* ------------------------------------------------------------------ tipos */

export type Rol = 'AGENCIA' | 'ANALISTA' | 'ADMIN'

export type EstadoCfdi =
  | 'RECIBIDO'
  | 'VALIDADO'
  | 'RECHAZADO'
  | 'CONCILIADO'
  | 'EN_EXCEPCION'

export type ResultadoMatch = 'MATCH' | 'PARCIAL' | 'NO_MATCH'

export type MetodoMatch = 'AUTOMATICO' | 'MANUAL'

export interface AgenciaDTO {
  id: number
  rfc: string
  razon_social: string
  activa: boolean
}

export interface UsuarioDTO {
  id: number
  email: string
  nombre: string
  rol: Rol
  activo: boolean
  agencia_id: number | null
  agencia: AgenciaDTO | null
}

export interface TokenDTO {
  access_token: string
  token_type: string
  expira_en_minutos: number
  usuario: UsuarioDTO
}

export interface ValidacionDTO {
  id: number
  regla: string
  aprobada: boolean
  detalle: string
  evaluada_en: string
}

export interface ConciliacionDTO {
  id: number
  cfdi_id: number
  reporte_id: number | null
  resultado: ResultadoMatch
  /** Signo conservado: positiva = el CFDI factura de MAS que la comision devengada. */
  diferencia_centavos: number
  motivo: string
  metodo: MetodoMatch
  resuelta_por_id: number | null
  nota_resolucion: string | null
  conciliada_en: string
}

export interface CfdiDTO {
  id: number
  uuid: string
  rfc_emisor: string
  rfc_receptor: string
  serie: string | null
  folio: string | null
  fecha_emision: string
  periodo: string
  subtotal_centavos: number
  impuestos_centavos: number
  total_centavos: number
  moneda: string
  nombre_archivo: string
  estado: EstadoCfdi
  agencia_id: number
  cargado_por_id: number | null
  cargado_en: string
  conciliacion: ConciliacionDTO | null
  /** Campos calculados por el backend a partir de la conciliacion. */
  resultado: ResultadoMatch | null
  diferencia_centavos: number | null
}

export interface CfdiDetalleDTO extends CfdiDTO {
  validaciones: ValidacionDTO[]
}

export interface ResumenDTO {
  periodo: string | null
  total_cfdis: number
  por_estado: Record<string, number>
  por_resultado: Record<string, number>
  total_facturado_centavos: number
  total_conciliado_centavos: number
  total_reportado_centavos: number
  diferencia_total_centavos: number
  /** Bandera del cliente: el resumen se calculo aqui sobre una pagina, no lo dio la API. */
  aproximado?: boolean
}

export interface EventoDTO {
  id: number
  actor: string
  accion: string
  entidad: string
  entidad_id: string
  detalle: string
  ocurrido_en: string
}

/** Vista de trabajo de la cola: el CFDI y su conciliacion, ya aplanados. */
export interface Excepcion {
  conciliacion_id: number
  cfdi: CfdiDTO
  resultado: ResultadoMatch
  diferencia_centavos: number
  /** Comision segun el reporte de venta; null si no hubo reporte contra que cruzar. */
  reportado_centavos: number | null
  motivo: string
}

export interface Sesion {
  token: string
  usuario_id: number
  rol: Rol
  agencia_id: number | null
  email: string
  nombre: string
  agencia: string | null
  rfc_agencia: string | null
}

/* -------------------------------------------------- token y perfil locales */

function leerLocal(clave: string): string | null {
  try {
    return window.localStorage.getItem(clave)
  } catch {
    return null
  }
}

function escribirLocal(clave: string, valor: string): void {
  try {
    window.localStorage.setItem(clave, valor)
  } catch {
    /* modo privado o almacenamiento bloqueado: la sesion dura lo que la pestana */
  }
}

function borrarLocal(clave: string): void {
  try {
    window.localStorage.removeItem(clave)
  } catch {
    /* nada que limpiar */
  }
}

export function leerToken(): string | null {
  return leerLocal(CLAVE_TOKEN)
}

function leerPerfil(): UsuarioDTO | null {
  const crudo = leerLocal(CLAVE_PERFIL)
  if (!crudo) return null
  try {
    return JSON.parse(crudo) as UsuarioDTO
  } catch {
    return null
  }
}

function guardarPerfil(usuario: UsuarioDTO): void {
  escribirLocal(CLAVE_PERFIL, JSON.stringify(usuario))
}

function limpiarSesion(): void {
  borrarLocal(CLAVE_TOKEN)
  borrarLocal(CLAVE_PERFIL)
}

/** Decodifica el payload del JWT (sin verificar firma: eso le toca al backend). */
function decodificarPayload(token: string): Record<string, unknown> | null {
  const partes = token.split('.')
  if (partes.length !== 3) return null
  try {
    const base64 = partes[1].replace(/-/g, '+').replace(/_/g, '/')
    const relleno = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
    const binario = atob(relleno)
    const bytes = Uint8Array.from(binario, (c) => c.charCodeAt(0))
    return JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>
  } catch {
    return null
  }
}

/**
 * Reconstruye la sesion desde el token guardado.
 * Devuelve null si no hay token, si no se puede leer o si ya expiro.
 */
export function leerSesion(): Sesion | null {
  const token = leerToken()
  if (!token) return null

  const cuerpo = decodificarPayload(token)
  if (!cuerpo) return null

  // El token trae `exp` en segundos. Si ya paso, ni se intenta usar.
  const expira = typeof cuerpo.exp === 'number' ? cuerpo.exp : null
  if (expira !== null && expira * 1000 <= Date.now()) {
    limpiarSesion()
    return null
  }

  const rolCrudo = typeof cuerpo.rol === 'string' ? cuerpo.rol : 'AGENCIA'
  const rol: Rol = (['AGENCIA', 'ANALISTA', 'ADMIN'] as const).includes(rolCrudo as Rol)
    ? (rolCrudo as Rol)
    : 'AGENCIA'

  const usuarioId = Number(cuerpo.sub)
  const perfil = leerPerfil()

  return {
    token,
    usuario_id: Number.isFinite(usuarioId) ? usuarioId : 0,
    rol,
    agencia_id: typeof cuerpo.agencia_id === 'number' ? cuerpo.agencia_id : null,
    email: perfil?.email ?? '',
    nombre: perfil?.nombre ?? perfil?.email ?? '',
    agencia: perfil?.agencia?.razon_social ?? null,
    rfc_agencia: perfil?.agencia?.rfc ?? null,
  }
}

/* ------------------------------------------------------------------ fetch */

export class ErrorApi extends Error {
  readonly estado: number

  constructor(estado: number, mensaje: string) {
    super(mensaje)
    this.name = 'ErrorApi'
    this.estado = estado
  }
}

/** Saca un mensaje legible del cuerpo de error (formato `detail` de FastAPI). */
async function mensajeDeError(respuesta: Response): Promise<string> {
  try {
    const cuerpo: unknown = await respuesta.json()
    if (typeof cuerpo === 'string') return cuerpo
    if (cuerpo && typeof cuerpo === 'object') {
      const detalle = (cuerpo as { detail?: unknown }).detail
      if (typeof detalle === 'string') return detalle
      if (Array.isArray(detalle)) {
        const partes = detalle
          .map((d) =>
            d && typeof d === 'object' ? String((d as { msg?: unknown }).msg ?? '') : String(d),
          )
          .filter(Boolean)
        if (partes.length > 0) return partes.join(' · ')
      }
    }
  } catch {
    /* el cuerpo no era JSON */
  }
  return `Error ${respuesta.status} al llamar la API`
}

async function pedirCrudo(ruta: string, opciones: RequestInit = {}): Promise<Response> {
  const cabeceras = new Headers(opciones.headers)
  const token = leerToken()
  if (token) cabeceras.set('Authorization', `Bearer ${token}`)
  // FormData pone su propio Content-Type con el `boundary`: no se toca.
  if (opciones.body !== undefined && !(opciones.body instanceof FormData)) {
    cabeceras.set('Content-Type', 'application/json')
  }
  if (!cabeceras.has('Accept')) cabeceras.set('Accept', 'application/json')

  let respuesta: Response
  try {
    respuesta = await fetch(`${BASE_API}${ruta}`, { ...opciones, headers: cabeceras })
  } catch {
    throw new ErrorApi(0, 'No se pudo contactar la API. Revisa que el servicio este arriba.')
  }

  if (respuesta.status === 401) {
    limpiarSesion()
    window.dispatchEvent(new CustomEvent(EVENTO_SESION_EXPIRADA))
    throw new ErrorApi(401, 'La sesion expiro o las credenciales no son validas.')
  }
  if (!respuesta.ok) {
    throw new ErrorApi(respuesta.status, await mensajeDeError(respuesta))
  }
  return respuesta
}

async function pedir<T>(ruta: string, opciones: RequestInit = {}): Promise<T> {
  const respuesta = await pedirCrudo(ruta, opciones)
  if (respuesta.status === 204) return undefined as T
  return (await respuesta.json()) as T
}

/** `true` si la ruta no existe todavia en el backend. */
function esRutaAusente(error: unknown): boolean {
  return error instanceof ErrorApi && (error.estado === 404 || error.estado === 405)
}

/* -------------------------------------------------------------- identidad */

/** Autentica, guarda token y perfil, y devuelve la sesion ya decodificada. */
export async function iniciarSesion(email: string, password: string): Promise<Sesion> {
  const datos = await pedir<TokenDTO>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  if (!datos.access_token) throw new ErrorApi(500, 'La API no devolvio un token de acceso.')

  escribirLocal(CLAVE_TOKEN, datos.access_token)
  if (datos.usuario) guardarPerfil(datos.usuario)

  const sesion = leerSesion()
  if (!sesion) {
    limpiarSesion()
    throw new ErrorApi(500, 'El token recibido no se pudo interpretar.')
  }
  return sesion
}

/** Revalida el token contra la API y refresca los datos de pantalla. */
export async function refrescarPerfil(): Promise<Sesion | null> {
  const usuario = await pedir<UsuarioDTO>('/auth/yo')
  guardarPerfil(usuario)
  return leerSesion()
}

export function cerrarSesion(): void {
  limpiarSesion()
}

/* ------------------------------------------------------------------ CFDIs */

export interface FiltroCfdis {
  estado?: EstadoCfdi
  periodo?: string
  resultado?: ResultadoMatch
  limit?: number
  offset?: number
}

function comoParametros(filtro: FiltroCfdis): string {
  const parametros = new URLSearchParams()
  if (filtro.estado) parametros.set('estado', filtro.estado)
  if (filtro.periodo) parametros.set('periodo', filtro.periodo)
  if (filtro.resultado) parametros.set('resultado', filtro.resultado)
  parametros.set('limit', String(filtro.limit ?? 200))
  if (filtro.offset) parametros.set('offset', String(filtro.offset))
  return parametros.toString()
}

export function listarCfdis(filtro: FiltroCfdis = {}): Promise<CfdiDTO[]> {
  return pedir<CfdiDTO[]>(`/cfdis?${comoParametros(filtro)}`)
}

export function obtenerCfdi(id: number): Promise<CfdiDetalleDTO> {
  return pedir<CfdiDetalleDTO>(`/cfdis/${id}`)
}

/** Sube UN XML. El backend valida, concilia y devuelve el CFDI ya con veredicto. */
export function cargarCfdi(archivo: File): Promise<CfdiDetalleDTO> {
  const formulario = new FormData()
  formulario.append('archivo', archivo, archivo.name)
  return pedir<CfdiDetalleDTO>('/cfdis', { method: 'POST', body: formulario })
}

/* --------------------------------------------------- panel de conciliacion */

/**
 * Comision del reporte de venta = total del CFDI menos la diferencia.
 * El motor define la diferencia como `total_cfdi - comision_reportada`, asi que
 * el dato se reconstruye con aritmetica entera exacta, sin pedir otro campo.
 * Sin `reporte_id` no hubo contra que cruzar: no se inventa un cero.
 */
export function reportadoCentavos(conciliacion: ConciliacionDTO | null, totalCfdi: number): number | null {
  if (!conciliacion || conciliacion.reporte_id === null) return null
  return totalCfdi - conciliacion.diferencia_centavos
}

function aExcepcion(cfdi: CfdiDTO): Excepcion | null {
  if (!cfdi.conciliacion) return null
  return {
    conciliacion_id: cfdi.conciliacion.id,
    cfdi,
    resultado: cfdi.conciliacion.resultado,
    diferencia_centavos: cfdi.conciliacion.diferencia_centavos,
    reportado_centavos: reportadoCentavos(cfdi.conciliacion, cfdi.total_centavos),
    motivo: cfdi.conciliacion.motivo,
  }
}

/** Cola de excepciones: los CFDIs que el motor no pudo cerrar. */
export async function listarExcepciones(): Promise<Excepcion[]> {
  const cfdis = await listarCfdis({ estado: 'EN_EXCEPCION', limit: 200 })
  return cfdis.map(aExcepcion).filter((e): e is Excepcion => e !== null)
}

function resumenVacio(): ResumenDTO {
  return {
    periodo: null,
    total_cfdis: 0,
    por_estado: {},
    por_resultado: {},
    total_facturado_centavos: 0,
    total_conciliado_centavos: 0,
    total_reportado_centavos: 0,
    diferencia_total_centavos: 0,
  }
}

/** Mismo agregado que `ResumenOut`, calculado sobre la pagina que ya se trajo. */
function resumenDesde(cfdis: CfdiDTO[]): ResumenDTO {
  const resumen = resumenVacio()
  resumen.total_cfdis = cfdis.length
  resumen.aproximado = true

  for (const cfdi of cfdis) {
    resumen.por_estado[cfdi.estado] = (resumen.por_estado[cfdi.estado] ?? 0) + 1
    resumen.total_facturado_centavos += cfdi.total_centavos

    if (cfdi.estado === 'CONCILIADO') {
      resumen.total_conciliado_centavos += cfdi.total_centavos
    }

    const conciliacion = cfdi.conciliacion
    if (!conciliacion) continue

    resumen.por_resultado[conciliacion.resultado] =
      (resumen.por_resultado[conciliacion.resultado] ?? 0) + 1
    resumen.diferencia_total_centavos += conciliacion.diferencia_centavos

    // Solo suma lo que de verdad tiene contraparte en el reporte de venta.
    const reportado = reportadoCentavos(conciliacion, cfdi.total_centavos)
    if (reportado !== null) resumen.total_reportado_centavos += reportado
  }
  return resumen
}

/**
 * Tablero del analista. Usa el endpoint dedicado; si aun no existe, calcula el
 * mismo agregado en el navegador y lo marca como aproximado (la pagina tiene
 * tope: el conteo seria mentira si se presentara como total).
 */
export async function obtenerResumen(): Promise<ResumenDTO> {
  try {
    return await pedir<ResumenDTO>('/resumen')
  } catch (error) {
    if (!esRutaAusente(error)) throw error
    return resumenDesde(await listarCfdis({ limit: 500 }))
  }
}

/**
 * Resuelve una excepcion.
 *   aceptar = true  -> se da por conciliado (MATCH manual).
 *   aceptar = false -> se respeta el veredicto del motor y sigue en excepcion.
 * Sin respaldo local: una mutacion no se adivina contra una ruta que no exista.
 */
export function resolverExcepcion(
  conciliacionId: number,
  aceptar: boolean,
  nota: string,
): Promise<ConciliacionDTO> {
  return pedir<ConciliacionDTO>(`/conciliaciones/${conciliacionId}/resolver`, {
    method: 'POST',
    body: JSON.stringify({ aceptar, nota }),
  })
}

export function listarAuditoria(limite = 200): Promise<EventoDTO[]> {
  return pedir<EventoDTO[]>(`/auditoria?limite=${limite}`)
}

/* ---------------------------------------------------------------- export */

const COLUMNAS_CSV = [
  'uuid',
  'rfc_emisor',
  'periodo',
  'fecha_emision',
  'estado',
  'resultado',
  'total_centavos',
  'reportado_centavos',
  'diferencia_centavos',
  'motivo',
] as const

function celda(valor: unknown): string {
  const texto = valor === null || valor === undefined ? '' : String(valor)
  return `"${texto.replace(/"/g, '""')}"`
}

function csvDesde(cfdis: CfdiDTO[]): string {
  const filas = [COLUMNAS_CSV.join(',')]
  for (const cfdi of cfdis) {
    const conciliacion = cfdi.conciliacion
    filas.push(
      [
        celda(cfdi.uuid),
        celda(cfdi.rfc_emisor),
        celda(cfdi.periodo),
        celda(cfdi.fecha_emision),
        celda(cfdi.estado),
        celda(conciliacion?.resultado ?? ''),
        // Los importes salen en CENTAVOS enteros: es lo que se audita.
        celda(cfdi.total_centavos),
        celda(reportadoCentavos(conciliacion, cfdi.total_centavos) ?? ''),
        celda(conciliacion?.diferencia_centavos ?? ''),
        celda(conciliacion?.motivo ?? ''),
      ].join(','),
    )
  }
  return filas.join('\r\n')
}

function entregarArchivo(contenido: Blob, nombre: string): void {
  const url = URL.createObjectURL(contenido)
  const enlace = document.createElement('a')
  enlace.href = url
  enlace.download = nombre
  document.body.appendChild(enlace)
  enlace.click()
  enlace.remove()
  URL.revokeObjectURL(url)
}

/**
 * Descarga el CSV de conciliacion.
 * No puede ser un `<a href>` normal: la ruta exige el encabezado Authorization.
 * Si el endpoint aun no existe, se arma el mismo CSV con lo que ya expone /cfdis.
 */
export async function descargarCsv(): Promise<void> {
  const nombre = `conciliacion-${new Date().toISOString().slice(0, 10)}.csv`
  try {
    const respuesta = await pedirCrudo('/exportar.csv', {
      headers: { Accept: 'text/csv' },
    })
    entregarArchivo(await respuesta.blob(), nombre)
  } catch (error) {
    if (!esRutaAusente(error)) throw error
    const cfdis = await listarCfdis({ limit: 500 })
    entregarArchivo(new Blob(['﻿', csvDesde(cfdis)], { type: 'text/csv;charset=utf-8' }), nombre)
  }
}

/* --------------------------------------------------------------- formatos */

const FORMATO_MONEDA = new Intl.NumberFormat('es-MX', {
  style: 'currency',
  currency: 'MXN',
  minimumFractionDigits: 2,
})

/** Centavos enteros -> "$1,234.56". La division entre 100 es SOLO de presentacion. */
export function formatearCentavos(centavos: number | null | undefined, moneda = 'MXN'): string {
  if (centavos === null || centavos === undefined || Number.isNaN(centavos)) return '—'
  if (moneda !== 'MXN') {
    return `${(centavos / 100).toLocaleString('es-MX', { minimumFractionDigits: 2 })} ${moneda}`
  }
  return FORMATO_MONEDA.format(centavos / 100)
}

/** Igual que el anterior pero con signo explicito, para diferencias. */
export function formatearDiferencia(centavos: number | null | undefined): string {
  if (centavos === null || centavos === undefined || Number.isNaN(centavos)) return '—'
  if (centavos === 0) return formatearCentavos(0)
  return `${centavos > 0 ? '+' : '−'}${formatearCentavos(Math.abs(centavos))}`
}

export function formatearFecha(valor: string | null | undefined): string {
  if (!valor) return '—'
  if (/^\d{4}-\d{2}-\d{2}$/.test(valor)) {
    const [anio, mes, dia] = valor.split('-')
    return `${dia}/${mes}/${anio}`
  }
  const fecha = new Date(valor)
  if (Number.isNaN(fecha.getTime())) return valor
  return fecha.toLocaleString('es-MX', { dateStyle: 'short', timeStyle: 'short' })
}

/** UUID completo -> forma corta para tabla; el original se conserva en el `title`. */
export function uuidCorto(uuid: string): string {
  if (uuid.length <= 13) return uuid
  return `${uuid.slice(0, 8)}…${uuid.slice(-4)}`
}
