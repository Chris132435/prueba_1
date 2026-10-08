from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ARCHIVO_REGISTRO = "ejecucion.json"
ARCHIVO_RESUMEN = "resumen_ejecucion.txt"
CARPETA_HISTORIAL = "historial"


def guardar(registro: dict, salida: Path) -> Path:
    salida.mkdir(parents=True, exist_ok=True)
    contenido = json.dumps(registro, ensure_ascii=False, indent=2, default=str)
    ultimo = salida / ARCHIVO_REGISTRO
    ultimo.write_text(contenido, encoding="utf-8")

    historial = salida / CARPETA_HISTORIAL
    historial.mkdir(exist_ok=True)
    marca_tiempo = registro.get("inicio", datetime.now().isoformat()).replace(":", "").replace("-", "")
    (historial / f"ejecucion_{marca_tiempo[:15]}.json").write_text(contenido, encoding="utf-8")

    (salida / ARCHIVO_RESUMEN).write_text(formatear(registro), encoding="utf-8")
    return ultimo


def leer(salida: Path) -> dict | None:
    ruta = salida / ARCHIVO_REGISTRO
    if not ruta.exists():
        return None
    return json.loads(ruta.read_text(encoding="utf-8"))


def _n(valor) -> str:
    if valor is None:
        return "-"
    if isinstance(valor, float):
        return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{valor:,}".replace(",", ".")


def _hora(iso: str | None) -> str:
    return iso.replace("T", " ")[:19] if iso else "-"


def formatear(r: dict) -> str:
    lineas: list[str] = []
    add = lineas.append
    linea = "═" * 64

    add(linea)
    add("  RESUMEN DE LA EJECUCIÓN · Pipeline Admetricks")
    add(linea)
    estado = {"ok": "OK", "con_errores": "TERMINÓ CON ERRORES", "fallo": "FALLÓ"}.get(r.get("estado"), r.get("estado"))
    add(f"Estado     : {estado}")
    add(f"Inicio     : {_hora(r.get('inicio'))}")
    add(f"Fin        : {_hora(r.get('fin'))}  ({_n(r.get('duracion_s'))} s)")
    if r.get("error"):
        add(f"Error      : {r['error']}")

    if "archivos" in r:
        archivos = r["archivos"]
        if not archivos:
            add("Archivos   : no había archivos nuevos en la carpeta de datos; nada que procesar")
        for i, a in enumerate(archivos, 1):
            add("")
            add(f"──────── ARCHIVO {i} de {len(archivos)} " + "─" * 40)
            _bloque_archivo(add, a)
    elif r.get("entrada"):
        _bloque_archivo(add, r)

    _bloque_final(add, r)
    add(linea)
    return "\n".join(lineas) + "\n"


def _bloque_archivo(add, r: dict) -> None:
    ent = r.get("entrada", {})
    add(f"Archivo    : {ent.get('archivo', '-')}")
    add(f"Filas      : {_n(ent.get('filas'))} · columnas: {_n(ent.get('columnas'))}")
    if r.get("error") and "archivos" not in r and "inicio" not in r:
        add(f"⚠ Error    : {r['error']}")

    p1 = r.get("paso1")
    if p1:
        add("")
        add("PASO 1 · Conversión de fechas → columna fecha_cast")
        add(f"  Fechas convertidas desde la columna Fecha : {_n(p1['convertidas'])}")
        aviso = "   ⚠ posible error en la descarga del archivo" if p1["con_apostrofo"] else ""
        add(f"  Valores con apóstrofo inicial (') quitado : {_n(p1['con_apostrofo'])}{aviso}")
        add(f"  Sin fecha (ej. '00:00.0') → respaldo      : {_n(p1['inferidas'])}"
            + (f"  (respaldo {p1['fecha_respaldo']})" if p1.get("fecha_respaldo") else ""))
        add(f"  Formato no reconocido                     : {_n(p1['no_reconocidas'])}")
        add(f"  Filas que quedaron sin fecha_cast         : {_n(p1['sin_fecha'])}")
        add(f"  Rango de fecha_cast                       : {p1.get('desde') or '-'} a {p1.get('hasta') or '-'}")

    p2 = r.get("paso2")
    if p2:
        add("")
        add("PASO 2 · Análisis de costos → columna cpm_delta")
        add(f"  CPM = (Valorización Local / Impresiones) × 1000 · promedio {p2['metodo']} por mes y marca")
        add(f"  Filas con CPM calculado    : {_n(p2['filas_con_cpm'])}")
        add(f"  Filas sin impresiones      : {_n(p2['filas_sin_impresiones'])} (cpm_delta vacío)")
        add(f"  Meses · marcas             : {', '.join(p2['meses']) or '-'} · {_n(p2['marcas'])} marcas")
        add(f"  CPM mediana / promedio     : {_n(p2['cpm_mediana'])} / {_n(p2['cpm_promedio'])}")
        add(f"  cpm_delta mínimo / máximo  : {_n(p2['cpm_delta_min'])} / {_n(p2['cpm_delta_max'])}")
        if p2.get("fila_mas_cara"):
            f = p2["fila_mas_cara"]
            add(f"  Fila más cara vs. su marca : {f['marca']} ({f['mes']}) cpm {_n(f['cpm'])} vs. promedio {_n(f['cpm_promedio'])}")

    p3 = r.get("paso3")
    if p3:
        add("")
        add("PASO 3 · Consumo de datos → descarga de anuncios")
        grupo = "por marca" if p3["alcance"] == "marca" else "en todo el archivo"
        add(f"  URLs únicas en Advertisement               : {_n(p3['urls_unicas'])}")
        if p3.get("candidatos_por_grupo"):
            add(f"  Filtro previo · top {p3['candidatos_por_grupo']} URLs más repetidas {grupo}")
            add(f"             candidatas                      : {_n(p3['candidatas'])}")
        add(f"  Criterio: los {p3['top']} anuncios con más impresiones {grupo}")
        add(f"  Anuncios seleccionados                     : {_n(p3['seleccionados'])} de {_n(p3['marcas'])} marcas")
        if p3.get("modo") == "omitido":
            add("  Descargas                                  : omitidas (--sin-descargas)")
        else:
            nav = p3.get("navegador") or {}
            if p3.get("modo") == "navegador":
                add(f"  Descargado con el navegador                : {nav.get('nombre')} ({nav.get('motivo')})")
            else:
                add(f"  Descargado por HTTP directo                : {p3.get('motivo_modo', '')}")
            add(f"  Navegador predeterminado detectado         : {nav.get('por_defecto') or 'no detectado'}")
            est = p3.get("estados", {})
            add(f"  Descargados / ya existían / con error      : {_n(est.get('descargado', 0))} / "
                f"{_n(est.get('existente', 0))} / {_n(est.get('error', 0))}")
            add(f"  Volumen en disco                           : {_n(round(p3.get('bytes', 0) / 1_048_576, 2))} MB")
            errores = p3.get("errores", [])
            if errores:
                add(f"  Errores ({len(errores)}):")
                for e in errores[:10]:
                    add(f"    - {e['marca']} #{e['ranking']}: {e['error']}")
                if len(errores) > 10:
                    add(f"    … y {len(errores) - 10} más (ver *_top_anuncios.csv)")
        bdd = p3.get("bd")
        if bdd:
            if bdd.get("error"):
                add(f"  Registro en la base local                  : ⚠ no se pudo ({bdd['error']})")
            else:
                add(f"  Registro en la base local (tabla descargas): {_n(bdd['registros'])} filas con la ruta local "
                    f"(total {_n(bdd['total'])})")
        if p3.get("principales"):
            add("  Anuncios con más impresiones:")
            for t in p3["principales"][:5]:
                add(f"    {t['marca']} #{t['ranking']} · {_n(t['impresiones'])} impresiones · "
                    f"{_n(t['repeticiones'])} repeticiones · {t.get('estado') or '-'}")

    bd = r.get("base_datos")
    if bd:
        add("")
        titulo = "--formateo (borrar y recargar)" if bd["modo"] == "formateo" else "--update (agregar filas)"
        add(f"BASE DE DATOS · {titulo}")
        add(f"  Base SQLite                : {bd['db']}")
        add(f"  Carga n.º                  : {bd['id_carga']}")
        if bd["modo"] == "formateo":
            add(f"  Filas borradas             : {_n(bd['filas_borradas'])} (ID reiniciado)")
        add(f"  Filas insertadas           : {_n(bd['filas_insertadas'])}  (ID {bd['id_desde']} a {bd['id_hasta']})")
        add(f"  Total en la tabla anuncios : {_n(bd['total_tabla'])} filas de {_n(bd['archivos_en_tabla'])} archivo(s)")
        if bd.get("columnas_agregadas"):
            add(f"  Columnas agregadas a la tabla: {', '.join(bd['columnas_agregadas'])}")

    if r.get("salidas") and "inicio" not in r:
        add("  Generados:")
        for nombre, ruta in r["salidas"].items():
            add(f"    {nombre:<20}: {ruta}")


def _bloque_final(add, r: dict) -> None:
    nube = r.get("nube")
    if nube:
        add("")
        add("RESPALDO EN LA NUBE · Neon (PostgreSQL)")
        if nube.get("error"):
            add(f"  Destino                    : {nube.get('destino', '-')}")
            add(f"  ⚠ Falló (la nube quedó con el respaldo anterior): {nube['error']}")
        else:
            add(f"  Origen (SQLite local)      : {nube['origen']}")
            add(f"  Destino (PostgreSQL)       : {nube['host']} / {nube['base']}")
            for tabla, filas in nube["filas"].items():
                add(f"  {tabla:<27}: {_n(filas)} filas copiadas y verificadas")
            add(f"  Duración                   : {_n(nube['duracion_s'])} s")

    if r.get("salidas"):
        add("")
        add("Archivos generados:")
        for nombre, ruta in r["salidas"].items():
            add(f"  {nombre:<22}: {ruta}")
