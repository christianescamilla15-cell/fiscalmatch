# FiscalMatch · atajos de operacion local.
# `make` sin argumentos lista lo disponible.

COMPOSE ?= docker compose
PY      ?= python3

.DEFAULT_GOAL := ayuda
.PHONY: ayuda up down seed test dev-api dev-web

ayuda:
	@echo "FiscalMatch — objetivos disponibles"
	@echo ""
	@echo "  make up       Levanta db + api + web (portal en http://localhost:5173)"
	@echo "  make down     Baja la pila y libera los puertos (el volumen de datos se conserva)"
	@echo "  make seed     Siembra agencias, usuarios demo, CFDIs y reportes de venta"
	@echo "  make test     Corre la suite de pytest del backend"
	@echo "  make dev-api  API en caliente con recarga automatica en :8000"
	@echo "  make dev-web  Portal de Vite en :5173 con proxy hacia la API"

up:
	@echo ">> Construyendo y levantando la pila (db -> api -> web)…"
	$(COMPOSE) up -d --build
	@echo ">> Portal: http://localhost:5173   API: http://localhost:8000/docs"

down:
	@echo ">> Bajando la pila. El volumen 'datos-db' NO se borra:"
	@echo "   para empezar de cero usa 'docker compose down -v'."
	$(COMPOSE) down

seed:
	@echo ">> Sembrando datos de demostracion dentro del contenedor de la API…"
	@echo "   (idempotente: puede correrse varias veces sin duplicar)"
	$(COMPOSE) run --rm api python -m app.seed

test:
	@echo ">> Ejecutando pytest en backend/ …"
	cd backend && $(PY) -m pytest -q

dev-api:
	@echo ">> API en modo desarrollo. Requiere Postgres arriba ('make up' o docker compose up -d db)."
	cd backend && $(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	@echo ">> Portal en modo desarrollo. El proxy de Vite manda /api a http://localhost:8000."
	cd frontend && npm install && npm run dev
