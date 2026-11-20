# FiscalMatch

[![ci](https://github.com/christianescamilla15-cell/fiscalmatch/actions/workflows/ci.yml/badge.svg)](https://github.com/christianescamilla15-cell/fiscalmatch/actions/workflows/ci.yml)
[![seguridad](https://github.com/christianescamilla15-cell/fiscalmatch/actions/workflows/seguridad.yml/badge.svg)](https://github.com/christianescamilla15-cell/fiscalmatch/actions/workflows/seguridad.yml)

Plataforma de **conciliación de CFDIs de comisiones** contra reportes de venta.

Las agencias cargan sus CFDIs por el portal (rol limitado, sólo ven lo suyo), el sistema los
valida fiscalmente y los concilia automáticamente contra los reportes de venta del periodo.
Lo que no cuadra cae en una cola de excepciones que un analista resuelve, con trazabilidad
completa de auditoría.

**Proyecto original**, construido de cero. No contiene código, datos ni referencias de ningún
cliente: los CFDIs de ejemplo son generados y los RFC son de prueba.

## Flujo E2E

```
Agencia ──carga XML──▶ Validación fiscal ──▶ Motor de conciliación ──▶ Resultados
 (rol limitado)        estructura CFDI 4.0    match vs reportes de venta   panel analista
                       RFC · UUID · totales   MATCH / PARCIAL / NO MATCH   export + audit trail
```

## Stack
Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL · React 19 + Vite · TypeScript · Docker Compose

## Arquitectura

C4 (contexto, contenedores y componentes), máquina de estados del CFDI y despliegue local en
[`docs/arquitectura/README.md`](docs/arquitectura/README.md); decisiones en [`docs/adr/`](docs/adr/)
(dinero en centavos y `Decimal`, estados por operación, quincena en hora de México). El contrato de la API
está versionado en [`docs/api/openapi.json`](docs/api/openapi.json).

## Cómo ejecutar

Con Docker (PostgreSQL + API + portal):

```bash
make up      # http://localhost:5173 (portal) · http://localhost:8000/docs (API)
make seed    # datos de demostración; la contraseña de todos los usuarios demo es demo1234
make humo    # prueba de humo: debe terminar en «todo responde»
```

No hay despliegue en la nube: el arranque, la migración manual de esquema y la reversa están en
[`RUNBOOK.md`](RUNBOOK.md).

## Pruebas

```bash
cd backend && pip install -r requirements-dev.txt
pytest -q                                   # SQLite temporal (la prueba de concurrencia exige PostgreSQL)
FM_TEST_DATABASE_URL=postgresql+psycopg://usuario:clave@localhost:5432/fiscalmatch_pruebas \
  FM_EXIGIR_POSTGRES=1 pytest -q            # contra PostgreSQL real, como el CI (o `make test-pg`)
cd ../frontend && npm ci && npm run build   # portal: tipos estrictos y build
```

La compuerta es **una prueba por regla de negocio**: [`docs/reglas.md`](docs/reglas.md) (72 reglas, incluidas
las transiciones inválidas de la máquina de estados y el acceso por rol). La paridad se mide contra un lote
golden con oráculo independiente, cifras control y controles negativos (`backend/tests/golden/`).

## Seguridad

Política y canal privado de reporte en [`SECURITY.md`](SECURITY.md). Cada cambio pasa por CodeQL, Semgrep,
Trivy (dependencias e imágenes), gitleaks, SBOM y la revisión de confidencialidad (`seguridad.yml`).

## Licencia

[MIT](LICENSE).
