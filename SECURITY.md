# Seguridad

## Reportar una vulnerabilidad

Escribe a **christianescamilla15@gmail.com** con el asunto «Seguridad: FiscalMatch». Respondo en 72 horas
hábiles y publico el arreglo con su aviso. Por favor no abras un issue público para vulnerabilidades.

## Qué revisa cada cambio

| Revisión | Herramienta | Bloquea |
|---|---|---|
| Análisis estático | CodeQL (Python y TypeScript) + Semgrep `p/default` | sí (Semgrep) |
| Dependencias, configuración e IaC | Trivy (`backend/requirements.txt` con versión exacta, `frontend/package-lock.json`, Dockerfiles) | sí, en CRITICAL/HIGH con arreglo publicado |
| Secretos en toda la historia | gitleaks (exige ver «N commits scanned») | sí |
| Datos de terceros | revisión de confidencialidad (`herramientas/confidencialidad.py`) | sí |
| Inventario de componentes | SBOM (syft, SPDX) | no: se publica con cada versión |
| Imágenes | el CI exige usuario no root en la API y el portal, y que la imagen de la API no traiga herramientas de pruebas | sí |

Cada revisión debe probar que analizó algo (archivos, paquetes o commits > 0); un reporte vacío cuenta como
fallo. Las excepciones viven en `.trivyignore`, cada una con fecha de vencimiento y motivo (hoy: ninguna).

## Controles del propio sistema

- Contraseñas con PBKDF2-HMAC-SHA256 (200 000 iteraciones, salt por usuario); JWT HS256 cuyo algoritmo fija
  el servidor (`alg: none` → 401); la identidad y el rol se releen de la base en cada petición.
- Una agencia sólo carga y ve lo suyo; la operación (excepciones, tablero, exportación, bitácora) es
  exclusiva de ANALISTA/ADMIN. Las pruebas por rol están en `docs/reglas.md`, sección 8.
- El XML se lee con `xml.etree` sobre el expat de la imagen (2.9 en `python:3.12-slim`), que no resuelve
  entidades externas y resiste «billion laughs» y la expansión cuadrática (expat ≥ 2.4.1). El portal limita
  a 20 MB las peticiones a `/api` (`client_max_body_size`); la API expuesta directamente no impone límite.
- Bitácora de sólo anexar: login (también el fallido), carga, resolución y exportación.
- Todos los datos del repositorio son sintéticos (RFC de prueba, sellos de relleno). Riesgos abiertos: ver
  `RUNBOOK.md`, «Riesgos conocidos» (secreto JWT por omisión, API sin límite de tamaño propio).
