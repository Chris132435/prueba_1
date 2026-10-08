"""Resolución tolerante de nombres de columna y limpieza básica de valores."""

from __future__ import annotations

import unicodedata

import pandas as pd

# Apóstrofos que Excel / algunos exports anteponen para forzar un valor como texto.
_APOSTROFOS = "'‘’`´"


def _normalizar(nombre: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().replace("_", " ").split())


def columna(df: pd.DataFrame, *candidatos: str) -> str:
    """Devuelve el nombre real de la primera columna de ``df`` que coincida con algún candidato."""
    reales = {_normalizar(c): c for c in df.columns}
    for candidato in candidatos:
        real = reales.get(_normalizar(candidato))
        if real is not None:
            return real
    raise KeyError(f"No se encontró ninguna de las columnas {candidatos} en el archivo")


def quitar_apostrofo(serie: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Quita espacios y el apóstrofo inicial (``'2026-10-01`` -> ``2026-10-01``).

    Devuelve la serie limpia (texto) y una máscara con las filas que traían apóstrofo, que
    suele indicar un error en la descarga o exportación del archivo.
    """
    texto = serie.astype("string").str.strip()
    tenia = texto.str.slice(0, 1).isin(list(_APOSTROFOS)).fillna(False).astype(bool)
    limpio = texto.where(~tenia, texto.str.lstrip(_APOSTROFOS).str.strip())
    return limpio, tenia


FECHA = ("Fecha",)
MARCA = ("Marca",)
IMPRESIONES = ("Impresiones",)
VALORIZACION_LOCAL = ("Valorización Local", "Valoración Local")
ADVERTISEMENT = ("Advertisement",)
