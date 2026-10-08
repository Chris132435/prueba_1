"""Lectura de exports de Admetricks en CSV o Excel, siempre como texto."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

EXTENSIONES = (".csv", ".xlsx", ".xlsm", ".xls")


def _separador(ruta: Path, encoding: str) -> str:
    """Detecta el separador (, ; o tab). Excel en español suele guardar CSV con ';'."""
    with open(ruta, encoding=encoding, newline="") as fh:
        muestra = fh.read(64 * 1024)
    try:
        return csv.Sniffer().sniff(muestra, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def leer_tabla(ruta: str | Path) -> pd.DataFrame:
    """Lee un CSV o Excel con todas las columnas como texto (sin que pandas adivine tipos).

    - CSV: UTF-8 (con o sin BOM) y, si falla, Latin-1; separador detectado automáticamente.
    - Excel: la primera hoja.
    Las celdas vacías quedan como NaN; el resto, tal como viene (``NULL`` incluido).
    """
    ruta = Path(ruta)
    extension = ruta.suffix.lower()
    if extension not in EXTENSIONES:
        raise ValueError(f"Formato no soportado: {ruta.name} (usa {', '.join(EXTENSIONES)})")

    if extension == ".csv":
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                sep = _separador(ruta, encoding)
                return pd.read_csv(
                    ruta, encoding=encoding, sep=sep, dtype=str, keep_default_na=False, na_values=[""]
                )
            except UnicodeDecodeError:
                log.warning("%s no es UTF-8; se lee como Latin-1", ruta.name)
        raise AssertionError("inalcanzable")

    df = pd.read_excel(ruta, sheet_name=0, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def listar_archivos(carpeta: Path) -> list[Path]:
    """CSV/Excel de ``carpeta``, del más antiguo al más reciente (ignora temporales ~$ de Excel)."""
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return []
    archivos = [
        p for p in carpeta.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONES and not p.name.startswith("~$")
    ]
    return sorted(archivos, key=lambda p: (p.stat().st_mtime, p.name))


def archivo_mas_reciente(carpeta: Path) -> Path:
    """El CSV/Excel modificado más recientemente en ``carpeta``."""
    archivos = listar_archivos(carpeta)
    if not archivos:
        raise FileNotFoundError(f"No se indicó un archivo y no hay CSV/Excel en {carpeta}")
    return archivos[-1]
