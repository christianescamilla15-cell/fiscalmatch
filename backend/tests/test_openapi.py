"""Contrato OpenAPI (N07): versionado en docs/api/openapi.json, al día y completo.

Si una ruta cambia y nadie regenera el contrato, esta prueba falla con la instrucción para
regenerarlo: `python3 herramientas/exportar_openapi.py`.
"""
from __future__ import annotations

import json

import pytest

from app.main import app

from apoyo import RAIZ_REPO

CONTRATO = RAIZ_REPO / "docs" / "api" / "openapi.json"
PUBLICAS = {("post", "/api/auth/login"), ("get", "/api/version"), ("get", "/health")}


def _operaciones() -> list[tuple[str, str, dict]]:
    return [(metodo, ruta, op) for ruta, ops in app.openapi()["paths"].items() for metodo, op in ops.items()]


def test_el_contrato_versionado_esta_al_dia() -> None:
    publicado = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    assert CONTRATO.read_text(encoding="utf-8") == publicado, (
        "docs/api/openapi.json no coincide con la API: corre `python3 herramientas/exportar_openapi.py`"
    )


@pytest.mark.parametrize(("metodo", "ruta", "op"), _operaciones(),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_cada_operacion_esta_documentada(metodo: str, ruta: str, op: dict) -> None:
    assert op.get("summary", "").strip(), "falta summary"
    assert op.get("description", "").strip(), "falta description (docstring de la ruta)"
    exitos = {codigo: r for codigo, r in op["responses"].items() if codigo.startswith("2")}
    assert exitos, "sin respuesta de éxito documentada"
    for codigo, respuesta in op["responses"].items():
        assert respuesta.get("description", "").strip(), f"la respuesta {codigo} no tiene descripción"
        for tipo, contenido in (respuesta.get("content") or {}).items():
            assert contenido.get("schema"), f"la respuesta {codigo} ({tipo}) no declara esquema"
    if (metodo, ruta) not in PUBLICAS:
        assert "401" in op["responses"], "una ruta protegida debe documentar el 401"
