import { useCallback, useEffect, useState } from 'react'

import {
  EVENTO_SESION_EXPIRADA,
  cerrarSesion,
  leerSesion,
  refrescarPerfil,
  type Sesion,
} from './api'
import { BarraSuperior, Login, VistaAgencia, VistaAnalista } from './vistas'

/**
 * Raiz del portal.
 *
 * Una sola decision de ruteo: si no hay sesion se muestra el login, y si la hay
 * manda el ROL del token. ADMIN ve lo mismo que ANALISTA (ve todo y resuelve).
 */
export default function App() {
  const [sesion, setSesion] = useState<Sesion | null>(() => leerSesion())

  // El cliente HTTP avisa por evento cuando la API devuelve 401.
  useEffect(() => {
    const alExpirar = () => setSesion(null)
    window.addEventListener(EVENTO_SESION_EXPIRADA, alExpirar)
    return () => window.removeEventListener(EVENTO_SESION_EXPIRADA, alExpirar)
  }, [])

  // Al abrir con un token ya guardado se revalida contra la API: confirma que
  // el token sigue vivo y repone nombre y agencia (el JWT no los lleva).
  useEffect(() => {
    if (!leerSesion()) return
    let vigente = true
    refrescarPerfil()
      .then((actualizada) => {
        if (vigente && actualizada) setSesion(actualizada)
      })
      .catch(() => {
        /* un 401 ya lo maneja el evento de sesion expirada; cualquier otro
           fallo no debe impedir trabajar con el perfil en cache */
      })
    return () => {
      vigente = false
    }
  }, [])

  const salir = useCallback(() => {
    cerrarSesion()
    setSesion(null)
  }, [])

  if (!sesion) {
    return <Login alEntrar={setSesion} />
  }

  const resuelveExcepciones = sesion.rol === 'ANALISTA' || sesion.rol === 'ADMIN'

  return (
    <div className="app">
      <BarraSuperior sesion={sesion} alSalir={salir} />
      <main className="contenido">
        {resuelveExcepciones ? <VistaAnalista sesion={sesion} /> : <VistaAgencia sesion={sesion} />}
      </main>
      <footer className="pie">
        FiscalMatch · conciliacion de CFDIs de comisiones · los importes se operan en centavos
        enteros
      </footer>
    </div>
  )
}
