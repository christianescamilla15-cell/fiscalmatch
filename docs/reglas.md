# Reglas de negocio → pruebas

Ésta es la compuerta real de calidad (más que el % de cobertura): cada regla del dominio tiene al menos una
prueba que falla si la regla se rompe. Las referencias usan `archivo::prueba` desde la raíz del repo;
`backend/tests/test_reglas_doc.py` verifica que cada prueba citada exista y que ninguna regla se quede sin
prueba (corre en el CI).

Las pruebas que pasan por la API usan la base de datos real: **PostgreSQL 16** en el CI (obligatorio,
`FM_EXIGIR_POSTGRES=1`) y SQLite temporal en local. Para agregar una regla: escribe la prueba primero,
agrégala a la tabla y corre `pytest`.

## 1. Validación fiscal del CFDI 4.0 · `backend/app/services/validacion.py`

Once reglas, evaluadas todas (no se corta al primer fallo); el CFDI pasa sólo si pasan todas.

| # | Regla | Pruebas |
|---|---|---|
| V1 | Se evalúan las 11 reglas y se reportan todas, con su detalle | `backend/tests/test_validacion.py::test_se_evaluan_las_once_reglas_sin_cortar_al_primer_fallo`, `backend/tests/test_validacion.py::test_cfdi_valido_pasa_todas_las_reglas` |
| V2 | ESTRUCTURA_CFDI40: `Version="4.0"` | `backend/tests/test_validacion.py::test_estructura_cfdi40_falla_con_version_anterior` |
| V3 | UUID_PRESENTE: timbre con UUID 8-4-4-4-12 hexadecimal | `backend/tests/test_validacion.py::test_uuid_presente_falla_sin_timbre`, `backend/tests/test_validacion.py::test_uuid_presente_falla_con_formato_incorrecto` |
| V4 | UUID_NO_DUPLICADO, sin distinguir mayúsculas | `backend/tests/test_validacion.py::test_uuid_no_duplicado_falla_si_ya_fue_cargado`, `backend/tests/test_validacion.py::test_uuid_duplicado_se_detecta_sin_importar_mayusculas` |
| V5 | RFC_EMISOR_VALIDO: persona moral (3 letras) o física (4) + AAMMDD + homoclave | `backend/tests/test_validacion.py::test_rfc_emisor_valido_falla_con_rfc_malformado` |
| V6 | RFC_RECEPTOR_ESPERADO: emitido a la empresa receptora configurada | `backend/tests/test_validacion.py::test_rfc_receptor_esperado_falla_si_la_factura_es_para_otro` |
| V7 | EMISOR_REGISTRADO: el emisor es una agencia activa del padrón | `backend/tests/test_validacion.py::test_emisor_registrado_falla_con_agencia_fuera_del_padron` |
| V8 | TOTALES_COHERENTES: SubTotal + impuestos trasladados = Total, al centavo | `backend/tests/test_validacion.py::test_totales_coherentes_falla_cuando_no_suma`, `backend/tests/test_bordes_importes.py::test_cada_importe_del_xml_se_redondea_por_separado` |
| V9 | MONEDA_MXN: las comisiones se liquidan sólo en pesos | `backend/tests/test_validacion.py::test_moneda_mxn_falla_con_divisa_extranjera` |
| V10 | IMPORTE_POSITIVO: Total > 0 | `backend/tests/test_validacion.py::test_importe_positivo_falla_con_total_en_cero`, `backend/tests/test_validacion.py::test_importe_positivo_falla_con_total_negativo` |
| V11 | SELLO_PRESENTE: Sello y Certificado no vacíos | `backend/tests/test_validacion.py::test_sello_presente_falla_sin_sello`, `backend/tests/test_validacion.py::test_sello_presente_falla_con_certificado_vacio` |
| V12 | CERTIFICADO_VIGENTE: SIMULACIÓN, sólo la forma (20 dígitos) y lo dice en el detalle | `backend/tests/test_validacion.py::test_certificado_vigente_falla_si_no_trae_20_digitos`, `backend/tests/test_validacion.py::test_certificado_vigente_declara_que_es_una_simulacion` |
| V13 | Una lista vacía de resultados NO aprueba (no se falla abierto) | `backend/tests/test_validacion.py::test_aprobado_exige_que_todas_pasen`, `backend/tests/test_validacion.py::test_aprobado_no_falla_abierto_con_lista_vacia` |
| V14 | El parseo es fiel: extrae sin corregir; Serie/Folio ausentes son NULL e impuestos ausentes valen 0 | `backend/tests/test_validacion.py::test_parseo_extrae_los_campos_del_comprobante`, `backend/tests/test_validacion.py::test_serie_y_folio_ausentes_quedan_en_none`, `backend/tests/test_validacion.py::test_impuestos_ausentes_valen_cero` |
| V15 | El parseo sólo falla con un documento ilegible: XML mal formado o vacío, raíz distinta, sin Emisor, importe o fecha ilegible | `backend/tests/test_validacion.py::test_parseo_falla_con_xml_mal_formado`, `backend/tests/test_validacion.py::test_parseo_falla_con_xml_vacio`, `backend/tests/test_validacion.py::test_parseo_falla_si_la_raiz_no_es_comprobante`, `backend/tests/test_validacion.py::test_parseo_falla_sin_nodo_emisor`, `backend/tests/test_validacion.py::test_parseo_falla_con_importe_no_numerico`, `backend/tests/test_validacion.py::test_parseo_falla_con_fecha_invalida` |

## 2. Carga y archivo · `backend/app/api/routers_cfdi.py`

| # | Regla | Pruebas |
|---|---|---|
| A1 | Sólo archivos `.xml` no vacíos e interpretables como CFDI; si no, 400 y no se archiva nada | `backend/tests/test_carga_y_consulta.py::test_carga_de_un_archivo_inservible_responde_400_y_no_archiva` |
| A2 | Un CFDI rechazado SÍ se archiva (RECHAZADO) con sus 11 reglas y su evento de bitácora; responde 422 con las reglas fallidas | `backend/tests/test_carga_y_consulta.py::test_un_cfdi_rechazado_se_archiva_con_su_desglose_y_su_bitacora` |
| A3 | Sólo un CFDI validado se concilia; el rechazado no tiene conciliación | `backend/tests/test_carga_y_consulta.py::test_un_cfdi_rechazado_se_archiva_con_su_desglose_y_su_bitacora`, `backend/tests/test_paridad_golden.py::test_paridad_del_lote_contra_el_golden` |
| A4 | El XML original se guarda y se devuelve byte por byte, como adjunto `application/xml` | `backend/tests/test_carga_y_consulta.py::test_el_xml_original_se_devuelve_byte_por_byte` |
| A5 | Se acepta XML en UTF-8 (con o sin BOM) y en Latin-1 | `backend/tests/test_carga_y_consulta.py::test_un_xml_en_latin1_se_acepta` |

## 3. Conciliación · `backend/app/services/conciliacion.py`

| # | Regla | Pruebas |
|---|---|---|
| C1 | Sólo son candidatos los reportes del MISMO periodo y RFC de la agencia | `backend/tests/test_conciliacion.py::test_periodo_distinto_no_matchea`, `backend/tests/test_conciliacion.py::test_rfc_distinto_no_matchea` |
| C2 | Sin reporte del periodo → NO_MATCH (diferencia 0, sin reporte) | `backend/tests/test_conciliacion.py::test_sin_candidatos_es_no_match` |
| C3 | \|total − comisión\| ≤ umbral → MATCH; exacto o dentro de tolerancia | `backend/tests/test_conciliacion.py::test_match_exacto`, `backend/tests/test_conciliacion.py::test_dentro_de_tolerancia_absoluta`, `backend/tests/test_conciliacion.py::test_dentro_de_tolerancia_porcentual_importe_grande` |
| C4 | \|diferencia\| > umbral → PARCIAL | `backend/tests/test_conciliacion.py::test_fuera_de_tolerancia_es_parcial` |
| C5 | Umbral = max(tolerancia absoluta, comisión × porcentaje), ambos bordes inclusivos | `backend/tests/test_bordes_importes.py::test_tolerancia_absoluta_es_inclusiva`, `backend/tests/test_bordes_importes.py::test_tolerancia_porcentual_es_inclusiva` |
| C6 | La diferencia conserva el signo (+ factura de más, − de menos) | `backend/tests/test_conciliacion.py::test_diferencia_negativa_conserva_el_signo` |
| C7 | Con varios candidatos (no debería pasar: hay UNIQUE) se toma el más cercano y se avisa en el motivo | `backend/tests/test_conciliacion.py::test_varios_candidatos_toma_el_mas_cercano` |
| C8 | Los importes del motivo se escriben como pesos con separador de miles y signo al frente | `backend/tests/test_conciliacion.py::test_pesos_formatea_con_separador_de_miles_y_signo` |

## 4. Importes y redondeo

Criterio documentado (ADR 0002): todo importe es un **entero de centavos**. Del XML se lee con `Decimal` y
**ROUND_HALF_UP, cada atributo por separado**; el umbral porcentual se calcula en `Decimal` (el porcentaje
por su representación decimal, «0.005») y se **trunca** a centavos (ROUND_DOWN): nunca es más generoso que el
porcentaje exacto. En la base son BIGINT.

| # | Regla | Pruebas |
|---|---|---|
| I1 | Importe decimal → centavos exactos con `Decimal`, nunca `float` | `backend/tests/test_validacion.py::test_importes_decimales_se_convierten_a_centavos_exactos`, `backend/tests/test_validacion.py::test_conversion_a_centavos_no_pasa_por_float`, `backend/tests/test_validacion.py::test_cfdi_con_decimales_conserva_los_centavos` |
| I2 | Medio centavo redondea hacia arriba (ROUND_HALF_UP), atributo por atributo | `backend/tests/test_validacion.py::test_conversion_a_centavos_redondea_medio_hacia_arriba`, `backend/tests/test_bordes_importes.py::test_cada_importe_del_xml_se_redondea_por_separado` |
| I3 | El umbral porcentual se calcula en `Decimal` (21000 × 0.9 % = 189, no 188) | `backend/tests/test_bordes_importes.py::test_umbral_porcentual_se_calcula_en_decimal_y_no_en_float`, `backend/tests/test_bordes_importes.py::test_porcentaje_como_decimal_o_float_da_el_mismo_umbral` |
| I4 | El umbral porcentual se trunca a centavos (2,899.5 → 2,899) | `backend/tests/test_bordes_importes.py::test_umbral_porcentual_se_trunca_a_centavos` |
| I5 | Un CFDI mayor a INT32 ($21,474,836.47) se guarda, concilia y suma en el tablero | `backend/tests/test_bordes_importes.py::test_cfdi_mayor_a_int32_se_guarda_y_concilia` |

## 5. Periodo quincenal y fechas

Criterio (ADR 0004): la Fecha sin zona es hora local de expedición (Anexo 20) y se toma tal cual; con zona, se
lleva a `America/Mexico_City` (sin horario de verano desde oct-2022) antes de tomar el día. Días 1–15 → P1.

| # | Regla | Pruebas |
|---|---|---|
| F1 | Periodo = AAAA-MM-P1 (días 1–15) o P2 (16 al fin de mes), derivado de la fecha, nunca capturado | `backend/tests/test_bordes_fechas.py::test_periodo_quincenal` |
| F2 | Último segundo del 15 es P1 y primer segundo del 16 es P2; fin de mes, de año y bisiesto | `backend/tests/test_bordes_fechas.py::test_fecha_de_emision_y_quincena_en_hora_de_mexico` |
| F3 | Una Fecha con zona se convierte a hora de México antes de cortar la quincena (puede cambiar de mes o año) | `backend/tests/test_bordes_fechas.py::test_fecha_de_emision_y_quincena_en_hora_de_mexico`, `backend/tests/test_bordes_fechas.py::test_cfdi_con_fecha_utc_concilia_contra_la_quincena_de_mexico` |

## 6. Estados del CFDI (máquina de estados) · `ORIGENES_PERMITIDOS`

```
carga ──▶ VALIDADO ──conciliar──▶ CONCILIADO (MATCH) | EN_EXCEPCION (PARCIAL, NO_MATCH)
     └──▶ RECHAZADO (terminal)
EN_EXCEPCION ──resolver(aceptar)──▶ CONCILIADO · ──resolver(no aceptar)──▶ EN_EXCEPCION
VALIDADO / CONCILIADO / EN_EXCEPCION ──conciliar──▶ re-conciliación del motor
```

| # | Regla | Pruebas |
|---|---|---|
| E1 | Cada operación declara de qué estados parte; lo no declarado es transición inválida | `backend/tests/test_maquina_estados.py::test_cada_operacion_declara_estados_de_origen_existentes`, `backend/tests/test_maquina_estados.py::test_transiciones_por_operacion` |
| E2 | VALIDADO → CONCILIADO si MATCH; → EN_EXCEPCION si PARCIAL o NO_MATCH | `backend/tests/test_maquina_estados.py::test_validado_se_dictamina_segun_el_veredicto` |
| E3 | INVÁLIDA: conciliar un CFDI RECHAZADO o RECIBIDO (no cambia nada) | `backend/tests/test_maquina_estados.py::test_conciliar_sin_validacion_aprobada_es_transicion_invalida` |
| E4 | EN_EXCEPCION → CONCILIADO al aceptar (MATCH, MANUAL, con responsable y bitácora) | `backend/tests/test_maquina_estados.py::test_resolver_aceptando_cierra_la_excepcion` |
| E5 | EN_EXCEPCION → EN_EXCEPCION al no aceptar: se respeta el veredicto del motor y sigue en la cola | `backend/tests/test_maquina_estados.py::test_resolver_sin_aceptar_deja_el_cfdi_en_la_cola` |
| E6 | INVÁLIDA: resolver un CFDI CONCILIADO (servicio: TransicionInvalida; API: 409 sin cambios ni bitácora) | `backend/tests/test_maquina_estados.py::test_resolver_un_cfdi_conciliado_es_transicion_invalida`, `backend/tests/test_maquina_estados.py::test_resolver_un_cfdi_conciliado_responde_409_sin_cambios_ni_auditoria` |
| E7 | INVÁLIDA: reabrir una excepción ya aceptada (409) | `backend/tests/test_maquina_estados.py::test_una_excepcion_ya_aceptada_no_se_reabre` |
| E8 | Re-conciliar reemplaza el veredicto (no duplica) y limpia la resolución manual | `backend/tests/test_maquina_estados.py::test_reconciliar_cuando_llega_el_reporte_limpia_la_resolucion_manual` |
| E9 | Resolver una conciliación que no existe → 404 | `backend/tests/test_maquina_estados.py::test_resolver_una_conciliacion_inexistente_responde_404` |

## 7. Reenvío idempotente (llave: UUID del timbre)

| # | Regla | Pruebas |
|---|---|---|
| R1 | El mismo CFDI cargado dos veces → 409; no cambia el CFDI, sus reglas, su conciliación ni la bitácora | `backend/tests/test_reenvio_idempotente.py::test_reenviar_el_mismo_cfdi_responde_409_y_no_cambia_nada` |
| R2 | El UUID se normaliza a mayúsculas: en minúsculas es el mismo CFDI | `backend/tests/test_reenvio_idempotente.py::test_reenviar_con_el_uuid_en_minusculas_es_el_mismo_cfdi` |
| R3 | Un rechazado no se «rescata» reenviándolo con el mismo UUID | `backend/tests/test_reenvio_idempotente.py::test_un_cfdi_rechazado_no_se_rescata_reenviandolo` |
| R4 | Cargas concurrentes del mismo UUID: una 201, las demás 409, una sola fila (PostgreSQL) | `backend/tests/test_reenvio_idempotente.py::test_reenvio_concurrente_del_mismo_uuid_deja_una_sola_fila` |
| R5 | Reenviar el lote completo no cambia el resultado del lote | `backend/tests/test_paridad_golden.py::test_reenviar_el_lote_completo_es_idempotente` |

## 8. Acceso por rol y frontera por agencia · `backend/app/api/deps.py`

| # | Regla | Pruebas |
|---|---|---|
| S1 | La agencia carga sólo CFDIs emitidos por su RFC (otro → 403, nada se archiva) | `backend/tests/test_autorizacion.py::test_agencia_carga_sus_propios_cfdis`, `backend/tests/test_autorizacion.py::test_agencia_no_carga_cfdis_emitidos_por_otra` |
| S2 | La agencia sólo lista lo suyo (también en el conteo X-Total-Count) | `backend/tests/test_autorizacion.py::test_agencia_solo_lista_lo_suyo` |
| S3 | La agencia NO ve el detalle ni el XML de otra agencia (403) | `backend/tests/test_autorizacion.py::test_agencia_no_ve_el_detalle_ni_el_xml_de_otra` |
| S4 | Rol AGENCIA sin agencia asignada no ve ni carga nada (jamás «todo») | `backend/tests/test_autorizacion.py::test_agencia_sin_agencia_asignada_no_ve_ni_carga_nada` |
| S5 | La agencia no entra a excepciones, resolución, tablero, exportación ni bitácora (403) | `backend/tests/test_autorizacion.py::test_agencia_no_entra_a_la_operacion` |
| S6 | El analista ve todas las agencias y su XML | `backend/tests/test_autorizacion.py::test_analista_ve_todas_las_agencias` |
| S7 | El analista carga a nombre de una agencia del padrón; un emisor fuera del padrón → 422 sin archivar | `backend/tests/test_autorizacion.py::test_analista_carga_a_nombre_de_una_agencia_del_padron`, `backend/tests/test_autorizacion.py::test_analista_no_carga_cfdis_de_un_emisor_fuera_del_padron` |
| S8 | Analista y administrador operan; el administrador no carga comprobantes | `backend/tests/test_autorizacion.py::test_analista_y_admin_operan`, `backend/tests/test_autorizacion.py::test_admin_no_carga_comprobantes` |
| S9 | Sin token, token alterado, vencido, con `alg: none` o de usuario inactivo → 401 | `backend/tests/test_autorizacion.py::test_sin_token_valido_responde_401`, `backend/tests/test_autorizacion.py::test_token_con_firma_alterada_responde_401`, `backend/tests/test_autorizacion.py::test_token_con_algoritmo_none_responde_401`, `backend/tests/test_autorizacion.py::test_token_expirado_responde_401`, `backend/tests/test_autorizacion.py::test_usuario_inactivo_responde_401_aunque_su_token_sea_valido` |
| S10 | El rol se relee de la base en cada petición (un cambio de rol surte efecto sin esperar al token) | `backend/tests/test_autorizacion.py::test_el_rol_se_relee_de_la_base_en_cada_peticion` |
| S11 | Login: correo sin distinguir mayúsculas ni espacios; el error no distingue la causa y queda en bitácora | `backend/tests/test_autorizacion.py::test_login_correcto_entrega_un_token_que_sirve`, `backend/tests/test_autorizacion.py::test_login_fallido_no_distingue_la_causa_y_queda_en_bitacora` |

## 9. Operación del analista: cola, tablero, exportación y bitácora · `backend/app/api/routers_operacion.py`

| # | Regla | Pruebas |
|---|---|---|
| O1 | La cola de excepciones son los CFDIs EN_EXCEPCION con su conciliación | `backend/tests/test_maquina_estados.py::test_resolver_sin_aceptar_deja_el_cfdi_en_la_cola`, `backend/tests/e2e/test_flujo_api_por_rol.py::test_quincena_completa_por_rol` |
| O2 | El listado filtra por estado, periodo y resultado; X-Total-Count no se pagina | `backend/tests/test_carga_y_consulta.py::test_el_listado_filtra_por_estado_periodo_y_resultado` |
| O3 | El tablero trae todas las llaves de estado y resultado aunque valgan cero | `backend/tests/test_carga_y_consulta.py::test_el_tablero_trae_todas_las_llaves_aunque_valgan_cero`, `backend/tests/e2e/test_flujo_api_por_rol.py::test_quincena_completa_por_rol` |
| O4 | La exportación CSV trae uuid, rfc, periodo, total, resultado, diferencia y estado de cada CFDI | `backend/tests/e2e/test_flujo_api_por_rol.py::test_quincena_completa_por_rol`, `backend/tests/test_paridad_golden.py::test_paridad_del_lote_contra_el_golden` |
| O5 | Bitácora: login, carga, resolución y exportación quedan con su actor; se lee del más reciente al más antiguo | `backend/tests/e2e/test_flujo_api_por_rol.py::test_quincena_completa_por_rol` |

## 10. Paridad contra el oráculo (N08b) y contrato (N07)

El lote golden `backend/tests/golden/lote_2026_09/` (16 CFDIs sintéticos, reporte de venta, resultado esperado
y cifras control) lo escribe `generar_lote.py` desde un **guion declarado a mano** que se contrasta con una
segunda lectura de estas reglas escrita aparte (no usa `app.services`). La salida que se compara es el CSV que
exporta la API después de que cada agencia sube sus XML. Comparación por **multiconjunto** con llave UUID
(nunca posicional) + cifras control: conteo, sumas en `Decimal`, conteos por estado/resultado y hash del lote.

| # | Regla | Pruebas |
|---|---|---|
| P1 | El golden corresponde a su guion byte a byte y sus cifras control cuadran con sus filas | `backend/tests/test_paridad_golden.py::test_el_golden_esta_al_dia_con_su_guion`, `backend/tests/test_paridad_golden.py::test_las_cifras_control_cuadran_con_las_filas_del_golden` |
| P2 | La salida del sistema para el lote es igual al golden (filas y cifras control) | `backend/tests/test_paridad_golden.py::test_paridad_del_lote_contra_el_golden` |
| P3 | El orden de carga no cambia el resultado; el comparador no es posicional y cuenta duplicados | `backend/tests/test_paridad_golden.py::test_el_orden_de_carga_no_cambia_el_resultado`, `backend/tests/test_paridad_golden.py::test_el_comparador_es_por_multiconjunto_y_no_posicional`, `backend/tests/test_paridad_golden.py::test_el_comparador_normaliza_separador_de_miles_bom_y_mayusculas` |
| P4 | CONTROL NEGATIVO: un golden alterado produce exactamente las diferencias sembradas | `backend/tests/test_paridad_golden.py::test_control_negativo_un_golden_alterado_se_detecta` |
| P5 | CONTROL NEGATIVO: un reporte de venta alterado se detecta en esa fila y en las cifras control | `backend/tests/test_paridad_golden.py::test_control_negativo_un_reporte_de_venta_alterado_se_detecta` |
| P6 | El contrato OpenAPI versionado está al día y cada operación está documentada | `backend/tests/test_openapi.py::test_el_contrato_versionado_esta_al_dia`, `backend/tests/test_openapi.py::test_cada_operacion_esta_documentada` |

## Lo que NO cubre esta tabla (y por qué)

- **Vigencia real del certificado (V12)**: exige consultar al SAT; el código lo simula y lo declara.
- **Dos CFDIs sin timbre**: la columna `uuid` es única y vacía para ambos, así que el segundo recibe 409 en vez
  del 422 de UUID_PRESENTE. Comportamiento conocido, sin prueba que lo fije a propósito (habría que guardar el
  UUID vacío como NULL; ver RUNBOOK, «Riesgos conocidos»).
- **Varios CFDIs de una agencia en la misma quincena**: cada uno se compara contra la comisión TOTAL del
  periodo. Es el comportamiento actual del motor; si el negocio factura por partes, la regla debe cambiar.
- **E2E de pantallas** (Playwright): pendiente; el portal se valida con `tsc` estricto y `npm run build`.
