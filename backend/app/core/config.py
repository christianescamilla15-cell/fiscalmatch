"""Configuración central. Todo el dinero se maneja en CENTAVOS (enteros)."""
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "FiscalMatch"
    database_url: str = "postgresql+psycopg://fiscalmatch:fiscalmatch@localhost:5432/fiscalmatch"
    jwt_secret: str = "dev-secret-cambiar-en-produccion"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # RFC de la empresa receptora de las facturas de comisión
    rfc_receptor_esperado: str = "FMA240101AB1"

    # Tolerancia de conciliación
    tolerancia_centavos: int = 100          # ±1.00 MXN absoluto
    tolerancia_porcentaje: float = 0.005    # ±0.5 %

    model_config = {"env_file": ".env", "env_prefix": "FM_", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
