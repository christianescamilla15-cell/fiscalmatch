#!/usr/bin/env python3
"""Exporta el contrato OpenAPI de la API a docs/api/openapi.json (N07).

    python3 herramientas/exportar_openapi.py            # reescribe docs/api/openapi.json
    python3 herramientas/exportar_openapi.py --revisar  # sale con 1 si el versionado no está al día

El contrato sale de la propia app (FastAPI): no se escribe a mano. La prueba
backend/tests/test_openapi.py exige que lo versionado coincida con lo que la app publica.
Requiere las dependencias del backend instaladas; no abre conexión a la base.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "docs" / "api" / "openapi.json"


def contrato() -> str:
    sys.path.insert(0, str(RAIZ / "backend"))
    # Importar la app crea el motor de SQLAlchemy, que no se conecta hasta usarse.
    os.environ.setdefault("FM_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    from app.main import app

    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    actual = contrato()
    if "--revisar" in sys.argv[1:]:
        al_dia = DESTINO.is_file() and DESTINO.read_text(encoding="utf-8") == actual
        estado = "al día" if al_dia else "DESACTUALIZADO (corre sin --revisar)"
        print(f"{DESTINO.relative_to(RAIZ)}: {estado}")
        return 0 if al_dia else 1
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(actual, encoding="utf-8")
    print(f"{DESTINO.relative_to(RAIZ)}: {len(json.loads(actual)['paths'])} rutas exportadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
