from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

TABLAS = ("cargas", "anuncios", "descargas")

_VISTA_CPM = """
CREATE VIEW v_anuncios_cpm AS
WITH base AS (
    SELECT a.*,
           substr(a.fecha_cast, 1, 7) AS mes,
           CASE WHEN a."Impresiones" > 0
                THEN (a."Valorización Local" / a."Impresiones") * 1000.0 END AS cpm
    FROM anuncios a
)
SELECT base.*,
       AVG(cpm) OVER (PARTITION BY mes, "Marca") - cpm AS cpm_delta
FROM base;
"""

_VISTA_DESCARGAS = """
CREATE VIEW v_descargas_ultimas AS
SELECT * FROM descargas
WHERE id_ejecucion = (SELECT MAX(id_ejecucion) FROM descargas);
"""

_RESPALDOS = """
CREATE TABLE IF NOT EXISTS respaldos (
    id BIGSERIAL PRIMARY KEY,
    fecha TIMESTAMPTZ NOT NULL DEFAULT now(),
    origen TEXT,
    equipo TEXT,
    filas_cargas BIGINT,
    filas_anuncios BIGINT,
    filas_descargas BIGINT,
    duracion_s DOUBLE PRECISION
);
"""


def ocultar_url(url: str) -> str:
    partes = urlsplit(url)
    usuario = partes.username or ""
    host = partes.hostname or ""
    puerto = f":{partes.port}" if partes.port else ""
    return f"{partes.scheme}://{usuario}:***@{host}{puerto}{partes.path}"


def _q(nombre: str) -> str:
    return '"' + nombre.replace('"', '""') + '"'


def _tipo_pg(tipo_sqlite: str, es_pk: bool) -> str:
    t = (tipo_sqlite or "").upper()
    if "INT" in t:
        base = "BIGINT"
    elif any(x in t for x in ("REAL", "FLOA", "DOUB", "NUM")):
        base = "DOUBLE PRECISION"
    else:
        base = "TEXT"
    return f"{base} PRIMARY KEY" if es_pk else base


def _columnas_sqlite(con: sqlite3.Connection, tabla: str) -> list[tuple[str, str, bool]]:
    return [(c[1], c[2], bool(c[5])) for c in con.execute(f"PRAGMA table_info({_q(tabla)})")]


def respaldar(ruta_db: str | Path, url: str) -> dict:
    import platform

    import psycopg

    ruta_db = Path(ruta_db)
    if not ruta_db.exists():
        raise FileNotFoundError(f"No existe la base local {ruta_db}")
    if not url:
        raise ValueError(
            "Falta la conexión a la nube: define NEON_DATABASE_URL en el archivo .env "
            "(ver .env.ejemplo) o usa --neon-url"
        )

    inicio = time.monotonic()
    local = sqlite3.connect(ruta_db)
    try:
        existentes = {r[0] for r in local.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tablas = [t for t in TABLAS if t in existentes]
        if not tablas:
            raise ValueError(f"La base {ruta_db} no tiene tablas para respaldar ({', '.join(TABLAS)})")
        esquemas = {t: _columnas_sqlite(local, t) for t in tablas}

        log.info("Conectando a %s", ocultar_url(url))
        with psycopg.connect(url, connect_timeout=20, application_name="admetricks-pipeline") as pg:
            with pg.transaction(), pg.cursor() as cur:
                cur.execute("DROP VIEW IF EXISTS v_anuncios_cpm, v_descargas_ultimas")
                for tabla, columnas in esquemas.items():
                    definicion = ", ".join(f"{_q(n)} {_tipo_pg(t, pk)}" for n, t, pk in columnas)
                    cur.execute(f"CREATE TABLE IF NOT EXISTS {_q(tabla)} ({definicion})")
                    for n, t, _ in columnas:
                        cur.execute(f"ALTER TABLE {_q(tabla)} ADD COLUMN IF NOT EXISTS {_q(n)} {_tipo_pg(t, False)}")

                cur.execute("TRUNCATE " + ", ".join(_q(t) for t in tablas))
                filas = {}
                for tabla, columnas in esquemas.items():
                    nombres = [n for n, _, _ in columnas]
                    lista = ", ".join(_q(n) for n in nombres)
                    with cur.copy(f"COPY {_q(tabla)} ({lista}) FROM STDIN") as copia:
                        n = 0
                        for fila in local.execute(f"SELECT {lista} FROM {_q(tabla)}"):
                            copia.write_row(fila)
                            n += 1
                    filas[tabla] = n
                    log.info("Nube · %s: %d filas copiadas", tabla, n)

                if "anuncios" in tablas:
                    cur.execute(_VISTA_CPM)
                if "descargas" in tablas:
                    cur.execute(_VISTA_DESCARGAS)
                cur.execute(_RESPALDOS)
                duracion = round(time.monotonic() - inicio, 2)
                cur.execute(
                    "INSERT INTO respaldos (origen, equipo, filas_cargas, filas_anuncios, filas_descargas, duracion_s) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (str(ruta_db.resolve()), platform.node(), filas.get("cargas"), filas.get("anuncios"),
                     filas.get("descargas"), duracion),
                )

            with pg.cursor() as cur:
                en_nube = {}
                for tabla in tablas:
                    cur.execute(f"SELECT COUNT(*) FROM {_q(tabla)}")
                    en_nube[tabla] = cur.fetchone()[0]
    finally:
        local.close()

    diferencias = {t: (filas[t], en_nube[t]) for t in tablas if filas[t] != en_nube[t]}
    if diferencias:
        raise RuntimeError(f"El respaldo no coincide con la base local (local, nube): {diferencias}")

    partes = urlsplit(url)
    resultado = {
        "destino": ocultar_url(url),
        "host": partes.hostname,
        "base": partes.path.lstrip("/"),
        "origen": str(ruta_db),
        "filas": filas,
        "duracion_s": round(time.monotonic() - inicio, 2),
    }
    log.info("Respaldo en la nube completo: %s", filas)
    return resultado
