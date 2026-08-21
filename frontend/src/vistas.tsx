/**
 * Todas las vistas del portal en un solo archivo.
 *
 *   Login          · entrada comun
 *   VistaAgencia   · carga de XML y seguimiento de SUS propios CFDIs
 *   VistaAnalista  · resumen, cola de excepciones, export y bitacora
 *
 * El rol del token decide cual se monta (ver App.tsx).
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  type ReactNode,
} from 'react'

import {
  ErrorApi,
  cargarCfdi,
  descargarCsv,
  formatearCentavos,
  formatearDiferencia,
  formatearFecha,
  iniciarSesion,
  listarAuditoria,
  listarCfdis,
  listarExcepciones,
  obtenerCfdi,
  obtenerResumen,
  reportadoCentavos,
  resolverExcepcion,
  uuidCorto,
  type CfdiDTO,
  type CfdiDetalleDTO,
  type EstadoCfdi,
  type EventoDTO,
  type Excepcion,
  type ResultadoMatch,
  type ResumenDTO,
  type Sesion,
} from './api'

/* ------------------------------------------------------------ constantes */

/**
 * Pistas de acceso para la demo. Deben coincidir con lo que siembra el seed
 * del backend; si el seed cambia, cambia esta lista (no hay endpoint que las
 * exponga, y no debe haberlo).
 */
const CREDENCIALES_DEMO = [
  { etiqueta: 'Agencia', email: 'agencia@fiscalmatch.mx', password: 'demo1234' },
  { etiqueta: 'Analista', email: 'analista@fiscalmatch.mx', password: 'demo1234' },
] as const

type Tono = 'ok' | 'aviso' | 'error' | 'info' | 'neutro'

const TONO_ESTADO: Record<EstadoCfdi, Tono> = {
  RECIBIDO: 'neutro',
  VALIDADO: 'info',
  RECHAZADO: 'error',
  CONCILIADO: 'ok',
  EN_EXCEPCION: 'aviso',
}

const TONO_RESULTADO: Record<ResultadoMatch, Tono> = {
  MATCH: 'ok',
  PARCIAL: 'aviso',
  NO_MATCH: 'error',
}

/** `EN_EXCEPCION` -> `EN EXCEPCION`: los enums se leen mejor sin guion bajo. */
function etiqueta(valor: string): string {
  return valor.replace(/_/g, ' ')
}

function mensajeDe(error: unknown): string {
  if (error instanceof ErrorApi) {
    if (error.estado === 404 || error.estado === 405) {
      return `${error.message} (el endpoint todavia no esta publicado en la API)`
    }
    return error.message
  }
  if (error instanceof Error) return error.message
  return 'Ocurrio un error inesperado.'
}

/* ------------------------------------------------------- piezas comunes */

function Insignia({ texto, tono }: { texto: string; tono: Tono }) {
  return <span className={`insignia insignia--${tono}`}>{texto}</span>
}

function Aviso({ tono, children }: { tono: Tono; children: ReactNode }) {
  return (
    <p className={`aviso aviso--${tono}`} role={tono === 'error' ? 'alert' : 'status'}>
      {children}
    </p>
  )
}

function Cargando({ texto = 'Cargando…' }: { texto?: string }) {
  return (
    <p className="cargando" role="status">
      <span className="cargando__punto" aria-hidden="true" />
      {texto}
    </p>
  )
}

function Panel({
  titulo,
  descripcion,
  acciones,
  children,
}: {
  titulo: string
  descripcion?: string
  acciones?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="panel">
      <header className="panel__cabecera">
        <div>
          <h2 className="panel__titulo">{titulo}</h2>
          {descripcion ? <p className="panel__descripcion">{descripcion}</p> : null}
        </div>
        {acciones ? <div className="panel__acciones">{acciones}</div> : null}
      </header>
      {children}
    </section>
  )
}

function Vacio({ texto }: { texto: string }) {
  return <p className="vacio">{texto}</p>
}

function Modal({
  titulo,
  alCerrar,
  children,
}: {
  titulo: string
  alCerrar: () => void
  children: ReactNode
}) {
  useEffect(() => {
    const alTeclear = (evento: KeyboardEvent) => {
      if (evento.key === 'Escape') alCerrar()
    }
    window.addEventListener('keydown', alTeclear)
    return () => window.removeEventListener('keydown', alTeclear)
  }, [alCerrar])

  return (
    <div className="capa" onMouseDown={alCerrar}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={titulo}
        onMouseDown={(evento) => evento.stopPropagation()}
      >
        <header className="modal__cabecera">
          <h3>{titulo}</h3>
          <button
            type="button"
            className="boton boton--icono"
            onClick={alCerrar}
            aria-label="Cerrar"
          >
            ×
          </button>
        </header>
        {children}
      </div>
    </div>
  )
}

export function BarraSuperior({ sesion, alSalir }: { sesion: Sesion; alSalir: () => void }) {
  return (
    <header className="barra">
      <div className="barra__marca">
        <span className="marca__glifo" aria-hidden="true">
          ⛭
        </span>
        <span className="marca__nombre">
          Fiscal<strong>Match</strong>
        </span>
      </div>
      <div className="barra__sesion">
        <div className="barra__quien">
          <span className="barra__nombre">{sesion.nombre || sesion.email || 'Usuario'}</span>
          <span className="barra__meta">
            {etiqueta(sesion.rol)}
            {sesion.agencia ? ` · ${sesion.agencia}` : ''}
          </span>
        </div>
        <button type="button" className="boton boton--tenue" onClick={alSalir}>
          Salir
        </button>
      </div>
    </header>
  )
}

/* ------------------------------------------------------------------ login */

export function Login({ alEntrar }: { alEntrar: (sesion: Sesion) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)

  const enviar = async (evento: FormEvent) => {
    evento.preventDefault()
    if (enviando) return
    setError(null)
    setEnviando(true)
    try {
      alEntrar(await iniciarSesion(email.trim(), password))
    } catch (fallo) {
      setError(mensajeDe(fallo))
      setEnviando(false)
    }
  }

  return (
    <div className="acceso">
      <div className="acceso__tarjeta">
        <div className="acceso__marca">
          <span className="marca__glifo" aria-hidden="true">
            ⛭
          </span>
          <span className="marca__nombre">
            Fiscal<strong>Match</strong>
          </span>
        </div>
        <p className="acceso__lema">
          Conciliacion de CFDIs de comisiones contra los reportes de venta del periodo.
        </p>

        <form className="formulario" onSubmit={enviar}>
          <label className="campo">
            <span className="campo__etiqueta">Correo</span>
            <input
              className="campo__control"
              type="email"
              name="email"
              autoComplete="username"
              value={email}
              onChange={(evento) => setEmail(evento.target.value)}
              placeholder="usuario@dominio.mx"
              required
            />
          </label>

          <label className="campo">
            <span className="campo__etiqueta">Contrasena</span>
            <input
              className="campo__control"
              type="password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={(evento) => setPassword(evento.target.value)}
              required
            />
          </label>

          {error ? <Aviso tono="error">{error}</Aviso> : null}

          <button type="submit" className="boton boton--primario boton--ancho" disabled={enviando}>
            {enviando ? 'Entrando…' : 'Entrar'}
          </button>
        </form>

        <div className="pistas">
          <span className="pistas__titulo">Credenciales de demostracion</span>
          <div className="pistas__lista">
            {CREDENCIALES_DEMO.map((pista) => (
              <button
                key={pista.email}
                type="button"
                className="pista"
                onClick={() => {
                  setEmail(pista.email)
                  setPassword(pista.password)
                  setError(null)
                }}
              >
                <span className="pista__rol">{pista.etiqueta}</span>
                <span className="pista__email">{pista.email}</span>
              </button>
            ))}
          </div>
          <span className="pistas__nota">Da clic en una para llenar el formulario.</span>
        </div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------- vista agencia */

interface ResultadoCarga {
  archivo: string
  ok: boolean
  mensaje: string
}

function ZonaCarga({
  ocupada,
  alSoltar,
}: {
  ocupada: boolean
  alSoltar: (archivos: File[]) => void
}) {
  const [encima, setEncima] = useState(false)
  const refInput = useRef<HTMLInputElement>(null)

  const tomar = (lista: FileList | null) => {
    if (!lista || lista.length === 0) return
    alSoltar(Array.from(lista))
  }

  return (
    <div
      className={`zona${encima ? ' zona--activa' : ''}${ocupada ? ' zona--ocupada' : ''}`}
      onDragOver={(evento: DragEvent<HTMLDivElement>) => {
        evento.preventDefault()
        if (!ocupada) setEncima(true)
      }}
      onDragLeave={() => setEncima(false)}
      onDrop={(evento: DragEvent<HTMLDivElement>) => {
        evento.preventDefault()
        setEncima(false)
        if (!ocupada) tomar(evento.dataTransfer.files)
      }}
    >
      <span className="zona__icono" aria-hidden="true">
        ⇪
      </span>
      <p className="zona__texto">
        Arrastra aqui tus CFDIs en XML <span className="zona__o">o</span>
      </p>
      <button
        type="button"
        className="boton boton--primario"
        disabled={ocupada}
        onClick={() => refInput.current?.click()}
      >
        {ocupada ? 'Procesando…' : 'Selecciona archivos'}
      </button>
      <p className="zona__nota">
        Solo XML del CFDI timbrado. Puedes soltar varios a la vez: cada uno se valida y concilia
        por separado.
      </p>
      <input
        ref={refInput}
        className="zona__input"
        type="file"
        multiple
        /* La extension puede venir en mayusculas: se acepta y se filtra sin distinguir caja. */
        accept=".xml,.XML,text/xml,application/xml"
        onChange={(evento: ChangeEvent<HTMLInputElement>) => {
          tomar(evento.target.files)
          evento.target.value = ''
        }}
      />
    </div>
  )
}

function ReglasValidacion({ validaciones }: { validaciones: CfdiDetalleDTO['validaciones'] }) {
  if (validaciones.length === 0) {
    return <Vacio texto="Este CFDI aun no tiene reglas evaluadas." />
  }
  return (
    <ul className="reglas">
      {validaciones.map((validacion) => (
        <li key={validacion.id} className={`regla regla--${validacion.aprobada ? 'ok' : 'error'}`}>
          <span className="regla__marca" aria-hidden="true">
            {validacion.aprobada ? '✓' : '✕'}
          </span>
          <div className="regla__cuerpo">
            <code className="regla__nombre">{validacion.regla}</code>
            {validacion.detalle ? <p className="regla__detalle">{validacion.detalle}</p> : null}
          </div>
        </li>
      ))}
    </ul>
  )
}

function DetalleCfdi({
  detalle,
  cargando,
  error,
}: {
  detalle: CfdiDetalleDTO | null
  cargando: boolean
  error: string | null
}) {
  if (cargando) return <Cargando texto="Cargando detalle…" />
  if (error) return <Aviso tono="error">{error}</Aviso>
  if (!detalle) return <Vacio texto="Selecciona un CFDI de la tabla para ver su dictamen." />

  const conciliacion = detalle.conciliacion
  const reportado = reportadoCentavos(conciliacion, detalle.total_centavos)

  return (
    <div className="detalle">
      <dl className="datos">
        <div className="datos__par">
          <dt>UUID</dt>
          <dd className="mono">{detalle.uuid}</dd>
        </div>
        <div className="datos__par">
          <dt>Emisor</dt>
          <dd className="mono">{detalle.rfc_emisor}</dd>
        </div>
        <div className="datos__par">
          <dt>Receptor</dt>
          <dd className="mono">{detalle.rfc_receptor}</dd>
        </div>
        <div className="datos__par">
          <dt>Serie · folio</dt>
          <dd>{[detalle.serie, detalle.folio].filter(Boolean).join(' · ') || '—'}</dd>
        </div>
        <div className="datos__par">
          <dt>Periodo</dt>
          <dd>{detalle.periodo}</dd>
        </div>
        <div className="datos__par">
          <dt>Emision</dt>
          <dd>{formatearFecha(detalle.fecha_emision)}</dd>
        </div>
        <div className="datos__par">
          <dt>Subtotal</dt>
          <dd className="cifra">{formatearCentavos(detalle.subtotal_centavos, detalle.moneda)}</dd>
        </div>
        <div className="datos__par">
          <dt>Impuestos</dt>
          <dd className="cifra">{formatearCentavos(detalle.impuestos_centavos, detalle.moneda)}</dd>
        </div>
        <div className="datos__par">
          <dt>Total</dt>
          <dd className="cifra cifra--fuerte">
            {formatearCentavos(detalle.total_centavos, detalle.moneda)}
          </dd>
        </div>
      </dl>

      <h3 className="subtitulo">Reglas de validacion fiscal</h3>
      <ReglasValidacion validaciones={detalle.validaciones} />

      <h3 className="subtitulo">Conciliacion</h3>
      {conciliacion ? (
        <div className={`veredicto veredicto--${TONO_RESULTADO[conciliacion.resultado]}`}>
          <div className="veredicto__cabecera">
            <Insignia
              texto={etiqueta(conciliacion.resultado)}
              tono={TONO_RESULTADO[conciliacion.resultado]}
            />
            <span className="veredicto__metodo">{etiqueta(conciliacion.metodo)}</span>
          </div>
          <p className="veredicto__motivo">{conciliacion.motivo || 'Sin motivo registrado.'}</p>
          <dl className="veredicto__cifras">
            <div>
              <dt>CFDI</dt>
              <dd className="cifra">{formatearCentavos(detalle.total_centavos, detalle.moneda)}</dd>
            </div>
            <div>
              <dt>Reporte de venta</dt>
              <dd className="cifra">{formatearCentavos(reportado)}</dd>
            </div>
            <div>
              <dt>Diferencia</dt>
              <dd className="cifra">{formatearDiferencia(conciliacion.diferencia_centavos)}</dd>
            </div>
          </dl>
          {conciliacion.nota_resolucion ? (
            <p className="veredicto__nota">
              <strong>Nota del analista:</strong> {conciliacion.nota_resolucion}
            </p>
          ) : null}
        </div>
      ) : (
        <Vacio texto="El CFDI todavia no llego al motor de conciliacion." />
      )}
    </div>
  )
}

export function VistaAgencia({ sesion }: { sesion: Sesion }) {
  const [cfdis, setCfdis] = useState<CfdiDTO[]>([])
  const [cargandoLista, setCargandoLista] = useState(true)
  const [errorLista, setErrorLista] = useState<string | null>(null)

  const [seleccionado, setSeleccionado] = useState<number | null>(null)
  const [detalle, setDetalle] = useState<CfdiDetalleDTO | null>(null)
  const [cargandoDetalle, setCargandoDetalle] = useState(false)
  const [errorDetalle, setErrorDetalle] = useState<string | null>(null)

  const [subiendo, setSubiendo] = useState(false)
  const [resultados, setResultados] = useState<ResultadoCarga[]>([])

  const recargar = useCallback(async () => {
    setCargandoLista(true)
    setErrorLista(null)
    try {
      setCfdis(await listarCfdis())
    } catch (fallo) {
      setErrorLista(mensajeDe(fallo))
    } finally {
      setCargandoLista(false)
    }
  }, [])

  useEffect(() => {
    void recargar()
  }, [recargar])

  useEffect(() => {
    if (seleccionado === null) {
      setDetalle(null)
      return
    }
    let vigente = true
    setCargandoDetalle(true)
    setErrorDetalle(null)
    obtenerCfdi(seleccionado)
      .then((datos) => {
        if (vigente) setDetalle(datos)
      })
      .catch((fallo: unknown) => {
        if (vigente) setErrorDetalle(mensajeDe(fallo))
      })
      .finally(() => {
        if (vigente) setCargandoDetalle(false)
      })
    return () => {
      vigente = false
    }
  }, [seleccionado])

  const subir = async (archivos: File[]) => {
    // El filtro NO distingue mayusculas: ".XML" es tan valido como ".xml".
    const xml = archivos.filter((archivo) => archivo.name.toLowerCase().endsWith('.xml'))
    const descartados = archivos.length - xml.length

    const acumulados: ResultadoCarga[] = []
    if (descartados > 0) {
      acumulados.push({
        archivo: `${descartados} archivo(s)`,
        ok: false,
        mensaje: 'No es XML: se omitio sin procesar.',
      })
    }
    if (xml.length === 0) {
      setResultados(acumulados)
      return
    }

    setSubiendo(true)
    setResultados(acumulados)
    for (const archivo of xml) {
      try {
        const cargado = await cargarCfdi(archivo)
        acumulados.push({
          archivo: archivo.name,
          ok: cargado.estado !== 'RECHAZADO',
          mensaje: `${etiqueta(cargado.estado)} · ${formatearCentavos(
            cargado.total_centavos,
            cargado.moneda,
          )}`,
        })
        setSeleccionado(cargado.id)
      } catch (fallo) {
        acumulados.push({ archivo: archivo.name, ok: false, mensaje: mensajeDe(fallo) })
      }
      setResultados([...acumulados])
    }
    setSubiendo(false)
    await recargar()
  }

  const totales = useMemo(
    () => ({
      cantidad: cfdis.length,
      suma: cfdis.reduce((acumulado, cfdi) => acumulado + cfdi.total_centavos, 0),
    }),
    [cfdis],
  )

  return (
    <>
      <div className="encabezado">
        <div>
          <h1 className="encabezado__titulo">Mis CFDIs de comision</h1>
          <p className="encabezado__sub">
            {sesion.agencia ?? 'Agencia'}
            {sesion.rfc_agencia ? ` · ${sesion.rfc_agencia}` : ''} · {totales.cantidad}{' '}
            comprobante(s) por {formatearCentavos(totales.suma)}
          </p>
        </div>
      </div>

      <Panel
        titulo="Carga de comprobantes"
        descripcion="Cada XML se valida fiscalmente y se concilia contra el reporte de venta del periodo."
      >
        <ZonaCarga ocupada={subiendo} alSoltar={(archivos) => void subir(archivos)} />
        {resultados.length > 0 ? (
          <ul className="resultados">
            {resultados.map((resultado, indice) => (
              <li
                key={`${resultado.archivo}-${indice}`}
                className={`resultado resultado--${resultado.ok ? 'ok' : 'error'}`}
              >
                <span className="resultado__archivo mono">{resultado.archivo}</span>
                <span className="resultado__mensaje">{resultado.mensaje}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </Panel>

      <div className="rejilla-detalle">
        <Panel
          titulo="Comprobantes cargados"
          acciones={
            <button
              type="button"
              className="boton boton--tenue"
              onClick={() => void recargar()}
              disabled={cargandoLista}
            >
              Actualizar
            </button>
          }
        >
          {cargandoLista ? <Cargando /> : null}
          {errorLista ? <Aviso tono="error">{errorLista}</Aviso> : null}
          {!cargandoLista && !errorLista && cfdis.length === 0 ? (
            <Vacio texto="Todavia no has cargado ningun CFDI." />
          ) : null}
          {cfdis.length > 0 ? (
            <div className="tabla-envoltura">
              <table className="tabla">
                <thead>
                  <tr>
                    <th>UUID</th>
                    <th>Folio</th>
                    <th>Periodo</th>
                    <th className="derecha">Total</th>
                    <th>Estado</th>
                    <th>Resultado</th>
                  </tr>
                </thead>
                <tbody>
                  {cfdis.map((cfdi) => (
                    <tr
                      key={cfdi.id}
                      className={`tabla__fila${
                        seleccionado === cfdi.id ? ' tabla__fila--activa' : ''
                      }`}
                      onClick={() => setSeleccionado(cfdi.id)}
                      tabIndex={0}
                      onKeyDown={(evento) => {
                        if (evento.key === 'Enter' || evento.key === ' ') {
                          evento.preventDefault()
                          setSeleccionado(cfdi.id)
                        }
                      }}
                    >
                      <td className="mono" title={cfdi.uuid}>
                        {uuidCorto(cfdi.uuid)}
                      </td>
                      <td>{[cfdi.serie, cfdi.folio].filter(Boolean).join('-') || '—'}</td>
                      <td>{cfdi.periodo}</td>
                      <td className="derecha cifra">
                        {formatearCentavos(cfdi.total_centavos, cfdi.moneda)}
                      </td>
                      <td>
                        <Insignia texto={etiqueta(cfdi.estado)} tono={TONO_ESTADO[cfdi.estado]} />
                      </td>
                      <td>
                        {cfdi.resultado ? (
                          <Insignia
                            texto={etiqueta(cfdi.resultado)}
                            tono={TONO_RESULTADO[cfdi.resultado]}
                          />
                        ) : (
                          <span className="dim">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Panel>

        <Panel titulo="Dictamen del comprobante">
          <DetalleCfdi detalle={detalle} cargando={cargandoDetalle} error={errorDetalle} />
        </Panel>
      </div>
    </>
  )
}

/* --------------------------------------------------------- vista analista */

function Tarjeta({
  titulo,
  valor,
  tono = 'neutro',
  pie,
}: {
  titulo: string
  valor: string
  tono?: Tono
  pie?: string
}) {
  return (
    <article className={`tarjeta tarjeta--${tono}`}>
      <span className="tarjeta__titulo">{titulo}</span>
      <strong className="tarjeta__valor">{valor}</strong>
      {pie ? <span className="tarjeta__pie">{pie}</span> : null}
    </article>
  )
}

function ModalResolver({
  excepcion,
  trabajando,
  error,
  alCerrar,
  alResolver,
}: {
  excepcion: Excepcion
  trabajando: boolean
  error: string | null
  alCerrar: () => void
  alResolver: (aceptar: boolean, nota: string) => void
}) {
  const [nota, setNota] = useState('')
  const notaValida = nota.trim().length >= 5

  return (
    <Modal titulo="Resolver excepcion" alCerrar={alCerrar}>
      <div className="modal__cuerpo">
        <dl className="datos datos--compacto">
          <div className="datos__par">
            <dt>UUID</dt>
            <dd className="mono">{excepcion.cfdi.uuid}</dd>
          </div>
          <div className="datos__par">
            <dt>Emisor</dt>
            <dd className="mono">{excepcion.cfdi.rfc_emisor}</dd>
          </div>
          <div className="datos__par">
            <dt>Periodo</dt>
            <dd>{excepcion.cfdi.periodo}</dd>
          </div>
          <div className="datos__par">
            <dt>CFDI</dt>
            <dd className="cifra">{formatearCentavos(excepcion.cfdi.total_centavos)}</dd>
          </div>
          <div className="datos__par">
            <dt>Reporte de venta</dt>
            <dd className="cifra">{formatearCentavos(excepcion.reportado_centavos)}</dd>
          </div>
          <div className="datos__par">
            <dt>Diferencia</dt>
            <dd className="cifra">{formatearDiferencia(excepcion.diferencia_centavos)}</dd>
          </div>
        </dl>

        <p className="modal__motivo">{excepcion.motivo || 'Sin motivo registrado.'}</p>

        <label className="campo">
          <span className="campo__etiqueta">Nota de resolucion (queda en la bitacora)</span>
          <textarea
            className="campo__control campo__control--area"
            rows={4}
            value={nota}
            onChange={(evento) => setNota(evento.target.value)}
            placeholder="Explica el criterio: por que se da por conciliado, o por que se mantiene la excepcion."
          />
        </label>

        {!notaValida ? (
          <p className="pie-campo">La nota es obligatoria: minimo 5 caracteres.</p>
        ) : null}
        {error ? <Aviso tono="error">{error}</Aviso> : null}
      </div>

      <footer className="modal__pie">
        <button
          type="button"
          className="boton boton--tenue"
          onClick={alCerrar}
          disabled={trabajando}
        >
          Cancelar
        </button>
        <button
          type="button"
          className="boton boton--peligro"
          disabled={!notaValida || trabajando}
          onClick={() => alResolver(false, nota.trim())}
          title="Se respeta el veredicto del motor: el CFDI sigue en excepcion con tu nota."
        >
          Mantener en excepcion
        </button>
        <button
          type="button"
          className="boton boton--exito"
          disabled={!notaValida || trabajando}
          onClick={() => alResolver(true, nota.trim())}
          title="El CFDI queda CONCILIADO por criterio humano (MATCH manual)."
        >
          {trabajando ? 'Guardando…' : 'Aceptar'}
        </button>
      </footer>
    </Modal>
  )
}

function Bitacora() {
  const [eventos, setEventos] = useState<EventoDTO[]>([])
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const recargar = useCallback(async () => {
    setCargando(true)
    setError(null)
    try {
      setEventos(await listarAuditoria())
    } catch (fallo) {
      setError(mensajeDe(fallo))
    } finally {
      setCargando(false)
    }
  }, [])

  useEffect(() => {
    void recargar()
  }, [recargar])

  return (
    <Panel
      titulo="Bitacora de auditoria"
      descripcion="Quien hizo que, cuando y sobre que entidad. Solo lectura."
      acciones={
        <button
          type="button"
          className="boton boton--tenue"
          onClick={() => void recargar()}
          disabled={cargando}
        >
          Actualizar
        </button>
      }
    >
      {cargando ? <Cargando /> : null}
      {error ? <Aviso tono="error">{error}</Aviso> : null}
      {!cargando && !error && eventos.length === 0 ? (
        <Vacio texto="La bitacora esta vacia." />
      ) : null}
      {eventos.length > 0 ? (
        <div className="tabla-envoltura">
          <table className="tabla">
            <thead>
              <tr>
                <th>Momento</th>
                <th>Actor</th>
                <th>Accion</th>
                <th>Entidad</th>
                <th>Detalle</th>
              </tr>
            </thead>
            <tbody>
              {eventos.map((evento) => (
                <tr key={evento.id}>
                  <td className="nowrap">{formatearFecha(evento.ocurrido_en)}</td>
                  <td>{evento.actor}</td>
                  <td>
                    <code className="chip">{evento.accion}</code>
                  </td>
                  <td className="mono dim">
                    {evento.entidad}#{evento.entidad_id}
                  </td>
                  <td className="detalle-largo">{evento.detalle}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Panel>
  )
}

/** Conteo por clave con respaldo, para no pintar 0 donde en realidad no hay dato. */
function conteo(mapa: Record<string, number> | undefined, clave: string): number {
  return mapa?.[clave] ?? 0
}

export function VistaAnalista({ sesion }: { sesion: Sesion }) {
  const [pestana, setPestana] = useState<'excepciones' | 'bitacora'>('excepciones')

  const [resumen, setResumen] = useState<ResumenDTO | null>(null)
  const [excepciones, setExcepciones] = useState<Excepcion[]>([])
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [enResolucion, setEnResolucion] = useState<Excepcion | null>(null)
  const [trabajando, setTrabajando] = useState(false)
  const [errorModal, setErrorModal] = useState<string | null>(null)
  const [mensaje, setMensaje] = useState<string | null>(null)
  const [exportando, setExportando] = useState(false)

  const recargar = useCallback(async () => {
    setCargando(true)
    setError(null)
    try {
      const [datosResumen, cola] = await Promise.all([obtenerResumen(), listarExcepciones()])
      setResumen(datosResumen)
      setExcepciones(cola)
    } catch (fallo) {
      setError(mensajeDe(fallo))
    } finally {
      setCargando(false)
    }
  }, [])

  useEffect(() => {
    void recargar()
  }, [recargar])

  const resolver = async (aceptar: boolean, nota: string) => {
    if (!enResolucion) return
    setTrabajando(true)
    setErrorModal(null)
    try {
      await resolverExcepcion(enResolucion.conciliacion_id, aceptar, nota)
      setMensaje(
        `Excepcion ${uuidCorto(enResolucion.cfdi.uuid)} ${
          aceptar ? 'aceptada como conciliada' : 'mantenida en excepcion'
        } por ${sesion.nombre || sesion.email}.`,
      )
      setEnResolucion(null)
      await recargar()
    } catch (fallo) {
      setErrorModal(mensajeDe(fallo))
    } finally {
      setTrabajando(false)
    }
  }

  const exportar = async () => {
    setExportando(true)
    setError(null)
    try {
      await descargarCsv()
    } catch (fallo) {
      setError(mensajeDe(fallo))
    } finally {
      setExportando(false)
    }
  }

  const conciliados = conteo(resumen?.por_estado, 'CONCILIADO')
  const enExcepcion = conteo(resumen?.por_estado, 'EN_EXCEPCION')
  const total = resumen?.total_cfdis ?? 0
  const nota = resumen?.aproximado ? 'calculado en el navegador' : undefined

  return (
    <>
      <div className="encabezado">
        <div>
          <h1 className="encabezado__titulo">Panel de conciliacion</h1>
          <p className="encabezado__sub">
            Cola de excepciones y trazabilidad de todo lo que se resuelve a mano.
          </p>
        </div>
        <button
          type="button"
          className="boton boton--tenue"
          onClick={() => void exportar()}
          disabled={exportando}
        >
          {exportando ? 'Generando…' : '↓ Exportar CSV'}
        </button>
      </div>

      <div className="tarjetas">
        <Tarjeta
          titulo="CFDIs recibidos"
          valor={resumen ? String(total) : '—'}
          pie={nota ?? 'en todos los periodos'}
        />
        <Tarjeta
          titulo="Conciliados"
          valor={resumen ? String(conciliados) : '—'}
          tono="ok"
          pie={total > 0 ? `${Math.round((conciliados / total) * 100)}% del total` : undefined}
        />
        <Tarjeta
          titulo="En excepcion"
          valor={resumen ? String(enExcepcion) : '—'}
          tono="aviso"
          pie="requieren dictamen"
        />
        <Tarjeta
          titulo="Monto conciliado"
          valor={formatearCentavos(resumen?.total_conciliado_centavos)}
          tono="info"
          pie={
            resumen ? `de ${formatearCentavos(resumen.total_facturado_centavos)} facturados` : undefined
          }
        />
      </div>

      <nav className="pestanas" aria-label="Secciones del panel">
        <button
          type="button"
          className={`pestana${pestana === 'excepciones' ? ' pestana--activa' : ''}`}
          onClick={() => setPestana('excepciones')}
        >
          Excepciones
          {excepciones.length > 0 ? (
            <span className="pestana__contador">{excepciones.length}</span>
          ) : null}
        </button>
        <button
          type="button"
          className={`pestana${pestana === 'bitacora' ? ' pestana--activa' : ''}`}
          onClick={() => setPestana('bitacora')}
        >
          Bitacora
        </button>
      </nav>

      {mensaje ? <Aviso tono="ok">{mensaje}</Aviso> : null}

      {pestana === 'excepciones' ? (
        <Panel
          titulo="Cola de excepciones"
          descripcion="Comprobantes que el motor no pudo cerrar dentro de la tolerancia."
          acciones={
            <button
              type="button"
              className="boton boton--tenue"
              onClick={() => void recargar()}
              disabled={cargando}
            >
              Actualizar
            </button>
          }
        >
          {cargando ? <Cargando /> : null}
          {error ? <Aviso tono="error">{error}</Aviso> : null}
          {!cargando && !error && excepciones.length === 0 ? (
            <Vacio texto="Sin excepciones pendientes. Todo cuadra." />
          ) : null}
          {excepciones.length > 0 ? (
            <div className="tabla-envoltura">
              <table className="tabla">
                <thead>
                  <tr>
                    <th>UUID</th>
                    <th>Emisor</th>
                    <th>Periodo</th>
                    <th className="derecha">CFDI</th>
                    <th className="derecha">Reporte</th>
                    <th className="derecha">Diferencia</th>
                    <th>Resultado</th>
                    <th>Motivo</th>
                    <th aria-label="Acciones" />
                  </tr>
                </thead>
                <tbody>
                  {excepciones.map((excepcion) => (
                    <tr key={excepcion.conciliacion_id}>
                      <td className="mono" title={excepcion.cfdi.uuid}>
                        {uuidCorto(excepcion.cfdi.uuid)}
                      </td>
                      <td className="mono">{excepcion.cfdi.rfc_emisor}</td>
                      <td>{excepcion.cfdi.periodo}</td>
                      <td className="derecha cifra">
                        {formatearCentavos(excepcion.cfdi.total_centavos)}
                      </td>
                      <td className="derecha cifra">
                        {formatearCentavos(excepcion.reportado_centavos)}
                      </td>
                      <td className="derecha cifra cifra--fuerte">
                        {formatearDiferencia(excepcion.diferencia_centavos)}
                      </td>
                      <td>
                        <Insignia
                          texto={etiqueta(excepcion.resultado)}
                          tono={TONO_RESULTADO[excepcion.resultado]}
                        />
                      </td>
                      <td className="detalle-largo">{excepcion.motivo}</td>
                      <td className="derecha">
                        <button
                          type="button"
                          className="boton boton--primario boton--chico"
                          onClick={() => {
                            setErrorModal(null)
                            setMensaje(null)
                            setEnResolucion(excepcion)
                          }}
                        >
                          Resolver
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Panel>
      ) : (
        <Bitacora />
      )}

      {enResolucion ? (
        <ModalResolver
          excepcion={enResolucion}
          trabajando={trabajando}
          error={errorModal}
          alCerrar={() => {
            if (!trabajando) setEnResolucion(null)
          }}
          alResolver={(aceptar, textoNota) => void resolver(aceptar, textoNota)}
        />
      ) : null}
    </>
  )
}
