# Arquitectura · FiscalMatch (Tally)

Modelo C4: contexto → contenedores → componentes, más la máquina de estados del CFDI y el despliegue local.
Los diagramas son texto (Mermaid), se versionan con el código y describen lo que HAY, no lo deseado. Las
decisiones que explican la forma están en [`docs/adr/`](../adr/); las reglas de negocio y sus pruebas, en
[`docs/reglas.md`](../reglas.md); el contrato de la API, en [`docs/api/openapi.json`](../api/openapi.json).

## 1. Contexto

```mermaid
C4Context
  title Contexto · FiscalMatch
  Person(agencia, "Agencia de viajes", "Rol AGENCIA: carga los CFDIs de comisión que emite y consulta sólo los suyos")
  Person(analista, "Analista de conciliación", "Rol ANALISTA (o ADMIN): resuelve excepciones, consulta el tablero, exporta y audita")
  System(fm, "FiscalMatch", "Valida CFDIs 4.0 de comisiones y los concilia contra el reporte de venta de la quincena")
  System_Ext(ventas, "Sistema de ventas", "Origen de la comisión devengada por agencia y quincena")
  System_Ext(pac, "SAT / PAC", "Timbra los CFDIs que emite la agencia")
  Rel(agencia, pac, "Timbra sus CFDIs")
  Rel(agencia, fm, "Carga XML y consulta lo suyo", "HTTPS")
  Rel(analista, fm, "Resuelve, exporta y audita", "HTTPS")
  Rel(ventas, fm, "Reporte de venta por quincena", "carga directa a la base (seed/sembrar.py); no hay endpoint")
```

FiscalMatch **no consulta al SAT**: la regla CERTIFICADO_VIGENTE sólo revisa la forma del número de
certificado y lo declara como simulación. Los RFC, sellos y UUID de los ejemplos son sintéticos.

## 2. Contenedores

```mermaid
C4Container
  title Contenedores · FiscalMatch
  Person(agencia, "Agencia")
  Person(analista, "Analista")
  System_Boundary(b, "FiscalMatch") {
    Container(web, "Portal", "React 19 + Vite · nginx sin privilegios :5173", "SPA de carga y panel del analista; reenvía /api a la API")
    Container(api, "API", "Python 3.12 · FastAPI · SQLAlchemy 2 · uvicorn :8000", "Autenticación, validación fiscal, motor de conciliación, operación y bitácora")
    ContainerDb(bd, "Base de datos", "PostgreSQL 16", "Agencias, usuarios, CFDIs con su XML, validaciones, reportes de venta, conciliaciones y bitácora")
    Container(seed, "Siembra de demostración", "Python · seed/sembrar.py", "Agencias, usuarios, reportes de venta y 24 CFDIs pasados por la validación y el motor reales")
  }
  Rel(agencia, web, "Usa", "HTTPS")
  Rel(analista, web, "Usa", "HTTPS")
  Rel(web, api, "Llama", "JSON / multipart sobre HTTP, Bearer JWT")
  Rel(api, bd, "Lee y escribe", "psycopg 3")
  Rel(seed, bd, "Siembra", "SQLAlchemy")
```

## 3. Componentes de la API

```mermaid
C4Component
  title Componentes · API de FiscalMatch
  Container(web, "Portal", "React")
  ContainerDb(bd, "Base de datos", "PostgreSQL 16")
  Container_Boundary(api, "API") {
    Component(auth, "routers_auth", "FastAPI", "POST /api/auth/login, GET /api/auth/yo; login fallido a bitácora")
    Component(cfdi, "routers_cfdi", "FastAPI", "Carga (parsear → frontera por agencia → UUID único → validar → archivar → conciliar), listado, detalle y XML")
    Component(oper, "routers_operacion", "FastAPI", "Cola de excepciones, resolución (409 si la transición es inválida), tablero, CSV y bitácora; sólo ANALISTA/ADMIN")
    Component(deps, "deps", "Python", "Identidad desde el JWT releída de la base, requiere_rol, frontera por agencia, registrar_auditoria")
    Component(seg, "seguridad", "hashlib · hmac", "PBKDF2-SHA256 para contraseñas y JWT HS256 firmado a mano (alg fijado por el servidor)")
    Component(val, "validacion", "xml.etree · Decimal · zoneinfo", "parsear_cfdi (fiel, centavos HALF_UP, fecha en hora de México) y validar (11 reglas)")
    Component(conc, "conciliacion", "Python puro + Session", "conciliar (umbral Decimal truncado), conciliar_cfdi, resolver_manual, periodo_de y ORIGENES_PERMITIDOS")
    Component(modelo, "models + db", "SQLAlchemy 2", "Agencia, Usuario, Cfdi, Validacion, ReporteVenta, Conciliacion, EventoAuditoria; importes BIGINT en centavos")
  }
  Rel(web, auth, "Inicia sesión")
  Rel(web, cfdi, "Carga y consulta")
  Rel(web, oper, "Opera")
  Rel(auth, seg, "Verifica contraseña y emite token")
  Rel(cfdi, deps, "Identidad, rol y bitácora")
  Rel(oper, deps, "Identidad, rol y bitácora")
  Rel(cfdi, val, "Parsea y valida")
  Rel(cfdi, conc, "Concilia lo validado")
  Rel(oper, conc, "Resuelve excepciones")
  Rel(conc, modelo, "Lee reportes y persiste el veredicto")
  Rel(modelo, bd, "SQL")
```

## 4. Máquina de estados del CFDI

```mermaid
stateDiagram-v2
  [*] --> VALIDADO: carga · pasan las 11 reglas
  [*] --> RECHAZADO: carga · falla alguna regla (se archiva con su desglose)
  VALIDADO --> CONCILIADO: conciliar · MATCH
  VALIDADO --> EN_EXCEPCION: conciliar · PARCIAL o NO_MATCH
  EN_EXCEPCION --> CONCILIADO: resolver(aceptar) · MANUAL
  EN_EXCEPCION --> EN_EXCEPCION: resolver(no aceptar) · sigue en la cola
  EN_EXCEPCION --> CONCILIADO: re-conciliar · llegó el reporte
  CONCILIADO --> EN_EXCEPCION: re-conciliar · el reporte cambió
  RECHAZADO --> [*]
```

Inválidas (prueba en `docs/reglas.md`, sección 6): conciliar un RECHAZADO o RECIBIDO, resolver algo que no
esté EN_EXCEPCION (la API responde 409 sin cambios ni bitácora). RECIBIDO existe en el enum pero la carga
dictamina en la misma transacción, así que nunca se persiste.

## 5. Despliegue

No hay despliegue en la nube (ni Vercel, ni Render, ni otro): la pila corre con `docker compose` y el CI
construye y arranca las mismas imágenes. Ver [`RUNBOOK.md`](../../RUNBOOK.md).

```mermaid
flowchart LR
  navegador["Navegador"] -- ":5173" --> web["web · nginx-unprivileged<br/>usuario 101"]
  web -- "/api → api:8000" --> api["api · python:3.12-slim<br/>uvicorn, usuario 10001"]
  api -- "db:5432" --> db[("db · postgres:16-alpine<br/>volumen datos-db")]
```
