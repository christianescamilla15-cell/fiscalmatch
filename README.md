# FiscalMatch

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
Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL · React 19 + Vite + TypeScript · Docker Compose
