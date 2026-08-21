"""Punto de entrada de la API de FiscalMatch."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routers_auth, routers_cfdi, routers_operacion
from app.core.config import get_settings
from app.core.db import engine
from app.domain.models import Base

VERSION = "1.0.0"

#: El portal de agencias corre en Vite durante el desarrollo.
ORIGENES_PERMITIDOS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
    """Prepara el esquema al arrancar."""
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="FiscalMatch API",
    version=VERSION,
    description=(
        "Conciliación de CFDIs de comisiones contra los reportes de venta del periodo. "
        "Todos los importes se manejan y se devuelven en centavos enteros."
    ),
    lifespan=ciclo_de_vida,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGENES_PERMITIDOS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # El front necesita leer el conteo total y el nombre del archivo exportado.
    expose_headers=["X-Total-Count", "Content-Disposition"],
)

app.include_router(routers_auth.router)
app.include_router(routers_cfdi.router)
app.include_router(routers_operacion.router)


@app.get("/health", tags=["infra"], summary="Sonda de salud")
def health() -> dict[str, str]:
    """Responde sin tocar la base: sirve como sonda de vida del proceso."""
    return {"estado": "ok", "servicio": "fiscalmatch-api"}


@app.get("/api/version", tags=["infra"], summary="Versión desplegada")
def version() -> dict[str, Any]:
    """Versión y parámetros de conciliación vigentes (sin secretos)."""
    config = get_settings()
    return {
        "nombre": "FiscalMatch API",
        "version": VERSION,
        "rfc_receptor_esperado": config.rfc_receptor_esperado,
        "tolerancia_centavos": config.tolerancia_centavos,
        "tolerancia_porcentaje": config.tolerancia_porcentaje,
    }
