"""Carga de los exports de Admetricks a una base SQLite local.

- ``--update``: agrega todas las filas del archivo como entradas nuevas, cada una con su ID
  automático, aunque sus datos ya estén en la base.
- ``--formateo``: borra todos los anuncios, reinicia los ID y vuelve a cargar el archivo.

Cada carga es una sola transacción (si falla, la base queda como estaba) y queda registrada en
la tabla ``cargas``; cada fila guarda ``id_carga`` y ``archivo_origen`` para saber de qué carga
vino.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import columnas as col
from .fechas import convertir_fechas

log = logging.getLogger(__name__)

TABLA = "anuncios"
MODOS = ("update", "formateo")

# Columnas del export en el orden del archivo, con su tipo en SQLite.
COLUMNAS_ARCHIVO: list[tuple[str, str]] = [
    ("Fecha", "TEXT"),
    ("Industria", "TEXT"),
    ("Marca", "TEXT"),
    ("Anunciante", "TEXT"),
    ("Nombre de Campaña", "TEXT"),
    ("Landing Page", "TEXT"),
    ("Etiquetas", "TEXT"),
    ("Sitio Web", "TEXT"),
    ("Secciones del Sitio Web", "TEXT"),
    ("Editor", "TEXT"),
    ("Formato", "TEXT"),
    ("Tamaño de Aviso", "TEXT"),
    ("Duración de Video", "TEXT"),  # trae valores como "1,0": se guarda tal cual
    ("Omitible Video", "INTEGER"),
    ("Posición", "TEXT"),
    ("Advertisement", "TEXT"),
    ("Screenshot", "TEXT"),
    ("Países", "TEXT"),
    ("Dispositivo", "TEXT"),
    ("Hospedado Por", "TEXT"),
    ("Vendido Por", "TEXT"),
    ("Impacto", "INTEGER"),
    ("Impresiones", "INTEGER"),
    ("Valorización Local", "REAL"),
    ("Valorización Dólares", "REAL"),
    ("Ads Count", "INTEGER"),
]

# Columnas agregadas por el pipeline (además del ID).
COLUMNAS_EXTRA: list[tuple[str, str]] = [
    ("fecha_cast", "TEXT"),  # yyyy-mm-dd (paso 1)
    ("archivo_origen", "TEXT"),
    ("id_carga", "INTEGER REFERENCES cargas(id)"),
]

_ESQUEMA_FIJO = f"""
CREATE TABLE IF NOT EXISTS cargas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    modo TEXT NOT NULL CHECK (modo IN ('update', 'formateo')),
    archivo TEXT NOT NULL,
    sha256_archivo TEXT,
    filas_leidas INTEGER,
    filas_insertadas INTEGER,
    filas_borradas INTEGER
);
-- Paso 3: un registro por anuncio seleccionado en cada ejecución. Solo se guarda la ruta del
-- archivo descargado en el equipo (ruta_local), no la imagen o el video.
CREATE TABLE IF NOT EXISTS descargas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_ejecucion INTEGER NOT NULL,
    fecha_ejecucion TEXT NOT NULL,
    archivo_origen TEXT,
    marca TEXT NOT NULL,
    ranking INTEGER,
    advertisement TEXT NOT NULL,
    impresiones INTEGER,
    repeticiones INTEGER,
    estado TEXT,
    ruta_local TEXT,
    equipo TEXT,
    bytes INTEGER,
    sha256 TEXT,
    error TEXT,
    modo TEXT,
    navegador TEXT
);
CREATE INDEX IF NOT EXISTS ix_descargas_marca ON descargas(marca, ranking);
CREATE INDEX IF NOT EXISTS ix_descargas_advertisement ON descargas(advertisement);
DROP VIEW IF EXISTS v_descargas_ultimas;
CREATE VIEW v_descargas_ultimas AS
SELECT * FROM descargas
WHERE id_ejecucion = (SELECT MAX(id_ejecucion) FROM descargas);

CREATE INDEX IF NOT EXISTS ix_{TABLA}_marca_fecha ON {TABLA}(Marca, fecha_cast);
CREATE INDEX IF NOT EXISTS ix_{TABLA}_advertisement ON {TABLA}(Advertisement);
CREATE INDEX IF NOT EXISTS ix_{TABLA}_id_carga ON {TABLA}(id_carga);

-- Paso 2 dentro de la base: CPM y cpm_delta se calculan al consultar, así siempre están
-- al día aunque se agreguen filas del mismo mes y marca con --update.
DROP VIEW IF EXISTS v_{TABLA}_cpm;
CREATE VIEW v_{TABLA}_cpm AS
WITH base AS (
    SELECT a.*,
           substr(a.fecha_cast, 1, 7) AS mes,
           CASE WHEN a.Impresiones > 0
                THEN (a."Valorización Local" / a.Impresiones) * 1000.0 END AS cpm
    FROM {TABLA} a
)
SELECT base.*,
       AVG(cpm) OVER (PARTITION BY mes, Marca) - cpm AS cpm_delta
FROM base;
"""


def _q(nombre: str) -> str:
    return '"' + nombre.replace('"', '""') + '"'


def ddl_tabla() -> str:
    """CREATE TABLE recomendado para ``anuncios``."""
    definiciones = ["    ID INTEGER PRIMARY KEY AUTOINCREMENT"]
    for nombre, tipo in COLUMNAS_ARCHIVO[:1] + COLUMNAS_EXTRA[:1] + COLUMNAS_ARCHIVO[1:] + COLUMNAS_EXTRA[1:]:
        definiciones.append(f"    {_q(nombre)} {tipo}")
    return f"CREATE TABLE IF NOT EXISTS {TABLA} (\n" + ",\n".join(definiciones) + "\n);"


def esquema_sql() -> str:
    return ddl_tabla() + "\n" + _ESQUEMA_FIJO


def conectar(ruta: str | Path) -> sqlite3.Connection:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def preparar_esquema(con: sqlite3.Connection) -> list[str]:
    """Crea la tabla si no existe; si ya existe (p. ej. creada a mano), agrega las columnas
    que le falten. Después crea la tabla de cargas, los índices y la vista. Devuelve las
    columnas agregadas."""
    con.execute(ddl_tabla())
    existentes = {fila[1] for fila in con.execute(f"PRAGMA table_info({TABLA})")}
    agregadas = []
    for nombre, tipo in COLUMNAS_ARCHIVO + COLUMNAS_EXTRA:
        if nombre not in existentes:
            con.execute(f"ALTER TABLE {TABLA} ADD COLUMN {_q(nombre)} {tipo}")
            agregadas.append(nombre)
    if agregadas:
        log.info("Columnas agregadas a la tabla %s: %s", TABLA, agregadas)
    con.executescript(_ESQUEMA_FIJO)
    return agregadas


def preparar_filas(df: pd.DataFrame, archivo: Path, fecha_respaldo: pd.Timestamp | None) -> pd.DataFrame:
    """Convierte el DataFrame leído (todo texto) en las filas a insertar en ``anuncios``."""
    reales = {}
    for nombre, _ in COLUMNAS_ARCHIVO:
        try:
            reales[nombre] = col.columna(df, nombre)
        except KeyError:
            reales[nombre] = None
    faltantes = [n for n, r in reales.items() if r is None]
    if faltantes:
        log.warning("El archivo no trae las columnas %s; se cargan vacías", faltantes)
    sobrantes = set(df.columns) - {r for r in reales.values() if r}
    if sobrantes:
        log.warning("Columnas del archivo que no están en la tabla (se ignoran): %s", sorted(sobrantes))

    crudo = pd.DataFrame(
        {n: (df[r] if r else pd.Series(pd.NA, index=df.index, dtype="string")) for n, r in reales.items()}
    ).astype("string")

    filas = pd.DataFrame(index=df.index)
    for nombre, tipo in COLUMNAS_ARCHIVO:
        serie = crudo[nombre]
        if tipo == "TEXT":
            valor = serie.str.strip()
            filas[nombre] = valor.where(~valor.isin(["", "NULL", "null"]), None)
        else:
            limpio, _ = col.quitar_apostrofo(serie)
            numero = pd.to_numeric(limpio, errors="coerce")
            invalidos = numero.isna() & limpio.notna() & ~limpio.isin(["", "NULL", "null"])
            if invalidos.any():
                log.warning("%d valores no numéricos en %s quedan vacíos (ej.: %s)",
                            int(invalidos.sum()), nombre, limpio[invalidos].head(3).tolist())
            filas[nombre] = numero.round().astype("Int64") if tipo == "INTEGER" else numero

    filas.insert(1, "fecha_cast", convertir_fechas(crudo["Fecha"], fecha_respaldo)["fecha_cast"])
    filas["archivo_origen"] = Path(archivo).name
    return filas


def _sha256_archivo(ruta: Path) -> str:
    sha = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 20), b""):
            sha.update(bloque)
    return sha.hexdigest()


def cargar(
    ruta_db: str | Path,
    df: pd.DataFrame,
    archivo: Path,
    modo: str,
    fecha_respaldo: pd.Timestamp | None = None,
) -> dict:
    """Carga ``df`` (leído de ``archivo``) en la base según ``modo`` (update / formateo)."""
    return cargar_varios(ruta_db, [(df, archivo, fecha_respaldo)], modo)[0]


def cargar_varios(
    ruta_db: str | Path,
    lotes: list[tuple[pd.DataFrame, Path, pd.Timestamp | None]],
    modo: str,
) -> list[dict]:
    """Carga varios archivos en una sola transacción. ``lotes`` = [(df, archivo, fecha_respaldo)].

    Con ``modo="formateo"`` primero borra todos los anuncios y reinicia el ID; si cualquier
    archivo falla, no se borra ni se carga nada. Devuelve un resultado por archivo.
    """
    if modo not in MODOS:
        raise ValueError(f"modo debe ser uno de {MODOS}, no {modo!r}")
    if not lotes:
        raise ValueError("No hay archivos para cargar")

    preparados = []
    for df, archivo, fecha_respaldo in lotes:
        filas = preparar_filas(df, archivo, fecha_respaldo)
        valores = filas.astype(object).where(filas.notna(), None).values.tolist()
        preparados.append((archivo, list(filas.columns) + ["id_carga"], valores))

    resultados = []
    con = conectar(ruta_db)
    try:
        agregadas = preparar_esquema(con)
        with con:  # una transacción: borrar + cargar es todo o nada
            borradas = 0
            if modo == "formateo":
                borradas = con.execute(f"DELETE FROM {TABLA}").rowcount
                con.execute("DELETE FROM sqlite_sequence WHERE name = ?", (TABLA,))
                log.info("Formateo: %d filas borradas y ID reiniciado", borradas)

            for i, (archivo, columnas, valores) in enumerate(preparados):
                sql = (
                    f"INSERT INTO {TABLA} ({', '.join(_q(c) for c in columnas)}) "
                    f"VALUES ({', '.join('?' for _ in columnas)})"
                )
                borradas_aqui = borradas if i == 0 else 0
                id_carga = con.execute(
                    "INSERT INTO cargas (fecha, modo, archivo, sha256_archivo, filas_leidas) VALUES (?, ?, ?, ?, ?)",
                    (datetime.now().isoformat(timespec="seconds"), modo, str(archivo),
                     _sha256_archivo(archivo), len(valores)),
                ).lastrowid
                antes = con.total_changes
                con.executemany(sql, [v + [id_carga] for v in valores])
                insertadas = con.total_changes - antes
                id_min, id_max = con.execute(
                    f"SELECT MIN(ID), MAX(ID) FROM {TABLA} WHERE id_carga = ?", (id_carga,)
                ).fetchone()
                con.execute(
                    "UPDATE cargas SET filas_insertadas = ?, filas_borradas = ? WHERE id = ?",
                    (insertadas, borradas_aqui, id_carga),
                )
                resultados.append({
                    "db": str(ruta_db),
                    "modo": modo,
                    "id_carga": id_carga,
                    "archivo": str(archivo),
                    "filas_leidas": len(valores),
                    "filas_insertadas": insertadas,
                    "filas_borradas": borradas_aqui,
                    "id_desde": id_min,
                    "id_hasta": id_max,
                    "columnas_agregadas": agregadas if i == 0 else [],
                })
        total = con.execute(f"SELECT COUNT(*) FROM {TABLA}").fetchone()[0]
        archivos = con.execute(f"SELECT COUNT(DISTINCT archivo_origen) FROM {TABLA}").fetchone()[0]
    finally:
        con.close()

    for r in resultados:
        r.update(total_tabla=total, archivos_en_tabla=archivos)
        log.info(
            "Base %s (%s): %s · %d filas insertadas (ID %s a %s)",
            ruta_db, modo, Path(r["archivo"]).name, r["filas_insertadas"], r["id_desde"], r["id_hasta"],
        )
    log.info("Total en la tabla %s: %d filas", TABLA, total)
    return resultados


def registrar_descargas(ruta_db: str | Path, manifiesto: pd.DataFrame, archivo_origen: Path, info: dict) -> dict:
    """Guarda en la tabla ``descargas`` un registro por anuncio de ``manifiesto`` (salida del
    paso 3). Solo se guarda la ruta absoluta del archivo en este equipo, no el archivo."""
    fecha = datetime.now().isoformat(timespec="seconds")
    equipo = platform.node()
    navegador = (info.get("navegador") or {}).get("nombre")
    filas = []
    for r in manifiesto.itertuples(index=False):
        descargado = r.estado in ("descargado", "existente") and r.archivo
        filas.append((
            None, fecha, Path(archivo_origen).name, r.marca, int(r.ranking), r.advertisement,
            int(r.impresiones), int(getattr(r, "repeticiones", 0) or 0), r.estado,
            str(Path(r.archivo).resolve()) if descargado else None, equipo,
            int(r.bytes or 0), r.sha256 or None, r.error or None, info.get("modo"), navegador,
        ))
    con = conectar(ruta_db)
    try:
        preparar_esquema(con)
        with con:
            id_ejecucion = con.execute("SELECT COALESCE(MAX(id_ejecucion), 0) + 1 FROM descargas").fetchone()[0]
            con.executemany(
                "INSERT INTO descargas (id_ejecucion, fecha_ejecucion, archivo_origen, marca, ranking, advertisement, "
                "impresiones, repeticiones, estado, ruta_local, equipo, bytes, sha256, error, modo, navegador) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(id_ejecucion,) + f[1:] for f in filas],
            )
        total = con.execute("SELECT COUNT(*) FROM descargas").fetchone()[0]
    finally:
        con.close()
    log.info("Tabla descargas de %s: %d registros agregados (total %d)", ruta_db, len(filas), total)
    return {"db": str(ruta_db), "registros": len(filas), "total": total, "id_ejecucion": id_ejecucion}


def archivos_de_carpeta(carpeta: str | Path) -> list[Path]:
    """Todos los CSV/Excel de ``carpeta`` (del más antiguo al más reciente), sin repetir contenido."""
    from .lectura import listar_archivos

    unicos: list[Path] = []
    vistos: set[str] = set()
    for archivo in listar_archivos(carpeta):
        huella = _sha256_archivo(archivo)
        if huella not in vistos:
            vistos.add(huella)
            unicos.append(archivo)
    return unicos


def archivos_pendientes(carpeta: str | Path, ruta_db: str | Path) -> list[Path]:
    """Archivos de ``carpeta`` que todavía no se cargaron a la base, del más antiguo al más nuevo.

    Un archivo ya está procesado si su sha256 (huella del contenido) figura en la tabla
    ``cargas``: renombrarlo no hace que se repita, y si su contenido cambia se procesa de nuevo.
    Si en la carpeta hay dos archivos con el mismo contenido, se toma solo el primero.
    """
    from .lectura import listar_archivos

    cargados: set[str] = set()
    if Path(ruta_db).exists():
        con = sqlite3.connect(ruta_db)
        try:
            cargados = {r[0] for r in con.execute(
                "SELECT sha256_archivo FROM cargas WHERE sha256_archivo IS NOT NULL")}
        except sqlite3.OperationalError:
            pass  # la base todavía no tiene la tabla cargas
        finally:
            con.close()

    pendientes: list[Path] = []
    vistos: set[str] = set()
    for archivo in listar_archivos(carpeta):
        huella = _sha256_archivo(archivo)
        if huella in cargados or huella in vistos:
            continue
        vistos.add(huella)
        pendientes.append(archivo)
    return pendientes
