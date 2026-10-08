"""Paso 3: selección de anuncios y descarga directa por HTTP.

La selección se hace solo con los metadatos del CSV (sin descargar nada): por cada marca,
los ``n`` anuncios (URLs de Advertisement) con más impresiones. Opcionalmente se puede limitar
antes a las ``candidatos`` URLs que más se repiten.

La descarga por defecto la hace el navegador (ver :mod:`.navegador`); este módulo tiene
además la descarga HTTP directa en paralelo, que sirve de respaldo.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import columnas as col

log = logging.getLogger(__name__)

_TAMANO_BLOQUE = 1 << 16
ALCANCES = ("marca", "global")


def seleccionar_top_anuncios(
    df: pd.DataFrame, n: int = 3, candidatos: int | None = None, alcance: str = "marca"
) -> pd.DataFrame:
    """Elige los anuncios a descargar sin descargar nada.

    Un anuncio es una URL única de Advertisement; sus impresiones son la suma de la columna
    Impresiones de todas sus filas. Para cada grupo (cada marca, o todo el archivo si
    ``alcance="global"``) se toman los ``n`` anuncios con más impresiones (desempate: más
    repeticiones, luego URL).

    Si se indica ``candidatos`` (opcional), antes se limita cada grupo a las ``candidatos``
    URLs que más se repiten (desempate: más impresiones, luego URL).

    Devuelve una fila por anuncio elegido con marca, ranking (1..n), advertisement,
    repeticiones, ranking_repeticiones e impresiones, ordenado por grupo y ranking.
    """
    if alcance not in ALCANCES:
        raise ValueError(f"alcance debe ser uno de {ALCANCES}, no {alcance!r}")

    marca = col.columna(df, *col.MARCA)
    anuncio = col.columna(df, *col.ADVERTISEMENT)
    impresiones, _ = col.quitar_apostrofo(df[col.columna(df, *col.IMPRESIONES)])

    datos = pd.DataFrame(
        {
            "marca": df[marca],
            "advertisement": df[anuncio].astype("string").str.strip(),
            "impresiones": pd.to_numeric(impresiones, errors="coerce").fillna(0),
        }
    )
    datos = datos[datos["advertisement"].notna() & (datos["advertisement"] != "")]

    agregado = datos.groupby(["marca", "advertisement"], as_index=False).agg(
        repeticiones=("impresiones", "size"), impresiones=("impresiones", "sum")
    )
    agregado["_grupo"] = agregado["marca"] if alcance == "marca" else "todas"

    agregado = agregado.sort_values(
        ["_grupo", "repeticiones", "impresiones", "advertisement"],
        ascending=[True, False, False, True],
    )
    agregado["ranking_repeticiones"] = agregado.groupby("_grupo").cumcount() + 1

    # Filtro opcional: solo las URLs que más se repiten.
    candidatas = agregado[agregado["ranking_repeticiones"] <= candidatos] if candidatos else agregado

    # Los n anuncios con más impresiones.
    top = (
        candidatas.sort_values(
            ["_grupo", "impresiones", "repeticiones", "advertisement"],
            ascending=[True, False, False, True],
        )
        .groupby("_grupo", sort=False)
        .head(n)
        .copy()
    )
    top["ranking"] = top.groupby("_grupo").cumcount() + 1

    top["impresiones"] = top["impresiones"].astype("int64")
    columnas = ["marca", "ranking", "advertisement", "repeticiones", "ranking_repeticiones", "impresiones"]
    return top[columnas].reset_index(drop=True)


def _slug(texto: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_).strip("._")
    return slug or "sin_nombre"


def nombre_archivo(url: str) -> str:
    """Nombre de archivo seguro derivado de la URL (sin rutas ni caracteres raros)."""
    base = Path(unquote(urlparse(url).path)).name
    nombre = _slug(base)
    if nombre == "sin_nombre":
        nombre = hashlib.sha1(url.encode()).hexdigest()[:16]
    return nombre


def ruta_destino(destino: Path, marca: str, ranking: int, url: str) -> Path:
    return Path(destino) / _slug(marca) / f"{ranking:02d}_{nombre_archivo(url)}"


def resultado_inicial(url: str, archivo: Path, sobrescribir: bool) -> dict:
    """Resultado base de una descarga; ya resuelto si la URL es inválida o el archivo existe."""
    resultado = {"archivo": str(archivo), "estado": "", "bytes": 0, "sha256": "", "error": ""}
    if urlparse(url).scheme not in ("http", "https"):
        resultado.update(estado="error", error="URL no es http(s)")
    elif archivo.exists() and archivo.stat().st_size > 0 and not sobrescribir:
        resultado.update(estado="existente", bytes=archivo.stat().st_size)
    return resultado


def sha256_archivo(ruta: Path) -> str:
    sha = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(_TAMANO_BLOQUE), b""):
            sha.update(bloque)
    return sha.hexdigest()


def finalizar_archivo(temporal: Path, archivo: Path, resultado: dict) -> None:
    """Valida el ``.part`` descargado y lo renombra al nombre final."""
    if temporal.stat().st_size == 0:
        raise IOError("respuesta vacía")
    sha = sha256_archivo(temporal)
    temporal.replace(archivo)
    resultado.update(estado="descargado", bytes=archivo.stat().st_size, sha256=sha, error="")


def armar_manifiesto(top: pd.DataFrame, resultados: list[dict]) -> pd.DataFrame:
    manifiesto = pd.concat([top.reset_index(drop=True), pd.DataFrame(resultados)], axis=1)
    for r in manifiesto[manifiesto["estado"] == "error"].itertuples(index=False):
        log.error("Falló la descarga de %s (%s): %s", r.advertisement, r.marca, r.error)
    return manifiesto


# ---------------------------------------------------------------- descarga HTTP directa

_local = threading.local()


def _sesion(reintentos: int) -> requests.Session:
    """Una sesión HTTP por hilo (requests.Session no es thread-safe)."""
    sesion = getattr(_local, "sesion", None)
    if sesion is None:
        reintento = Retry(
            total=reintentos,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        sesion = requests.Session()
        sesion.headers["User-Agent"] = "admetricks-pipeline/1.0"
        adaptador = HTTPAdapter(max_retries=reintento)
        sesion.mount("http://", adaptador)
        sesion.mount("https://", adaptador)
        _local.sesion = sesion
    return sesion


def _descargar_uno(
    url: str, archivo: Path, timeout: float, reintentos: int, sobrescribir: bool
) -> dict:
    resultado = resultado_inicial(url, archivo, sobrescribir)
    if resultado["estado"]:
        return resultado

    archivo.parent.mkdir(parents=True, exist_ok=True)
    temporal = archivo.with_name(archivo.name + ".part")
    try:
        with _sesion(reintentos).get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            with open(temporal, "wb") as fh:
                for bloque in resp.iter_content(_TAMANO_BLOQUE):
                    fh.write(bloque)
        finalizar_archivo(temporal, archivo, resultado)
    except Exception as exc:  # se registra en el manifiesto; no se detiene el lote
        temporal.unlink(missing_ok=True)
        resultado.update(estado="error", error=f"{type(exc).__name__}: {exc}")
    return resultado


def descargar_anuncios(
    top: pd.DataFrame,
    destino: str | Path,
    workers: int = 8,
    timeout: float = 30,
    reintentos: int = 3,
    sobrescribir: bool = False,
) -> pd.DataFrame:
    """Descarga por HTTP, en paralelo, el archivo de cada anuncio de ``top`` en
    ``destino/<marca>/<ranking>_<archivo>``.

    Es idempotente: los archivos ya descargados se omiten salvo ``sobrescribir=True``.
    Devuelve ``top`` con las columnas archivo, estado, bytes, sha256 y error.
    """
    destino = Path(destino)
    tareas = [
        (fila.advertisement, ruta_destino(destino, fila.marca, fila.ranking, fila.advertisement))
        for fila in top.itertuples(index=False)
    ]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        resultados = list(
            pool.map(
                lambda t: _descargar_uno(t[0], t[1], timeout, reintentos, sobrescribir), tareas
            )
        )
    return armar_manifiesto(top, resultados)
