from __future__ import annotations

import numpy as np
import pandas as pd

from . import columnas as col

METODOS_PROMEDIO = ("simple", "ponderado")


def _numero(serie: pd.Series) -> pd.Series:
    limpio, _ = col.quitar_apostrofo(serie)
    return pd.to_numeric(limpio, errors="coerce")


def detalle_cpm(df: pd.DataFrame, metodo: str = "simple") -> pd.DataFrame:
    if metodo not in METODOS_PROMEDIO:
        raise ValueError(f"metodo debe ser uno de {METODOS_PROMEDIO}, no {metodo!r}")

    marca = df[col.columna(df, *col.MARCA)]
    impresiones = _numero(df[col.columna(df, *col.IMPRESIONES)])
    valor = _numero(df[col.columna(df, *col.VALORIZACION_LOCAL)])

    impresiones_validas = impresiones.where(impresiones > 0)
    cpm = (valor / impresiones_validas) * 1000

    mes = df["fecha_cast"].str.slice(0, 7)
    claves = [mes, marca]

    if metodo == "simple":
        promedio = cpm.groupby(claves, dropna=False).transform("mean")
    else:
        valido = cpm.notna()
        suma_valor = valor.where(valido).groupby(claves, dropna=False).transform("sum")
        suma_impr = impresiones_validas.where(valido).groupby(claves, dropna=False).transform("sum")
        promedio = (suma_valor / suma_impr.replace(0, np.nan)) * 1000

    return pd.DataFrame(
        {
            "mes": mes,
            "marca": marca,
            "impresiones": impresiones,
            "cpm": cpm,
            "cpm_promedio": promedio,
            "cpm_delta": promedio - cpm,
        },
        index=df.index,
    )


def calcular_cpm_delta(df: pd.DataFrame, metodo: str = "simple") -> pd.DataFrame:
    salida = df.copy()
    salida["cpm_delta"] = detalle_cpm(df, metodo)["cpm_delta"]
    return salida


def resumen_cpm_mes_marca(detalle: pd.DataFrame) -> pd.DataFrame:
    return (
        detalle.groupby(["mes", "marca"], dropna=False)
        .agg(
            filas=("cpm", "size"),
            filas_sin_impresiones=("cpm", lambda s: int(s.isna().sum())),
            impresiones=("impresiones", "sum"),
            cpm_promedio=("cpm_promedio", "first"),
            cpm_min=("cpm", "min"),
            cpm_max=("cpm", "max"),
            cpm_delta_min=("cpm_delta", "min"),
            cpm_delta_max=("cpm_delta", "max"),
        )
        .reset_index()
    )
