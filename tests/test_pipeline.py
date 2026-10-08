import functools
import http.server
import os
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

from admetricks_pipeline import (
    calcular_cpm_delta,
    convertir_fechas,
    descargar_anuncios,
    detalle_cpm,
    periodo_desde_nombre,
    seleccionar_top_anuncios,
)
from admetricks_pipeline import navegador as nav
from admetricks_pipeline.cli import main
from admetricks_pipeline.descargas import nombre_archivo

CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")


def test_convertir_fechas_formatos_variados():
    serie = pd.Series(
        [
            "2026-01-15",
            "2026-01-15 00:00:00.0",
            "2026/01/15",
            "2026-01-15T23:59:59-05:00",
            "15/01/2026",
            "5-1-2026 10:00",
            "46037",
            "31/02/2026",
        ]
    )
    r = convertir_fechas(serie)
    assert r["fecha_cast"].tolist()[:7] == ["2026-01-15"] * 4 + ["2026-01-15", "2026-01-05", "2026-01-15"]
    assert pd.isna(r["fecha_cast"].iloc[7])
    assert r["no_reconocida"].tolist() == [False] * 7 + [True]
    assert not r["fecha_inferida"].any()


def test_convertir_fechas_quita_apostrofo_inicial():
    serie = pd.Series(["'2026-10-01 00:00:00.0", " '15/10/2026", "’2026-10-20", "'00:00.0", "2026-10-02"])
    r = convertir_fechas(serie, pd.Timestamp("2026-10-01"))
    assert r["fecha_cast"].tolist() == ["2026-10-01", "2026-10-15", "2026-10-20", "2026-10-01", "2026-10-02"]
    assert r["apostrofo"].tolist() == [True, True, True, True, False]
    assert r["fecha_inferida"].tolist() == [False, False, False, True, False]


def test_convertir_fechas_solo_hora_usa_respaldo():
    serie = pd.Series(["00:00.0", "2026-03-02", None, ""])
    r = convertir_fechas(serie, pd.Timestamp("2026-10-01"))
    assert r["fecha_cast"].tolist() == ["2026-10-01", "2026-03-02", "2026-10-01", "2026-10-01"]
    assert r["fecha_inferida"].tolist() == [True, False, True, True]


@pytest.mark.parametrize(
    "nombre, esperado",
    [
        ("2026-10-Admetricks-Mascotas-Dummy.csv", "2026-10-01"),
        ("export_2025-03-15.csv", "2025-03-15"),
        ("sin_fecha.csv", None),
        ("2026-13-x.csv", None),
    ],
)
def test_periodo_desde_nombre(nombre, esperado):
    fecha = periodo_desde_nombre(nombre)
    assert (None if fecha is None else str(fecha.date())) == esperado


def _df_costos():
    return pd.DataFrame(
        {
            "fecha_cast": ["2026-01-03", "2026-01-20", "2026-02-01", "2026-01-10", "2026-01-11"],
            "Marca": ["a", "a", "a", "b", "b"],
            "Impresiones": [1000, 2000, 1000, 1000, 0],
            "Valorización Local": [5.84, 25.12, 7.0, 3.0, 1.0],
        }
    )


def test_cpm_delta_agrega_solo_esa_columna():
    df = _df_costos()
    r = calcular_cpm_delta(df)
    assert list(r.columns) == list(df.columns) + ["cpm_delta"]


def test_cpm_delta_simple_ejemplo_enunciado():
    r = detalle_cpm(_df_costos())
    assert r.loc[0, "cpm"] == pytest.approx(5.84)
    assert r.loc[0, "cpm_promedio"] == pytest.approx(9.20)
    assert r.loc[0, "cpm_delta"] == pytest.approx(3.36)
    assert r.loc[1, "cpm_delta"] == pytest.approx(-3.36)
    assert r.loc[2, "cpm_delta"] == pytest.approx(0)
    assert pd.isna(r.loc[4, "cpm"]) and pd.isna(r.loc[4, "cpm_delta"])
    assert r.loc[3, "cpm_promedio"] == pytest.approx(3.0)
    assert calcular_cpm_delta(_df_costos())["cpm_delta"].equals(r["cpm_delta"])


def test_cpm_delta_ponderado():
    r = detalle_cpm(_df_costos(), metodo="ponderado")
    assert r.loc[0, "cpm_promedio"] == pytest.approx(10.32)
    assert r.loc[0, "cpm_delta"] == pytest.approx(10.32 - 5.84)


def test_cpm_acepta_apostrofo_y_nombre_alternativo():
    df = _df_costos().astype({"Impresiones": str}).rename(columns={"Valorización Local": "Valoracion local"})
    df.loc[0, "Impresiones"] = "'1000"
    assert calcular_cpm_delta(df).loc[0, "cpm_delta"] == pytest.approx(3.36)


def _df_anuncios():
    filas = []
    for url, reps, impr in [("u1", 6, 10), ("u2", 5, 30), ("u3", 4, 50), ("u4", 3, 5), ("u5", 2, 400), ("u6", 1, 9999)]:
        filas += [{"Marca": "a", "Advertisement": url, "Impresiones": impr}] * reps
    filas += [{"Marca": "b", "Advertisement": "u9", "Impresiones": 5}]
    return pd.DataFrame(filas)


def test_top_anuncios_por_impresiones_por_defecto():
    top = seleccionar_top_anuncios(_df_anuncios(), n=3)
    a = top[top.marca == "a"]
    assert a["advertisement"].tolist() == ["u6", "u5", "u3"]
    assert a["impresiones"].tolist() == [9999, 800, 200]
    assert a["ranking"].tolist() == [1, 2, 3]
    assert top[top.marca == "b"]["advertisement"].tolist() == ["u9"]


def test_top_anuncios_con_filtro_opcional_de_repetidas():
    top = seleccionar_top_anuncios(_df_anuncios(), n=3, candidatos=5)
    a = top[top.marca == "a"]
    assert a["advertisement"].tolist() == ["u5", "u3", "u2"]
    assert a["impresiones"].tolist() == [800, 200, 150]
    assert a["ranking"].tolist() == [1, 2, 3]
    assert a["ranking_repeticiones"].tolist() == [5, 3, 2]
    assert top[top.marca == "b"]["advertisement"].tolist() == ["u9"]


def test_top_anuncios_alcance_global():
    top = seleccionar_top_anuncios(_df_anuncios(), n=3, alcance="global")
    assert top["advertisement"].tolist() == ["u6", "u5", "u3"]
    assert top["ranking"].tolist() == [1, 2, 3]


def test_nombre_archivo_seguro():
    assert nombre_archivo("https://x.com/a/banner_1.jpg?x=1") == "banner_1.jpg"
    assert nombre_archivo("https://x.com/../../etc/pass%20wd") == "pass_wd"
    assert len(nombre_archivo("https://x.com/")) == 16


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("BraveHTML", "brave"),
        ("MSEdgeHTM", "edge"),
        ("com.microsoft.edgemac", "edge"),
        ("com.brave.browser", "brave"),
        ("brave-browser.desktop\n", "brave"),
        ("microsoft-edge.desktop", "edge"),
        ("ChromeHTML", "chrome"),
        ("", None),
    ],
)
def test_identificar_navegador(texto, esperado):
    assert nav.identificar(texto) == esperado


def test_elegir_navegador_prefiere_el_predeterminado(monkeypatch):
    instalados = {"brave": "/x/brave", "edge": "/x/edge"}
    monkeypatch.setattr(nav, "buscar_ejecutable", instalados.get)

    monkeypatch.setattr(nav, "navegador_por_defecto", lambda: "edge")
    assert nav.elegir_navegador().nombre == "edge"

    monkeypatch.setattr(nav, "navegador_por_defecto", lambda: "brave")
    assert nav.elegir_navegador().nombre == "brave"

    monkeypatch.setattr(nav, "navegador_por_defecto", lambda: "firefox")
    elegido = nav.elegir_navegador()
    assert elegido.nombre == "brave" and "firefox" in elegido.motivo

    del instalados["brave"]
    assert nav.elegir_navegador().nombre == "edge"
    assert nav.elegir_navegador("brave") is None


class _Manejador(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        if self.path.startswith("/adjunto"):
            self.send_header("Content-Disposition", "attachment; filename=adjunto.jpg")
        super().end_headers()


@pytest.fixture
def servidor(tmp_path):
    raiz = tmp_path / "servidor"
    raiz.mkdir()
    (raiz / "banner_1.jpg").write_bytes(b"imagen")
    (raiz / "video_1.mp4").write_bytes(os.urandom(300_000))
    (raiz / "adjunto.jpg").write_bytes(b"adjunto")
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Manejador, directory=str(raiz)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", raiz
    httpd.shutdown()


@pytest.fixture(autouse=True)
def sin_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def entorno_aislado(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMETRICKS_DB", str(tmp_path / "pruebas.db"))
    monkeypatch.setenv("NEON_DATABASE_URL", "")


def _top(url):
    return pd.DataFrame(
        {
            "marca": ["Pet's Table", "Pet's Table", "otra", "otra"],
            "ranking": [1, 2, 1, 2],
            "advertisement": [f"{url}/banner_1.jpg", f"{url}/video_1.mp4", f"{url}/no_existe.jpg", f"{url}/adjunto.jpg"],
            "impresiones": [4, 3, 2, 1],
        }
    )


def test_descargar_anuncios_http(servidor, tmp_path):
    url, raiz = servidor
    destino = tmp_path / "anuncios"
    m = descargar_anuncios(_top(url), destino, workers=2, reintentos=0)
    assert m["estado"].tolist() == ["descargado", "descargado", "error", "descargado"]
    assert (destino / "Pet_s_Table" / "01_banner_1.jpg").read_bytes() == b"imagen"
    assert (destino / "Pet_s_Table" / "02_video_1.mp4").read_bytes() == (raiz / "video_1.mp4").read_bytes()
    assert "404" in m.loc[2, "error"]
    assert not list(destino.rglob("*.part"))

    m2 = descargar_anuncios(_top(url), destino, workers=2, reintentos=0)
    assert m2["estado"].tolist() == ["existente", "existente", "error", "existente"]


requiere_chromium = pytest.mark.skipif(
    not Path(CHROMIUM).exists(), reason="no hay un navegador Chromium para la prueba (CHROMIUM_PATH)"
)


@requiere_chromium
def test_descargar_con_navegador_en_orden(servidor, tmp_path):
    pytest.importorskip("playwright")
    url, raiz = servidor
    destino = tmp_path / "anuncios"
    navegador = nav.Navegador("personalizado", CHROMIUM, None, "prueba")
    m = nav.descargar_con_navegador(_top(url), destino, navegador, timeout=20, reintentos=0, oculto=True)
    assert m["estado"].tolist() == ["descargado", "descargado", "error", "descargado"]
    assert (destino / "Pet_s_Table" / "02_video_1.mp4").read_bytes() == (raiz / "video_1.mp4").read_bytes()
    assert (destino / "otra" / "02_adjunto.jpg").read_bytes() == b"adjunto"
    assert "404" in m.loc[2, "error"]
    assert not list(destino.rglob("*.part"))

    m2 = nav.descargar_con_navegador(_top(url), destino, navegador, timeout=20, reintentos=0, oculto=True)
    assert m2["estado"].tolist() == ["existente", "existente", "error", "existente"]


def _csv_prueba(tmp_path, url):
    csv = tmp_path / "2026-10-Admetricks-Prueba.csv"
    pd.DataFrame(
        {
            "Fecha": ["'2026-10-05 00:00:00.0", "00:00.0", "00:00.0"],
            "Marca": ["a", "a", "b"],
            "Advertisement": [f"{url}/banner_1.jpg", f"{url}/video_1.mp4", f"{url}/banner_1.jpg"],
            "Impresiones": ["1000", "2000", "500"],
            "Valorización Local": ["5.84", "25.12", "1.0"],
        }
    ).to_csv(csv, index=False, encoding="utf-8-sig")
    return csv


def test_cli_end_to_end_y_resume(servidor, tmp_path, capsys):
    url, _ = servidor
    csv = _csv_prueba(tmp_path, url)
    salida = tmp_path / "out"
    assert main([str(csv), "--salida", str(salida), "--modo-descarga", "http", "--reintentos", "0"]) == 0

    procesado = pd.read_csv(salida / "2026-10-Admetricks-Prueba_procesado.csv", encoding="utf-8-sig")
    assert list(procesado.columns[-2:]) == ["fecha_cast", "cpm_delta"]
    assert procesado["fecha_cast"].tolist() == ["2026-10-05", "2026-10-01", "2026-10-01"]
    assert procesado.loc[0, "cpm_delta"] == pytest.approx(3.36)
    manifiesto = pd.read_csv(salida / "2026-10-Admetricks-Prueba_top_anuncios.csv", encoding="utf-8-sig")
    assert set(manifiesto["estado"]) == {"descargado"}
    assert (salida / "anuncios" / "b" / "01_banner_1.jpg").exists()
    assert (salida / "logs" / "pipeline.log").exists()
    assert (salida / "resumen_ejecucion.txt").exists()

    capsys.readouterr()
    assert main(["--resume", "--salida", str(salida)]) == 0
    resumen = capsys.readouterr().out
    assert "Estado     : OK" in resumen
    assert "apóstrofo inicial (') quitado : 1" in resumen
    assert "Descargados / ya existían / con error      : 3 / 0 / 0" in resumen


def test_resume_sin_ejecuciones(tmp_path, capsys):
    assert main(["--resume", "--salida", str(tmp_path / "vacio")]) == 1
    assert "No hay ejecuciones" in capsys.readouterr().out


def test_cli_sin_consola(servidor, tmp_path, monkeypatch):
    url, _ = servidor
    csv = _csv_prueba(tmp_path, url)
    salida = tmp_path / "out"
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    codigo = main([str(csv), "--salida", str(salida), "--modo-descarga", "http", "--reintentos", "0"])
    monkeypatch.undo()
    assert codigo == 0
    assert "Listo (ok)" in (salida / "logs" / "pipeline.log").read_text(encoding="utf-8")


def test_cli_registra_fallo(tmp_path):
    salida = tmp_path / "out"
    assert main([str(tmp_path / "no_existe.csv"), "--salida", str(salida)]) == 2
    assert "FALLÓ" in (salida / "resumen_ejecucion.txt").read_text(encoding="utf-8")


import sqlite3

from admetricks_pipeline.basedatos import esquema_sql
from admetricks_pipeline.lectura import leer_tabla

_ESQUEMA_USUARIO = """CREATE TABLE anuncios (
    ID INTEGER PRIMARY KEY AUTOINCREMENT, Fecha TEXT, Industria TEXT, Marca TEXT, Anunciante TEXT,
    "Nombre de Campaña" TEXT, "Landing Page" TEXT, Etiquetas TEXT, "Sitio Web" TEXT,
    "Secciones del Sitio Web" TEXT, Editor TEXT, Formato TEXT, "Tamaño de Aviso" TEXT,
    "Duración de Video" TEXT, "Omitible Video" TEXT, Posición TEXT, Advertisement TEXT, Screenshot TEXT,
    Países TEXT, Dispositivo TEXT, "Hospedado Por" TEXT, "Vendido Por" TEXT, Impacto TEXT,
    Impresiones INTEGER, "Valorización Local" REAL, "Valorización Dólares" REAL, "Ads Count" INTEGER);"""


def _csv_bd(tmp_path, nombre="2026-10-Admetricks-BD.csv"):
    csv = tmp_path / nombre
    fila = {"Fecha": "'2026-10-05 00:00:00.0", "Marca": "a", "Etiquetas": "NULL", "Omitible Video": "0",
            "Advertisement": "https://x/1.jpg", "Impresiones": "1000", "Valorización Local": "5.84",
            "Ads Count": "3"}
    filas = [fila, dict(fila), {**fila, "Fecha": "00:00.0", "Impresiones": "2000", "Valorización Local": "25.12"}]
    pd.DataFrame(filas).to_csv(csv, index=False, encoding="utf-8-sig")
    return csv


def _consulta(db, sql):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_update_agrega_siempre_filas_nuevas(tmp_path):
    csv, db, salida = _csv_bd(tmp_path), tmp_path / "a.db", tmp_path / "out"
    assert main([str(csv), "--update", "--db", str(db), "--salida", str(salida)]) == 0
    assert _consulta(db, "SELECT ID FROM anuncios ORDER BY ID") == [(1,), (2,), (3,)]

    assert main([str(csv), "--update", "--db", str(db), "--salida", str(salida)]) == 0
    assert _consulta(db, "SELECT MIN(ID), MAX(ID), COUNT(*) FROM anuncios") == [(1, 6, 6)]
    assert _consulta(db, "SELECT id_carga, COUNT(*) FROM anuncios GROUP BY id_carga") == [(1, 3), (2, 3)]
    assert _consulta(db, "SELECT modo, filas_insertadas FROM cargas") == [("update", 3), ("update", 3)]

    fila = _consulta(db, 'SELECT Fecha, fecha_cast, Etiquetas, typeof(Impresiones), '
                         'typeof("Valorización Local"), typeof("Omitible Video"), archivo_origen '
                         'FROM anuncios WHERE ID = 1')[0]
    assert fila == ("'2026-10-05 00:00:00.0", "2026-10-05", None, "integer", "real", "integer", csv.name)


def test_formateo_borra_y_recarga_desde_id_1(tmp_path, capsys):
    csv, db, salida = _csv_bd(tmp_path), tmp_path / "a.db", tmp_path / "out"
    for _ in range(2):
        main([str(csv), "--update", "--db", str(db), "--salida", str(salida)])
    assert main([str(csv), "--formateo", "--db", str(db), "--salida", str(salida)]) == 0
    assert _consulta(db, "SELECT MIN(ID), MAX(ID), COUNT(*) FROM anuncios") == [(1, 3, 3)]
    assert _consulta(db, "SELECT modo, filas_borradas FROM cargas WHERE id = 3") == [("formateo", 6)]

    capsys.readouterr()
    main(["--resume", "--salida", str(salida)])
    resumen = capsys.readouterr().out
    assert "--formateo (borrar y recargar)" in resumen
    assert "Filas borradas             : 6 (ID reiniciado)" in resumen


def test_formateo_fallido_no_borra_nada(tmp_path):
    csv, db, salida = _csv_bd(tmp_path), tmp_path / "a.db", tmp_path / "out"
    main([str(csv), "--update", "--db", str(db), "--salida", str(salida)])
    roto = tmp_path / "roto.csv"
    roto.write_text("Fecha,Marca\n2026-10-01,a\n", encoding="utf-8")
    con = sqlite3.connect(db)
    con.execute("CREATE TRIGGER falla BEFORE INSERT ON anuncios BEGIN SELECT RAISE(ABORT, 'falla'); END")
    con.commit()
    con.close()
    assert main([str(roto), "--formateo", "--db", str(db), "--salida", str(salida)]) == 2
    assert _consulta(db, "SELECT COUNT(*) FROM anuncios") == [(3,)]


def test_update_sobre_tabla_creada_a_mano_y_excel(tmp_path):
    db = tmp_path / "usuario.db"
    con = sqlite3.connect(db)
    con.executescript(_ESQUEMA_USUARIO)
    con.close()
    xlsx = tmp_path / "2026-11-Admetricks.xlsx"
    leer_tabla(_csv_bd(tmp_path)).to_excel(xlsx, index=False)

    assert main([str(xlsx), "--update", "--db", str(db), "--salida", str(tmp_path / "out")]) == 0
    columnas = {c[1] for c in _consulta(db, "PRAGMA table_info(anuncios)")}
    assert {"fecha_cast", "archivo_origen", "id_carga"} <= columnas
    assert _consulta(db, "SELECT COUNT(*), MAX(fecha_cast) FROM anuncios") == [(3, "2026-11-01")]


def test_vista_cpm_delta_igual_al_pipeline(tmp_path):
    csv, db = _csv_bd(tmp_path), tmp_path / "a.db"
    main([str(csv), "--update", "--db", str(db), "--salida", str(tmp_path / "out")])
    deltas = [round(d, 6) for (d,) in _consulta(db, "SELECT cpm_delta FROM v_anuncios_cpm ORDER BY ID")]
    assert deltas == [2.24, 2.24, -4.48]


def test_esquema_sql_de_referencia_esta_al_dia():
    ruta = Path(__file__).resolve().parents[1] / "sql" / "esquema.sql"
    assert esquema_sql().strip() in ruta.read_text(encoding="utf-8")


def test_leer_tabla_csv_con_punto_y_coma(tmp_path):
    csv = tmp_path / "pc.csv"
    csv.write_text("Fecha;Marca;Impresiones\n2026-10-01;a;10\n", encoding="utf-8")
    assert leer_tabla(csv).columns.tolist() == ["Fecha", "Marca", "Impresiones"]


from admetricks_pipeline.nube import ocultar_url

PG_TEST_URL = os.environ.get("ADMETRICKS_PG_TEST_URL")
requiere_pg = pytest.mark.skipif(not PG_TEST_URL, reason="define ADMETRICKS_PG_TEST_URL para probar contra PostgreSQL")


def test_pipeline_registra_descargas_con_ruta_local(servidor, tmp_path):
    url, _ = servidor
    csv = _csv_prueba(tmp_path, url)
    db, salida = tmp_path / "local.db", tmp_path / "out"
    args = [str(csv), "--salida", str(salida), "--db", str(db), "--modo-descarga", "http", "--reintentos", "0"]
    assert main(args) == 0
    filas = _consulta(db, "SELECT marca, ranking, estado, ruta_local, bytes, sha256, modo FROM descargas ORDER BY marca, ranking")
    assert [(m, r, e) for m, r, e, *_ in filas] == [("a", 1, "descargado"), ("a", 2, "descargado"), ("b", 1, "descargado")]
    for _, _, _, ruta, tam, sha, modo in filas:
        assert Path(ruta).is_absolute() and Path(ruta).exists()
        assert tam > 0 and len(sha) == 64 and modo == "http"
    assert main(args) == 0
    assert _consulta(db, "SELECT COUNT(*) FROM descargas") == [(6,)]
    assert _consulta(db, "SELECT COUNT(*), MIN(estado) FROM v_descargas_ultimas") == [(3, "existente")]


def test_pipeline_sin_bd_no_registra(servidor, tmp_path):
    url, _ = servidor
    db = tmp_path / "local.db"
    main([str(_csv_prueba(tmp_path, url)), "--salida", str(tmp_path / "out"), "--db", str(db),
          "--modo-descarga", "http", "--sin-bd"])
    assert not db.exists()


def test_ocultar_url():
    url = "postgresql://ana:s3cr3t@ep-late-lab-b48wgsfv.us-east-2.aws.neon.tech/neondb?sslmode=require"
    assert ocultar_url(url) == "postgresql://ana:***@ep-late-lab-b48wgsfv.us-east-2.aws.neon.tech/neondb"
    assert "s3cr3t" not in ocultar_url(url)


def test_respaldo_sin_conexion_configurada(tmp_path, capsys):
    csv, db, salida = _csv_bd(tmp_path), tmp_path / "a.db", tmp_path / "out"
    main([str(csv), "--update", "--db", str(db), "--salida", str(salida)])
    assert main(["--respaldo-nube", "--db", str(db), "--salida", str(salida)]) == 2
    assert "NEON_DATABASE_URL" in (salida / "resumen_ejecucion.txt").read_text(encoding="utf-8")


@pytest.fixture
def pg_limpia():
    import psycopg

    with psycopg.connect(PG_TEST_URL, autocommit=True) as con:
        con.execute("DROP VIEW IF EXISTS v_anuncios_cpm, v_descargas_ultimas")
        con.execute("DROP TABLE IF EXISTS anuncios, cargas, descargas, respaldos")
    yield PG_TEST_URL


@requiere_pg
def test_respaldo_nube_copia_y_reemplaza(servidor, tmp_path, pg_limpia, capsys):
    import psycopg

    url, _ = servidor
    csv = _csv_prueba(tmp_path, url)
    db, salida = tmp_path / "local.db", tmp_path / "out"
    main([str(csv), "--update", "--db", str(db), "--salida", str(salida)])
    main([str(csv), "--salida", str(salida), "--db", str(db), "--modo-descarga", "http", "--reintentos", "0"])

    assert main(["--respaldo-nube", "--db", str(db), "--neon-url", pg_limpia, "--salida", str(salida)]) == 0
    with psycopg.connect(pg_limpia) as con:
        cuenta = lambda t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert (cuenta("anuncios"), cuenta("cargas"), cuenta("descargas"), cuenta("respaldos")) == (3, 1, 3, 1)
        fila = con.execute('SELECT "ID", "Marca", fecha_cast, "Impresiones", "Valorización Local" '
                           'FROM anuncios ORDER BY "ID" LIMIT 1').fetchone()
        assert fila == (1, "a", "2026-10-05", 1000, 5.84)
        deltas = [round(d, 6) for (d,) in con.execute('SELECT cpm_delta FROM v_anuncios_cpm ORDER BY "ID"')]
        assert deltas == [3.36, -3.36, 0.0]
        rutas = [r for (r,) in con.execute("SELECT ruta_local FROM descargas")]
        assert all(r and Path(r).is_absolute() for r in rutas)

    main([str(csv), "--update", "--db", str(db), "--salida", str(salida)])
    assert main(["--respaldo-nube", "--db", str(db), "--neon-url", pg_limpia, "--salida", str(salida)]) == 0
    with psycopg.connect(pg_limpia) as con:
        assert con.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0] == 6
        assert con.execute("SELECT COUNT(*) FROM respaldos").fetchone()[0] == 2

    capsys.readouterr()
    main(["--resume", "--salida", str(salida)])
    resumen = capsys.readouterr().out
    assert "RESPALDO EN LA NUBE" in resumen and "anuncios                   : 6 filas" in resumen
    assert "***" not in resumen or "@" not in resumen


@requiere_pg
def test_respaldo_fallido_no_toca_la_nube(tmp_path, pg_limpia):
    import psycopg

    csv, db, salida = _csv_bd(tmp_path), tmp_path / "a.db", tmp_path / "out"
    main([str(csv), "--update", "--db", str(db), "--salida", str(salida)])
    assert main(["--respaldo-nube", "--db", str(db), "--neon-url", pg_limpia, "--salida", str(salida)]) == 0

    con = sqlite3.connect(db)
    con.execute("UPDATE anuncios SET Impresiones = 'no es número' WHERE ID = 2")
    con.execute("INSERT INTO cargas (fecha, modo, archivo) VALUES ('x', 'update', 'y')")
    con.commit()
    con.close()
    assert main(["--respaldo-nube", "--db", str(db), "--neon-url", pg_limpia, "--salida", str(salida)]) == 2
    with psycopg.connect(pg_limpia) as pg:
        assert pg.execute("SELECT COUNT(*) FROM cargas").fetchone()[0] == 1
        assert pg.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0] == 3


def _correr_pipeline(tmp_path, url, *extra):
    csv = _csv_prueba(tmp_path, url)
    db, salida = tmp_path / "local.db", tmp_path / "out"
    codigo = main([str(csv), "--salida", str(salida), "--db", str(db), "--modo-descarga", "http",
                   "--reintentos", "0", *extra])
    return codigo, salida


def test_pipeline_sin_neon_configurado_no_respalda(servidor, tmp_path):
    url, _ = servidor
    codigo, salida = _correr_pipeline(tmp_path, url)
    assert codigo == 0
    assert "RESPALDO EN LA NUBE" not in (salida / "resumen_ejecucion.txt").read_text(encoding="utf-8")


def test_pipeline_respaldo_fallido_no_deshace_descargas(servidor, tmp_path, capsys):
    url, _ = servidor
    neon_caido = "postgresql://usuario:secreta@127.0.0.1:1/neondb?connect_timeout=2"
    codigo, salida = _correr_pipeline(tmp_path, url, "--neon-url", neon_caido)
    assert codigo == 1
    manifiesto = pd.read_csv(salida / "2026-10-Admetricks-Prueba_top_anuncios.csv", encoding="utf-8-sig")
    assert set(manifiesto["estado"]) == {"descargado"}
    capsys.readouterr()
    main(["--resume", "--salida", str(salida)])
    resumen = capsys.readouterr().out
    assert "⚠ Falló" in resumen and "secreta" not in resumen


def test_pipeline_sin_nube_omite_el_respaldo(servidor, tmp_path):
    url, _ = servidor
    codigo, salida = _correr_pipeline(tmp_path, url, "--neon-url", "postgresql://x:y@127.0.0.1:1/db", "--sin-nube")
    assert codigo == 0


@requiere_pg
def test_pipeline_respalda_en_la_nube_al_terminar(servidor, tmp_path, pg_limpia):
    import psycopg

    url, _ = servidor
    codigo, salida = _correr_pipeline(tmp_path, url, "--neon-url", pg_limpia)
    assert codigo == 0
    with psycopg.connect(pg_limpia) as con:
        assert con.execute("SELECT COUNT(*) FROM descargas").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM respaldos").fetchone()[0] == 1
    assert "descargas                  : 3 filas" in (salida / "resumen_ejecucion.txt").read_text(encoding="utf-8")


def _csv_mes(carpeta, mes, url, marca="a"):
    csv = carpeta / f"2026-{mes:02d}-Admetricks-Prueba.csv"
    pd.DataFrame(
        {
            "Fecha": [f"2026-{mes:02d}-05", f"2026-{mes:02d}-06"],
            "Marca": [marca, marca],
            "Advertisement": [f"{url}/banner_1.jpg", f"{url}/video_1.mp4"],
            "Impresiones": [str(1000 * mes), "500"],
            "Valorización Local": ["5.84", "1.0"],
        }
    ).to_csv(csv, index=False, encoding="utf-8-sig")
    return csv


def _main_nuevos(tmp_path, *extra):
    return main(["--entrada", str(tmp_path / "raw"), "--db", str(tmp_path / "local.db"),
                 "--salida", str(tmp_path / "out"), "--modo-descarga", "http", "--reintentos", "0", *extra])


def test_procesa_todos_los_archivos_nuevos_y_los_recuerda(servidor, tmp_path, capsys):
    url, _ = servidor
    raw = tmp_path / "raw"
    raw.mkdir()
    oct_, nov = _csv_mes(raw, 10, url), _csv_mes(raw, 11, url)
    os.utime(oct_, (1_000_000, 1_000_000))
    db = tmp_path / "local.db"

    assert _main_nuevos(tmp_path) == 0
    assert _consulta(db, "SELECT archivo FROM cargas ORDER BY id") == [(str(oct_),), (str(nov),)]
    assert _consulta(db, "SELECT COUNT(*), MIN(fecha_cast), MAX(fecha_cast) FROM anuncios") == [(4, "2026-10-05", "2026-11-06")]
    assert _consulta(db, "SELECT COUNT(DISTINCT id_ejecucion), COUNT(*) FROM descargas") == [(2, 4)]
    for f in (oct_, nov):
        assert (tmp_path / "out" / f"{f.stem}_procesado.csv").exists()
        assert (tmp_path / "out" / f"{f.stem}_top_anuncios.csv").exists()
    capsys.readouterr()
    main(["--resume", "--salida", str(tmp_path / "out")])
    resumen = capsys.readouterr().out
    assert "ARCHIVO 1 de 2" in resumen and "ARCHIVO 2 de 2" in resumen

    assert _main_nuevos(tmp_path) == 0
    assert _consulta(db, "SELECT COUNT(*) FROM cargas") == [(2,)]
    assert _consulta(db, "SELECT COUNT(*) FROM anuncios") == [(4,)]
    capsys.readouterr()
    main(["--resume", "--salida", str(tmp_path / "out")])
    assert "no había archivos nuevos" in capsys.readouterr().out

    (raw / "copia_de_octubre.csv").write_bytes(oct_.read_bytes())
    dic = _csv_mes(raw, 12, url)
    assert _main_nuevos(tmp_path) == 0
    assert _consulta(db, "SELECT archivo FROM cargas ORDER BY id") == [(str(oct_),), (str(nov),), (str(dic),)]
    assert _consulta(db, "SELECT COUNT(*) FROM anuncios") == [(6,)]


def test_archivo_que_falla_queda_pendiente(servidor, tmp_path):
    url, _ = servidor
    raw = tmp_path / "raw"
    raw.mkdir()
    bueno = _csv_mes(raw, 10, url)
    roto = raw / "2026-11-roto.csv"
    roto.write_text("Fecha,Marca\n2026-11-01,a\n", encoding="utf-8")
    db = tmp_path / "local.db"

    assert _main_nuevos(tmp_path) == 2
    assert _consulta(db, "SELECT archivo FROM cargas") == [(str(bueno),)]

    from admetricks_pipeline.basedatos import archivos_pendientes
    assert archivos_pendientes(raw, db) == [roto]


def test_ultimo_procesa_el_mas_reciente_sin_cargarlo(servidor, tmp_path):
    url, _ = servidor
    raw = tmp_path / "raw"
    raw.mkdir()
    _csv_mes(raw, 10, url)
    assert _main_nuevos(tmp_path, "--ultimo") == 0
    assert _consulta(tmp_path / "local.db", "SELECT COUNT(*) FROM cargas") == [(0,)]
    assert (tmp_path / "out" / "2026-10-Admetricks-Prueba_top_anuncios.csv").exists()


def _base_con_dummy(tmp_path, url):
    dummy = tmp_path / "2026-09-dummy.csv"
    pd.DataFrame({"Fecha": ["2026-09-01"] * 3, "Marca": ["vieja"] * 3,
                  "Advertisement": [f"{url}/banner_1.jpg"] * 3, "Impresiones": ["1", "2", "3"],
                  "Valorización Local": ["1", "1", "1"]}).to_csv(dummy, index=False, encoding="utf-8-sig")
    db = tmp_path / "local.db"
    for _ in range(2):
        main([str(dummy), "--update", "--db", str(db), "--salida", str(tmp_path / "out")])
    return db


def test_formateo_completo_reemplaza_con_todos_los_archivos(servidor, tmp_path, capsys):
    url, _ = servidor
    db = _base_con_dummy(tmp_path, url)
    raw = tmp_path / "raw"
    raw.mkdir()
    oct_, nov = _csv_mes(raw, 10, url, marca="real"), _csv_mes(raw, 11, url, marca="real")
    os.utime(oct_, (1_000_000, 1_000_000))

    assert _main_nuevos(tmp_path, "--formateo") == 0
    assert _consulta(db, 'SELECT MIN(ID), MAX(ID), COUNT(*) FROM anuncios') == [(1, 4, 4)]
    assert _consulta(db, "SELECT DISTINCT Marca FROM anuncios") == [("real",)]
    assert _consulta(db, "SELECT archivo_origen, MIN(ID) FROM anuncios GROUP BY 1 ORDER BY 2") == [
        (oct_.name, 1), (nov.name, 3)]
    assert _consulta(db, "SELECT modo, filas_borradas FROM cargas WHERE modo = 'formateo' ORDER BY id") == [
        ("formateo", 6), ("formateo", 0)]
    for f in (oct_, nov):
        assert (tmp_path / "out" / f"{f.stem}_top_anuncios.csv").exists()
    capsys.readouterr()
    main(["--resume", "--salida", str(tmp_path / "out")])
    resumen = capsys.readouterr().out
    assert "ARCHIVO 2 de 2" in resumen and "--formateo (borrar y recargar)" in resumen
    assert "Filas borradas             : 6 (ID reiniciado)" in resumen

    assert _main_nuevos(tmp_path) == 0
    assert _consulta(db, "SELECT COUNT(*) FROM anuncios") == [(4,)]


def test_formateo_con_un_archivo_roto_no_borra_nada(servidor, tmp_path, capsys):
    url, _ = servidor
    db = _base_con_dummy(tmp_path, url)
    raw = tmp_path / "raw"
    raw.mkdir()
    _csv_mes(raw, 10, url, marca="real")
    (raw / "2026-11-roto.csv").write_text("Fecha,Marca\n2026-11-01,a\n", encoding="utf-8")

    assert _main_nuevos(tmp_path, "--formateo") == 2
    assert _consulta(db, "SELECT COUNT(*), MIN(Marca) FROM anuncios") == [(6, "vieja")]
    capsys.readouterr()
    main(["--resume", "--salida", str(tmp_path / "out")])
    assert "Formateo cancelado" in capsys.readouterr().out


def test_formateo_con_carpeta_vacia_no_borra_nada(servidor, tmp_path):
    url, _ = servidor
    db = _base_con_dummy(tmp_path, url)
    (tmp_path / "raw").mkdir()
    assert _main_nuevos(tmp_path, "--formateo") == 2
    assert _consulta(db, "SELECT COUNT(*) FROM anuncios") == [(6,)]
