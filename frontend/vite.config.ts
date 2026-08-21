import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * En desarrollo el portal corre en :5173 y la API en :8000.
 * El proxy de "/api" evita CORS y hace que el codigo del cliente use
 * SIEMPRE rutas relativas, igual que en el contenedor de nginx.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
