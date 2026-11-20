# ADR 0003 · Máquina de estados del CFDI declarada por operación

- Estado: aceptada · Fecha: 2026-11-20

## Contexto
El estado del CFDI (`VALIDADO`, `RECHAZADO`, `CONCILIADO`, `EN_EXCEPCION`) lo movían `conciliar_cfdi()` y
`resolver_manual()` sin mirar de dónde partía. Al escribir las pruebas de transiciones inválidas aparecieron
dos defectos: un CFDI `RECHAZADO` con reporte que cuadraba podía terminar `CONCILIADO`, y resolver «no
aceptar» sobre un CFDI ya `CONCILIADO` lo regresaba a la cola con resultado MATCH, un estado incoherente.

## Decisión
`ORIGENES_PERMITIDOS` (en `app/services/conciliacion.py`) declara, **por operación**, de qué estados puede
partir: `conciliar` desde VALIDADO, CONCILIADO o EN_EXCEPCION (la re-conciliación es legítima cuando llega o
cambia el reporte); `resolver` sólo desde EN_EXCEPCION. `exigir_estado()` lanza `TransicionInvalida` antes de
tocar nada y el endpoint de resolución la traduce a **409** sin cambios ni evento de bitácora.

## Alternativas
- Una tabla `estado → estados destino`: no distingue QUIÉN mueve el estado. CONCILIADO → EN_EXCEPCION es
  válido para el motor (el reporte cambió) e inválido para el analista; por operación sí se expresa.
- Validar sólo en el endpoint: la siembra y cualquier otro llamador del servicio quedarían sin la guarda.

## Consecuencias
- Pruebas: `docs/reglas.md`, sección 6 (E1–E9), a nivel servicio y a nivel API.
- Una excepción aceptada ya no se reabre a mano; si hace falta, se re-concilia (el veredicto vuelve al motor).
