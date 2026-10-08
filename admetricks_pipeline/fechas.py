"""Paso 1: normalización de la columna Fecha a ``fecha_cast`` (yyyy-mm-dd)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from .columnas import quitar_apostrofo

log = logging.getLogger(__name__)

# yyyy-mm-dd, yyyy/mm/dd, yyyy-mm-dd hh:mm:ss.f, yyyy-mm-ddThh:mm:ssZ ...
_RE_ISO = r"^(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})(?:$|[ T])"
# dd/mm/yyyy, dd-mm-yyyy (formato habitual en Latinoamérica), con o sin hora
_RE_DIA_PRIMERO = r"^(?P<d>\d{1,2})[-/.](?P<m>\d{1,2})[-/.](?P<y>\d{4})(?:$|[ T])"
# Número de serie de Excel (días desde 1899-12-30), p. ej. 46296 o 46296.0
_RE_SERIAL_EXCEL = r"^\d{5}(?:\.\d+)?$"
# Solo hora, sin fecha: lo que deja Excel al guardar un datetime con formato mm:ss.0
_RE_SOLO_HORA = r"^\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?$"

_RE_PERIODO_ARCHIVO = re.compile(r"(?<!\d)(\d{4})-(\d{2})(?:-(\d{2}))?(?!\d)")


def periodo_desde_nombre(ruta: str | Path) -> pd.Timestamp | None:
    """Obtiene la fecha de referencia del nombre del archivo.

    ``2026-10-Admetricks-Mascotas.csv`` -> 2026-10-01. Devuelve None si no hay periodo.
    """
    m = _RE_PERIODO_ARCHIVO.search(Path(ruta).name)
    if not m:
        return None
    anio, mes, dia = m.group(1), m.group(2), m.group(3) or "01"
    fecha = pd.to_datetime(f"{anio}-{mes}-{dia}", format="%Y-%m-%d", errors="coerce")
    return None if pd.isna(fecha) else fecha


def _desde_partes(partes: pd.DataFrame) -> pd.Series:
    """Construye fechas a partir de columnas y/m/d (texto). Fechas inválidas -> NaT."""
    numeros = partes[["y", "m", "d"]].astype("float")
    numeros.columns = ["year", "month", "day"]
    return pd.to_datetime(numeros, errors="coerce")


def convertir_fechas(
    serie: pd.Series, fecha_respaldo: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Convierte una serie de fechas heterogéneas al formato yyyy-mm-dd.

    Antes de convertir se quita el apóstrofo inicial (``'2026-10-01 00:00:00.0``), que
    aparece cuando el valor se exportó forzado como texto; esas filas se cuentan y se
    reporta un warning porque suelen indicar un error en la descarga del archivo.

    Se toma la fecha calendario tal como está escrita (sin convertir zonas horarias).
    Los valores sin fecha recuperable (vacíos o solo hora, como ``00:00.0``) se
    rellenan con ``fecha_respaldo``.

    Devuelve un DataFrame alineado con ``serie`` con las columnas:
    ``fecha_cast`` (texto yyyy-mm-dd), ``fecha_inferida`` (se usó el respaldo),
    ``apostrofo`` (traía apóstrofo inicial) y ``no_reconocida`` (formato desconocido).
    """
    texto, apostrofo = quitar_apostrofo(serie)
    if apostrofo.any():
        log.warning(
            "%d de %d valores de Fecha traían un apóstrofo inicial (') — posible error en la "
            "descarga del archivo; se quitó antes de convertir (ej.: %s)",
            int(apostrofo.sum()),
            len(serie),
            serie[apostrofo].head(3).tolist(),
        )

    fechas = pd.Series(pd.NaT, index=serie.index, dtype="datetime64[ns]")

    for patron in (_RE_ISO, _RE_DIA_PRIMERO):
        pendientes = fechas.isna()
        partes = texto[pendientes].str.extract(patron)
        encontrados = partes["y"].notna()
        if encontrados.any():
            fechas.loc[partes.index[encontrados]] = _desde_partes(partes[encontrados])

    pendientes = fechas.isna() & texto.str.fullmatch(_RE_SERIAL_EXCEL).fillna(False)
    if pendientes.any():
        fechas.loc[pendientes] = pd.to_datetime(
            texto[pendientes].astype(float), unit="D", origin="1899-12-30"
        ).dt.normalize()

    sin_fecha = fechas.isna()
    solo_hora = texto.str.fullmatch(_RE_SOLO_HORA).fillna(False)
    no_reconocidos = (sin_fecha & texto.notna() & (texto != "") & ~solo_hora).astype(bool)
    if no_reconocidos.any():
        ejemplos = texto[no_reconocidos].unique()[:5].tolist()
        log.warning(
            "%d valores de Fecha con formato no reconocido (ej.: %s)",
            int(no_reconocidos.sum()),
            ejemplos,
        )

    inferida = pd.Series(False, index=serie.index)
    if sin_fecha.any():
        if fecha_respaldo is not None:
            fechas.loc[sin_fecha] = fecha_respaldo
            inferida = sin_fecha.copy()
            log.warning(
                "%d de %d filas no traen fecha en la columna Fecha (p. ej. '00:00.0', "
                "típico de un CSV guardado desde Excel); se usa la fecha de respaldo %s",
                int(sin_fecha.sum()),
                len(serie),
                fecha_respaldo.date(),
            )
        else:
            log.warning(
                "%d filas quedan sin fecha_cast: no traen fecha y no hay fecha de "
                "respaldo (usa --periodo YYYY-MM)",
                int(sin_fecha.sum()),
            )

    return pd.DataFrame(
        {
            "fecha_cast": fechas.dt.strftime("%Y-%m-%d"),
            "fecha_inferida": inferida.astype(bool),
            "apostrofo": apostrofo,
            "no_reconocida": no_reconocidos,
        },
        index=serie.index,
    )
