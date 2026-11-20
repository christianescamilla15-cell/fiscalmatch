#!/usr/bin/env bash
# Prueba de humo de FiscalMatch: sale con 0 sólo si la API (y el portal, si se da) responden como deben.
#
#   herramientas/humo.sh <url-api> [url-portal]
#   herramientas/humo.sh http://localhost:8000 http://localhost:5173      # pila de docker compose
#
# La usan el job «imagen» del CI (contra la imagen recién construida y PostgreSQL) y el RUNBOOK.
# Cada llamada espera hasta HUMO_TIMEOUT segundos (90 por omisión) a que el servicio arranque.
set -uo pipefail

API="${1:?uso: herramientas/humo.sh <url-api> [url-portal]}"
FRONT="${2:-}"
TIMEOUT="${HUMO_TIMEOUT:-90}"
API="${API%/}"
FRONT="${FRONT%/}"
fallas=0

ok()    { echo "  ok    $1"; }
falla() { echo "  FALLA $1"; fallas=$((fallas + 1)); }

# Espera a que /health conteste (el contenedor puede tardar en crear el esquema).
esperar() {
  local url="$1" limite=$((SECONDS + TIMEOUT))
  until curl -fsS --max-time 5 "$url" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$limite" ]; then return 1; fi
    sleep 2
  done
}

echo "humo: API ${API}"
if esperar "${API}/health"; then
  salud="$(curl -fsS --max-time 10 "${API}/health")"
  if printf '%s' "$salud" | grep -q '"estado":"ok"'; then ok "/health estado=ok"; else falla "/health: ${salud:0:200}"; fi
else
  falla "/health no respondió en ${TIMEOUT} s"
fi

version="$(curl -fsS --max-time 10 "${API}/api/version" 2>&1)"
if printf '%s' "$version" | grep -q '"nombre":"FiscalMatch API"'; then
  ok "/api/version $(printf '%s' "$version" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')"
else
  falla "/api/version: ${version:0:200}"
fi

# Contesta la app (no un proxy): una ruta protegida sin token es 401.
codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${API}/api/auth/yo")"
if [ "$codigo" = "401" ]; then ok "/api/auth/yo sin token → 401"; else falla "/api/auth/yo sin token → ${codigo}"; fi

# Toca la base: el login consulta usuarios y deja el intento fallido en la bitácora.
codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H 'Content-Type: application/json' \
  -d '{"email":"humo@fiscalmatch.invalid","password":"no-existe"}' "${API}/api/auth/login")"
if [ "$codigo" = "401" ]; then ok "login inexistente → 401 (base de datos viva)"; else falla "login → ${codigo}"; fi

if [ -n "$FRONT" ]; then
  echo "humo: portal ${FRONT}"
  if esperar "${FRONT}/"; then
    if curl -fsS --max-time 10 "${FRONT}/" | grep -qi '<div id="raiz"'; then ok "portal sirve la SPA"; else falla "portal sin #raiz"; fi
  else
    falla "el portal no respondió en ${TIMEOUT} s"
  fi
  codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${FRONT}/excepciones")"
  if [ "$codigo" = "200" ]; then ok "ruta profunda → index.html"; else falla "ruta profunda → ${codigo}"; fi
  if curl -fsS --max-time 10 "${FRONT}/api/version" | grep -q '"nombre":"FiscalMatch API"'; then
    ok "el portal reenvía /api a la API"
  else
    falla "el proxy /api del portal no llega a la API"
  fi
fi

if [ "$fallas" -gt 0 ]; then
  echo "humo: ${fallas} falla(s)"
  exit 1
fi
echo "humo: todo responde"
