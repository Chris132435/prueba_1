from __future__ import annotations

import unicodedata

import pandas as pd

_APOSTROFOS = "'‘’`´"


def _normalizar(nombre: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().replace("_", " ").split())


def columna(df: pd.DataFrame, *candidatos: str) -> str:
    reales = {_normalizar(c): c for c in df.columns}
    for candidato in candidatos:
        real = reales.get(_normalizar(candidato))
        if real is not None:
            return real
    raise KeyError(f"No se encontró ninguna de las columnas {candidatos} en el archivo")


def quitar_apostrofo(serie: pd.Series) -> tuple[pd.Series, pd.Series]:
    texto = serie.astype("string").str.strip()
    tenia = texto.str.slice(0, 1).isin(list(_APOSTROFOS)).fillna(False).astype(bool)
    limpio = texto.where(~tenia, texto.str.lstrip(_APOSTROFOS).str.strip())
    return limpio, tenia


FECHA = ("Fecha",)
MARCA = ("Marca",)
IMPRESIONES = ("Impresiones",)
VALORIZACION_LOCAL = ("Valorización Local", "Valoración Local")
ADVERTISEMENT = ("Advertisement",)
