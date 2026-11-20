# FiscalMatch · atajos de operacion local.
# `make` sin argumentos lista lo disponible.

COMPOSE ?= docker compose
PY      ?= python3

.DEFAULT_GOAL := ayuda
.PHONY: ayuda up down seed test test-pg humo dev-api dev-web

ayuda:
	@echo "FiscalMatch — objetivos disponibles"
	@echo ""
	@echo "  make up       Levanta db + api + web (portal en http://localhost:5173)"
	@echo "  make down     Baja la pila y libera los puertos (el volumen de datos se conserva)"
	@echo "  make seed     Siembra agencias, usuarios demo, CFDIs y reportes de venta"
	@echo "  make test     Corre la suite de pytest del backend (SQLite temporal)"
	@echo "  make test-pg  Corre la suite contra el PostgreSQL de la pila (base aparte)"
	@echo "  make humo     Prueba de humo contra la pila levantada (API + portal)"
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
	@echo ">> Sembrando datos de demostracion con la imagen de la API (seed/ se monta de solo lectura)…"
	@echo "   (idempotente: puede correrse varias veces sin duplicar)"
	$(COMPOSE) run --rm -v "$(CURDIR)/seed:/seed:ro" -e PYTHONPATH=/app api python /seed/sembrar.py

test:
	@echo ">> Ejecutando pytest en backend/ (SQLite temporal; la prueba de concurrencia exige PostgreSQL)…"
	cd backend && $(PY) -m pytest -q

# Nunca toca la base de la demo: usa fiscalmatch_pruebas, que crea si no existe.
test-pg:
	@echo ">> Ejecutando pytest contra el PostgreSQL de la pila (requiere 'make up')…"
	$(COMPOSE) exec -T db sh -c "psql -U fiscalmatch -d fiscalmatch -tAc \"SELECT 1 FROM pg_database WHERE datname='fiscalmatch_pruebas'\" | grep -q 1 || createdb -U fiscalmatch fiscalmatch_pruebas"
	cd backend && FM_TEST_DATABASE_URL=postgresql+psycopg://fiscalmatch:fiscalmatch@localhost:5432/fiscalmatch_pruebas \
		FM_EXIGIR_POSTGRES=1 $(PY) -m pytest -q

humo:
	herramientas/humo.sh http://localhost:8000 http://localhost:5173

dev-api:
	@echo ">> API en modo desarrollo. Requiere Postgres arriba ('make up' o docker compose up -d db)."
	cd backend && $(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	@echo ">> Portal en modo desarrollo. El proxy de Vite manda /api a http://localhost:8000."
	cd frontend && npm install && npm run dev
