import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import './styles.css'

const contenedor = document.getElementById('raiz')
if (!contenedor) {
  throw new Error('No se encontro el contenedor #raiz en index.html')
}

createRoot(contenedor).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
