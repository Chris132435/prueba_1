-- Esquema SQLite de la base local de anuncios de Admetricks.
-- El script lo aplica solo (python main.py --update / --formateo); este archivo es de referencia
-- para crear la base a mano:  sqlite3 data/admetricks.db < sql/esquema.sql
--
-- Cambios respecto del CREATE TABLE original:
--   * "Omitible Video" e Impacto pasan a INTEGER (en el export son siempre números enteros).
--   * fecha_cast: la Fecha convertida a yyyy-mm-dd (paso 1), para filtrar y agrupar por mes.
--   * archivo_origen e id_carga: de qué archivo y de qué carga vino cada fila.
--   * Tabla cargas: un registro por cada --update / --formateo.
--   * Tabla descargas: los anuncios del paso 3 con la ruta local del archivo (no el archivo).
--   * Índices para las consultas por marca, fecha y anuncio.
--   * Vista v_anuncios_cpm: CPM y cpm_delta (paso 2) calculados al consultar.

CREATE TABLE IF NOT EXISTS anuncios (
    ID INTEGER PRIMARY KEY AUTOINCREMENT,
    "Fecha" TEXT,
    "fecha_cast" TEXT,
    "Industria" TEXT,
    "Marca" TEXT,
    "Anunciante" TEXT,
    "Nombre de Campaña" TEXT,
    "Landing Page" TEXT,
    "Etiquetas" TEXT,
    "Sitio Web" TEXT,
    "Secciones del Sitio Web" TEXT,
    "Editor" TEXT,
    "Formato" TEXT,
    "Tamaño de Aviso" TEXT,
    "Duración de Video" TEXT,
    "Omitible Video" INTEGER,
    "Posición" TEXT,
    "Advertisement" TEXT,
    "Screenshot" TEXT,
    "Países" TEXT,
    "Dispositivo" TEXT,
    "Hospedado Por" TEXT,
    "Vendido Por" TEXT,
    "Impacto" INTEGER,
    "Impresiones" INTEGER,
    "Valorización Local" REAL,
    "Valorización Dólares" REAL,
    "Ads Count" INTEGER,
    "archivo_origen" TEXT,
    "id_carga" INTEGER REFERENCES cargas(id)
);

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

CREATE INDEX IF NOT EXISTS ix_anuncios_marca_fecha ON anuncios(Marca, fecha_cast);
CREATE INDEX IF NOT EXISTS ix_anuncios_advertisement ON anuncios(Advertisement);
CREATE INDEX IF NOT EXISTS ix_anuncios_id_carga ON anuncios(id_carga);

-- Paso 2 dentro de la base: CPM y cpm_delta se calculan al consultar, así siempre están
-- al día aunque se agreguen filas del mismo mes y marca con --update.
DROP VIEW IF EXISTS v_anuncios_cpm;
CREATE VIEW v_anuncios_cpm AS
WITH base AS (
    SELECT a.*,
           substr(a.fecha_cast, 1, 7) AS mes,
           CASE WHEN a.Impresiones > 0
                THEN (a."Valorización Local" / a.Impresiones) * 1000.0 END AS cpm
    FROM anuncios a
)
SELECT base.*,
       AVG(cpm) OVER (PARTITION BY mes, Marca) - cpm AS cpm_delta
FROM base;
