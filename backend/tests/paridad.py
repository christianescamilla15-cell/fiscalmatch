"""Comparador de paridad (N08b): multiconjunto de filas con llave estable, cifras control y hash.

* Cada fila se normaliza (UUID en mayúsculas, importes como `Decimal` con 2 decimales, sin
  separador de miles) y se resume en una huella sha256 de sus campos.
* La comparación es por MULTICONJUNTO (`Counter`), nunca posicional: el orden de las filas no
  importa y una fila repetida cuenta dos veces. Lo que no empata se agrupa por la llave (UUID)
  para decir QUÉ campo difiere, qué fila falta y cuál sobra.
* Las cifras control (conteo, sumas en `Decimal`, conteos por estado/resultado y hash del lote)
  se calculan aquí por separado del generador del golden: si ambos coinciden, el oráculo y la
  forma de medir están de acuerdo.
"""
from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

COLUMNAS: tuple[str, ...] = ("uuid", "rfc_emisor", "periodo", "total", "resultado", "diferencia", "estado")
Fila = tuple[str, ...]


def _importe(texto: str) -> str:
    texto = (texto or "").strip().replace(",", "")
    return "" if not texto else f"{Decimal(texto):.2f}"


def normalizar(crudo: dict[str, str]) -> Fila:
    return (
        crudo["uuid"].strip().upper(),
        crudo["rfc_emisor"].strip().upper(),
        crudo["periodo"].strip(),
        _importe(crudo["total"]),
        crudo["resultado"].strip(),
        _importe(crudo["diferencia"]),
        crudo["estado"].strip(),
    )


def leer_csv(texto: str) -> list[Fila]:
    """Filas normalizadas de un CSV (export de la API o golden); tolera el BOM de Excel."""
    lector = csv.DictReader(io.StringIO(texto.lstrip("﻿")))
    faltan = set(COLUMNAS) - set(lector.fieldnames or ())
    if faltan:
        raise ValueError(f"al CSV le faltan columnas: {sorted(faltan)}")
    return [normalizar(fila) for fila in lector]


def huella(fila: Fila) -> str:
    return hashlib.sha256("|".join(fila).encode("utf-8")).hexdigest()


def cifras_control(filas: list[Fila]) -> dict:
    total = sum((Decimal(f[3]) for f in filas), Decimal(0))
    diferencia = sum((Decimal(f[5]) for f in filas if f[5]), Decimal(0))
    por_estado = Counter(f[6] for f in filas)
    por_resultado = Counter(f[4] or "(sin conciliar)" for f in filas)
    return {
        "conteo": len(filas),
        "suma_total": f"{total:.2f}",
        "suma_diferencia": f"{diferencia:.2f}",
        "por_estado": dict(sorted(por_estado.items())),
        "por_resultado": dict(sorted(por_resultado.items())),
        "hash_lote": hashlib.sha256("\n".join(sorted(huella(f) for f in filas)).encode()).hexdigest(),
    }


@dataclass
class Diferencias:
    #: llave → campos que difieren (la fila existe en ambos lados)
    distintas: dict[str, list[str]] = field(default_factory=dict)
    #: llaves que el golden espera y la salida no trae (una entrada por copia faltante)
    faltantes: list[str] = field(default_factory=list)
    #: llaves que la salida trae y el golden no espera
    sobrantes: list[str] = field(default_factory=list)

    @property
    def vacia(self) -> bool:
        return not (self.distintas or self.faltantes or self.sobrantes)


def comparar(esperadas: list[Fila], obtenidas: list[Fila]) -> Diferencias:
    resto_esperado = Counter(esperadas) - Counter(obtenidas)
    resto_obtenido = Counter(obtenidas) - Counter(esperadas)
    por_llave_e: dict[str, list[Fila]] = {}
    por_llave_o: dict[str, list[Fila]] = {}
    for fila in resto_esperado.elements():
        por_llave_e.setdefault(fila[0], []).append(fila)
    for fila in resto_obtenido.elements():
        por_llave_o.setdefault(fila[0], []).append(fila)

    diferencias = Diferencias()
    for llave in sorted(set(por_llave_e) | set(por_llave_o)):
        esperadas_k, obtenidas_k = por_llave_e.get(llave, []), por_llave_o.get(llave, [])
        for esperada, obtenida in zip(esperadas_k, obtenidas_k, strict=False):
            diferencias.distintas[llave] = [c for c, a, b in zip(COLUMNAS, esperada, obtenida, strict=True)
                                            if a != b]
        diferencias.faltantes += [llave] * max(0, len(esperadas_k) - len(obtenidas_k))
        diferencias.sobrantes += [llave] * max(0, len(obtenidas_k) - len(esperadas_k))
    return diferencias
