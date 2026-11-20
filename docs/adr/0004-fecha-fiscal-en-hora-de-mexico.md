# ADR 0004 · La quincena se corta en hora de México

- Estado: aceptada · Fecha: 2026-11-20

## Contexto
La comisión se liquida por quincena (días 1–15 y 16–fin de mes) y el periodo se deriva de la Fecha del CFDI.
El Anexo 20 pide esa Fecha en hora local del lugar de expedición y sin zona, pero el parser acepta también
ISO 8601 con zona (`...Z`, `-05:00`) y tomaba el día tal como venía escrito: `2026-08-16T05:30:00Z`, que en
México es el 15 a las 23:30, caía en la segunda quincena y se conciliaba contra el reporte equivocado.

## Decisión
- Fecha **sin zona**: se toma el día tal cual (hora local de expedición, como pide el Anexo 20).
- Fecha **con zona**: el instante se lleva a `America/Mexico_City` con `zoneinfo` antes de tomar el día.
- La base de zonas viaja con la app (`tzdata` en `requirements.txt`): no depende de que la imagen traiga
  `/usr/share/zoneinfo`. Desde el 30-oct-2022 esa zona no tiene horario de verano; no se fija a mano.

## Alternativas
- Rechazar la Fecha con zona por no cumplir el Anexo 20: más estricto, pero castiga a sistemas de facturación
  que serializan en UTC; se prefirió interpretarla correctamente.
- Suponer UTC para las fechas sin zona: contradice el Anexo 20 y movería de quincena a todo lo emitido de
  noche.

## Consecuencias
- Pruebas: `docs/reglas.md`, sección 5 (F1–F3) y caso G10 del lote golden (UTC que cruza de mes).
- La franja fronteriza con horario de verano no se modela: el calendario fiscal es el de la Ciudad de México.
- Un cambio futuro de la regla de horario lo trae una actualización de `tzdata` (Dependabot la propone).
