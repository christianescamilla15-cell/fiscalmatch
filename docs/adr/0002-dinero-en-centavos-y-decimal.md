# ADR 0002 · Dinero en centavos enteros, `Decimal` al leer y umbral truncado

- Estado: aceptada · Fecha: 2026-11-20

## Contexto
FiscalMatch compara importes fiscales: un centavo de diferencia entre el CFDI y la comisión devengada es un
hallazgo de auditoría. El código ya guardaba todo en centavos enteros y leía el XML con `Decimal`, pero dos
puntos quedaban fuera de esa regla:

- La tolerancia porcentual se multiplicaba en `float` y se truncaba: `21000 × 0.009` da
  `188.99999999999997` y el umbral quedaba en 188 centavos en vez de 189, convirtiendo un MATCH en PARCIAL.
- Las columnas eran `INTEGER`: en PostgreSQL un CFDI de más de $21,474,836.47 reventaba la carga con un 500
  (en SQLite no se notaba, por eso las pruebas pasaban).

## Decisión
- Todo importe es un **entero de centavos** (`*_centavos`) y en la base es **BIGINT**.
- Del XML se lee con `Decimal` y **ROUND_HALF_UP, cada atributo por separado** (SubTotal, impuestos y Total);
  TOTALES_COHERENTES compara esos enteros, sin sumar antes de redondear.
- El umbral es `max(tolerancia absoluta, comisión × porcentaje)`. El porcentaje se convierte a `Decimal` por
  su representación decimal (`Decimal(str(0.005))`) y el producto se **trunca** a centavos (ROUND_DOWN): el
  umbral nunca es más generoso que el porcentaje exacto. Ambos bordes son inclusivos.

## Alternativas
- `NUMERIC(18,2)` en la base y `Decimal` en todo el código: igual de exacto, pero obliga a cuidar la escala en
  cada operación; los enteros hacen imposible el medio centavo por construcción.
- Redondear el umbral (HALF_UP) en vez de truncarlo: con 2,899.5 centavos el umbral sería 2,900 y aceptaría
  una diferencia mayor que el porcentaje pactado.
- Redondeo bancario (HALF_EVEN): no es la convención de los importes del CFDI.

## Consecuencias
- Pruebas: `docs/reglas.md`, sección 4 (I1–I5) y casos G02–G07 y G16 del lote golden.
- Las bases creadas antes de este cambio tienen `INTEGER`: `create_all` no altera tablas existentes, así que
  hay que aplicar la migración manual del RUNBOOK o recrear el volumen.
