# Runbook · FiscalMatch

## Dónde corre

**No hay despliegue en la nube** a la fecha (2026-11-20): el repositorio no trae configuración de Vercel,
Render ni de otro proveedor, y no se publica ninguna imagen. La pila se levanta en local con
`docker compose` (`docker-compose.yml`) y el CI construye y ARRANCA esas mismas imágenes en cada cambio
(job `imagen` de `.github/workflows/ci.yml`).

| Pieza | Imagen | Puerto | Notas |
|---|---|---|---|
| `db` | `postgres:16-alpine` | 5432 | volumen `datos-db`; `pg_isready` como healthcheck |
| `api` | `backend/Dockerfile` (`python:3.12-slim`, usuario 10001) | 8000 | crea el esquema al arrancar (`create_all`); `/health` |
| `web` | `frontend/Dockerfile` (`nginx-unprivileged`, usuario 101) | 5173 | SPA; reenvía `/api` a `api:8000` |

Variables de la API (prefijo `FM_`): `FM_DATABASE_URL`, `FM_JWT_SECRET`, `FM_RFC_RECEPTOR_ESPERADO`,
`FM_TOLERANCIA_CENTAVOS`, `FM_TOLERANCIA_PORCENTAJE`. Los valores de `docker-compose.yml` son de desarrollo.

## Arrancar (local)

```bash
make up      # construye y levanta db → api → web
make seed    # agencias, usuarios demo (contraseña demo1234), reportes y 24 CFDIs por la validación real
make humo    # herramientas/humo.sh http://localhost:8000 http://localhost:5173 → debe salir con 0
```

La prueba de humo revisa `/health`, `/api/version`, que una ruta protegida sin token dé 401 (contesta la app),
que un login inexistente dé 401 (la base está viva y la bitácora escribe), que el portal sirva la SPA y sus
rutas profundas, y que el proxy `/api` del portal llegue a la API.

## Cambiar de versión

1. Todo cambio entra por PR a `main` con los checks en verde: `ci` (pruebas en PostgreSQL y SQLite, front,
   imágenes) y `seguridad` (confidencialidad, gitleaks, CodeQL, Semgrep, Trivy, SBOM).
2. En la máquina donde corre: `git pull && make up && make humo`.
3. **Si el cambio toca el esquema**: `create_all` crea tablas nuevas pero NO altera las existentes. Para pasar
   una base anterior al 20-nov-2026 a importes BIGINT y UUID normalizados (probado en PostgreSQL 16):

   ```bash
   docker compose exec -T db psql -U fiscalmatch -d fiscalmatch <<'SQL'
   BEGIN;
   ALTER TABLE cfdis ALTER COLUMN subtotal_centavos TYPE BIGINT,
                     ALTER COLUMN impuestos_centavos TYPE BIGINT,
                     ALTER COLUMN total_centavos TYPE BIGINT;
   ALTER TABLE reportes_venta ALTER COLUMN comision_centavos TYPE BIGINT;
   ALTER TABLE conciliaciones ALTER COLUMN diferencia_centavos TYPE BIGINT;
   UPDATE cfdis SET uuid = upper(uuid) WHERE uuid <> upper(uuid);
   COMMIT;
   SQL
   ```

   Si el `UPDATE` choca con la llave única, había dos CFDIs con el mismo UUID en distinta capitalización:
   es un duplicado real que hay que revisar a mano antes de repetir.

## Revertir (no ensayado en un ambiente compartido: no existe)

1. Respaldo antes de tocar nada:
   `docker compose exec -T db pg_dump -U fiscalmatch -d fiscalmatch > respaldo_$(date +%F).sql`
2. Volver a la versión anterior: `git checkout <commit-anterior> && make up && make humo`.
3. Ampliar una columna a BIGINT es compatible hacia atrás (el código anterior lee y escribe igual); no hace
   falta deshacer la migración. Para restaurar datos: `docker compose exec -T db psql -U fiscalmatch -d
   fiscalmatch < respaldo_AAAA-MM-DD.sql` sobre una base vacía.
4. Empezar de cero (BORRA los datos): `docker compose down -v && make up && make seed`.

## Diagnóstico

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `api` reinicia en bucle con `connection refused` | La base aún no acepta conexiones o `FM_DATABASE_URL` apunta mal | `docker compose logs db api`; el `depends_on: service_healthy` espera a `pg_isready` |
| Carga de un CFDI grande responde 500 `NumericValueOutOfRange` | Base creada antes del cambio a BIGINT | Aplicar la migración de «Cambiar de versión» |
| Un CFDI cae en la quincena «equivocada» | Fecha con zona (UTC) cerca de medianoche | Esperado: se convierte a hora de México (ADR 0004); revisar la Fecha del XML |
| `409` al cargar | El UUID ya existe (también en otra capitalización) | Esperado: reenvío idempotente; un CFDI corregido lleva UUID nuevo |
| `409` al resolver | El CFDI ya no está EN_EXCEPCION | Esperado (ADR 0003); refrescar la cola |
| `make seed` falla con `No module named app.seed` | Makefile anterior al 20-nov-2026 | Actualizar: ahora monta `seed/` en la imagen de la API |
| El portal carga pero todo da 502 | `web` no resuelve el host `api` (fuera de compose) | Levantar con `make up` o, a mano, `--add-host api:<ip de la API>` |

## Riesgos conocidos

- `FM_JWT_SECRET` tiene un valor de desarrollo por omisión y la app no se niega a arrancar con él: en
  cualquier ambiente compartido hay que definirlo (con el de ejemplo, cualquiera puede firmar tokens).
- CORS sólo permite `localhost:5173`; el portal de compose no lo necesita porque llama por el mismo origen.
- La API no limita el tamaño de la carga: sólo el proxy del portal (20 MB). No exponer el puerto 8000 fuera
  de la red de compose sin un límite delante.
- Dos CFDIs SIN timbre chocan en la llave única de `uuid` (vacío): el segundo recibe 409 en vez del 422 de
  UUID_PRESENTE (ver `docs/reglas.md`, «Lo que NO cubre»).
- No hay migraciones versionadas (Alembic): los cambios de esquema se documentan aquí a mano.
