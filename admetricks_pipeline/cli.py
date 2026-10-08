"""Punto de entrada: ``python main.py [archivo.csv]`` o ``python main.py --resume``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import columnas as col
from . import registro as reg
from .basedatos import cargar as cargar_bd
from .basedatos import archivos_de_carpeta, archivos_pendientes, cargar_varios, registrar_descargas
from .lectura import archivo_mas_reciente, leer_tabla
from .costos import METODOS_PROMEDIO, calcular_cpm_delta, detalle_cpm, resumen_cpm_mes_marca
from .descargas import ALCANCES, descargar_anuncios, seleccionar_top_anuncios
from .fechas import convertir_fechas, periodo_desde_nombre

log = logging.getLogger("admetricks_pipeline")


def _cargar_env() -> None:
    """Lee el archivo .env de la carpeta actual (la del proyecto), si existe.

    Ahí van NEON_DATABASE_URL (conexión a la nube) y ADMETRICKS_DB (ruta de la base local),
    para no escribirlos en cada comando ni dejarlos en el código.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(".env"), override=False)


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python main.py",
        description="Procesa un export de Admetricks: fecha_cast, cpm_delta y descarga "
        "de los anuncios con más impresiones por marca.",
    )
    p.add_argument("csv", type=Path, nargs="?",
                   help="CSV o Excel a procesar (default: todos los archivos nuevos de --entrada)")
    acciones = p.add_mutually_exclusive_group()
    acciones.add_argument("--resume", "--resumen", dest="resume", action="store_true",
                          help="Muestra el resumen de la última ejecución y termina")
    acciones.add_argument("--update", action="store_true",
                          help="Agrega a la base SQLite las filas del archivo que aún no están y termina")
    acciones.add_argument("--formateo", action="store_true",
                          help="Reemplaza los datos de la base: sin archivo, procesa todos los de --entrada "
                          "(pasos 1-3), borra la base, los carga y respalda; con archivo, solo lo recarga")
    acciones.add_argument("--respaldo-nube", "--respaldo", dest="respaldo_nube", action="store_true",
                          help="Copia la base SQLite local a PostgreSQL en la nube (Neon) y termina")
    p.add_argument("--db", type=Path, default=Path(os.environ.get("ADMETRICKS_DB") or "data/admetricks.db"),
                   help="Base SQLite local (default: ADMETRICKS_DB del .env, o data/admetricks.db)")
    p.add_argument("--neon-url", default=os.environ.get("NEON_DATABASE_URL"),
                   help="Conexión PostgreSQL de Neon (default: NEON_DATABASE_URL del .env)")
    p.add_argument("--sin-nube", action="store_true",
                   help="Al terminar python main.py, no respaldar la base local en Neon")
    p.add_argument("--sin-bd", action="store_true",
                   help="No registrar las descargas en la tabla descargas de la base local")
    p.add_argument("--entrada", type=Path, default=Path("data/raw"),
                   help="Carpeta de donde se toman los archivos (default: data/raw)")
    p.add_argument("--ultimo", action="store_true",
                   help="Procesar solo el archivo más reciente de --entrada, aunque ya se haya cargado, "
                   "sin cargarlo a la base")
    p.add_argument("--salida", type=Path, default=Path("output"), help="Carpeta de salida (default: output)")
    p.add_argument("--periodo", help="Fecha de respaldo YYYY-MM o YYYY-MM-DD para filas sin fecha "
                   "(default: se deduce del nombre del archivo)")
    p.add_argument("--cpm-metodo", choices=METODOS_PROMEDIO, default="simple",
                   help="Cómo calcular el CPM promedio por mes y marca (default: simple)")
    p.add_argument("--top", type=int, default=3,
                   help="Anuncios con más impresiones a descargar por marca (default: 3)")
    p.add_argument("--candidatos", type=int, default=0,
                   help="Opcional: antes de elegir el top, limitar cada marca a sus N URLs más "
                   "repetidas (default: 0 = sin este filtro)")
    p.add_argument("--alcance", choices=ALCANCES, default="marca",
                   help="Aplicar los filtros por marca o sobre todo el archivo (default: marca)")
    p.add_argument("--modo-descarga", choices=("navegador", "http"), default="navegador",
                   help="navegador: Brave/Edge, de a uno y en orden; http: directo y en paralelo")
    p.add_argument("--navegador", choices=("auto", "brave", "edge"), default="auto",
                   help="auto: el predeterminado si es Brave o Edge; si no, el que esté instalado")
    p.add_argument("--navegador-ruta", help="Ejecutable de un navegador Chromium a usar en su lugar")
    p.add_argument("--navegador-oculto", action="store_true", help="No mostrar la ventana del navegador")
    p.add_argument("--workers", type=int, default=8, help="Descargas en paralelo en modo http (default: 8)")
    p.add_argument("--timeout", type=float, help="Timeout por descarga en segundos "
                   "(default: 60 en navegador, 30 en http)")
    p.add_argument("--reintentos", type=int, default=2, help="Reintentos por descarga (default: 2)")
    p.add_argument("--sobrescribir", action="store_true", help="Volver a descargar archivos existentes")
    p.add_argument("--sin-descargas", action="store_true", help="Solo calcula; no descarga archivos")
    return p.parse_args(argv)


def _configurar_logs(salida: Path) -> Path:
    """Log a archivo siempre; a consola solo si hay una (con pythonw no la hay)."""
    carpeta = salida / "logs"
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / "pipeline.log"
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handlers: list[logging.Handler] = [logging.FileHandler(archivo, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    raiz = logging.getLogger()
    for h in list(raiz.handlers):
        raiz.removeHandler(h)
        h.close()
    for h in handlers:
        h.setFormatter(formato)
        raiz.addHandler(h)
    raiz.setLevel(logging.INFO)
    return archivo


def _csv_entrada(args: argparse.Namespace) -> Path:
    return args.csv if args.csv else archivo_mas_reciente(args.entrada)


def _fecha_respaldo(args: argparse.Namespace, csv: Path) -> pd.Timestamp | None:
    if args.periodo:
        texto = args.periodo if len(args.periodo) > 7 else f"{args.periodo}-01"
        fecha = pd.to_datetime(texto, format="%Y-%m-%d", errors="coerce")
        if pd.isna(fecha):
            raise ValueError(f"--periodo inválido: {args.periodo!r} (usa YYYY-MM o YYYY-MM-DD)")
        return fecha
    return periodo_desde_nombre(csv)


def _f(valor) -> float | None:
    return None if pd.isna(valor) else round(float(valor), 4)


def _descargar(args: argparse.Namespace, top: pd.DataFrame, destino: Path, info: dict) -> pd.DataFrame:
    """Descarga con el navegador si es posible; si no, por HTTP directo."""
    if args.modo_descarga == "navegador":
        from .navegador import elegir_navegador, navegador_por_defecto

        navegador = elegir_navegador(args.navegador, args.navegador_ruta)
        try:
            import playwright  # noqa: F401
        except ImportError:
            navegador, motivo = None, "Playwright no está instalado (pip install playwright)"
        else:
            motivo = "no se encontró Brave ni Edge instalado"

        if navegador is not None:
            from .navegador import descargar_con_navegador

            info.update(modo="navegador", navegador=vars(navegador))
            log.info("Descarga con %s: %s", navegador.nombre, navegador.motivo)
            return descargar_con_navegador(
                top, destino, navegador,
                timeout=args.timeout or 60, reintentos=args.reintentos,
                sobrescribir=args.sobrescribir, oculto=args.navegador_oculto,
            )
        log.warning("No se puede usar el navegador (%s); se descarga por HTTP directo", motivo)
        info.update(modo="http", motivo_modo=f"respaldo: {motivo}",
                    navegador={"por_defecto": navegador_por_defecto()})
    else:
        info.update(modo="http", motivo_modo="pedido con --modo-descarga http")

    return descargar_anuncios(
        top, destino, workers=args.workers, timeout=args.timeout or 30,
        reintentos=args.reintentos, sobrescribir=args.sobrescribir,
    )


def ejecutar(args: argparse.Namespace, registro: dict) -> int:
    """python main.py: procesa los archivos y, al final, respalda la base en la nube.

    - Sin archivo: todos los archivos nuevos de --entrada (los que aún no están en la tabla
      cargas), del más antiguo al más reciente. Cada uno pasa por los pasos 1-3 y se carga a
      la base local. Si no hay nuevos, no hace nada.
    - Con archivo, --ultimo o --sin-bd: solo ese archivo (o el más reciente), sin cargarlo.
    """
    registro["archivos"] = []
    if args.formateo:
        # Reemplazo completo: todos los archivos de la carpeta. Nunca se deja la base vacía.
        archivos, cargar = archivos_de_carpeta(args.entrada), "formateo"
        registro["formateo"] = True
        if not archivos:
            raise FileNotFoundError(f"No hay CSV/Excel en {args.entrada}: no se borró nada de la base")
        log.info("Formateo con %d archivo(s) de %s: %s", len(archivos), args.entrada, ", ".join(a.name for a in archivos))
    elif args.csv or args.ultimo or args.sin_bd:
        archivos, cargar = [_csv_entrada(args)], None
    else:
        archivos, cargar = archivos_pendientes(args.entrada, args.db), "update"
        registro["archivos_nuevos"] = True
        if not archivos:
            log.info("No hay archivos nuevos en %s: todos ya están cargados en %s", args.entrada, args.db)
            return 0
        log.info("%d archivo(s) nuevo(s) en %s: %s", len(archivos), args.entrada, ", ".join(a.name for a in archivos))

    para_formateo = []

    codigo = 0
    for i, archivo in enumerate(archivos, 1):
        ra: dict = {"salidas": {}}
        registro["archivos"].append(ra)
        log.info("Archivo %d de %d: %s", i, len(archivos), archivo)
        try:
            df = leer_tabla(archivo)
            log.info("Leídas %d filas y %d columnas de %s", len(df), df.shape[1], archivo)
            ra["entrada"] = {"archivo": str(archivo), "filas": len(df), "columnas": int(df.shape[1])}
            codigo = max(codigo, procesar_archivo(args, archivo, ra, df.copy()))
            # Se carga a la base al final: si el procesamiento falla, el archivo sigue pendiente
            # y se reintenta en la próxima ejecución.
            if cargar == "update":
                ra["base_datos"] = cargar_bd(args.db, df, archivo, "update", _fecha_respaldo(args, archivo))
            elif cargar == "formateo":
                para_formateo.append((df, archivo, _fecha_respaldo(args, archivo), ra))
        except Exception as exc:
            if not cargar:
                raise  # un solo archivo indicado: la ejecución completa falla
            # Con varios archivos nuevos, uno con problemas no frena a los demás.
            log.exception("Falló el procesamiento de %s", archivo)
            ra["error"] = f"{type(exc).__name__}: {exc}"
            codigo = 2

    if cargar == "formateo":
        fallidos = [Path(ra["entrada"]["archivo"]).name if ra.get("entrada") else "?"
                    for ra in registro["archivos"] if ra.get("error")]
        if fallidos:
            registro["error"] = (f"Formateo cancelado: falló el procesamiento de {', '.join(fallidos)}. "
                                 "No se borró nada; la base quedó como estaba.")
            log.error(registro["error"])
            return 2
        # Todos los archivos se procesaron bien: ahora sí, borrar y cargar en una sola transacción.
        resultados = cargar_varios(args.db, [(d, a, f) for d, a, f, _ in para_formateo], "formateo")
        for (_, _, _, ra), resultado in zip(para_formateo, resultados):
            ra["base_datos"] = resultado

    # Al terminar, respaldo automático de la base local en Neon (si está configurado en .env).
    if not _respaldo_automatico(args, registro):
        codigo = max(codigo, 1)
    return codigo


def procesar_archivo(args: argparse.Namespace, csv: Path, registro: dict, df: pd.DataFrame) -> int:
    """Pasos 1, 2 y 3 sobre un archivo. Devuelve 1 si alguna descarga falló."""
    salidas = registro["salidas"]

    # ---- Paso 1: fechas
    respaldo = _fecha_respaldo(args, csv)
    fechas = convertir_fechas(df[col.columna(df, *col.FECHA)], respaldo)
    df["fecha_cast"] = fechas["fecha_cast"]
    validas = fechas["fecha_cast"].dropna()
    registro["paso1"] = {
        "convertidas": int((fechas["fecha_cast"].notna() & ~fechas["fecha_inferida"]).sum()),
        "con_apostrofo": int(fechas["apostrofo"].sum()),
        "inferidas": int(fechas["fecha_inferida"].sum()),
        "no_reconocidas": int(fechas["no_reconocida"].sum()),
        "sin_fecha": int(fechas["fecha_cast"].isna().sum()),
        "fecha_respaldo": str(respaldo.date()) if respaldo is not None else None,
        "desde": validas.min() if len(validas) else None,
        "hasta": validas.max() if len(validas) else None,
    }

    # ---- Paso 2: costos
    detalle = detalle_cpm(df, metodo=args.cpm_metodo)
    df = calcular_cpm_delta(df, metodo=args.cpm_metodo)
    procesado = args.salida / f"{csv.stem}_procesado.csv"
    df.to_csv(procesado, index=False, encoding="utf-8-sig")
    resumen = args.salida / f"{csv.stem}_resumen_cpm.csv"
    resumen_cpm_mes_marca(detalle).to_csv(resumen, index=False, encoding="utf-8-sig")
    salidas.update({"csv_procesado": str(procesado), "resumen_cpm": str(resumen)})
    log.info("CSV procesado: %s", procesado)

    mas_cara = detalle.loc[detalle["cpm_delta"].idxmin()] if detalle["cpm_delta"].notna().any() else None
    registro["paso2"] = {
        "metodo": args.cpm_metodo,
        "filas_con_cpm": int(detalle["cpm"].notna().sum()),
        "filas_sin_impresiones": int(detalle["cpm"].isna().sum()),
        "meses": sorted(detalle["mes"].dropna().unique().tolist()),
        "marcas": int(detalle["marca"].nunique()),
        "cpm_mediana": _f(detalle["cpm"].median()),
        "cpm_promedio": _f(detalle["cpm"].mean()),
        "cpm_delta_min": _f(detalle["cpm_delta"].min()),
        "cpm_delta_max": _f(detalle["cpm_delta"].max()),
        "fila_mas_cara": None if mas_cara is None else {
            "marca": mas_cara["marca"], "mes": mas_cara["mes"],
            "cpm": _f(mas_cara["cpm"]), "cpm_promedio": _f(mas_cara["cpm_promedio"]),
        },
    }

    # ---- Paso 3: selección y descargas
    top = seleccionar_top_anuncios(df, n=args.top, candidatos=args.candidatos, alcance=args.alcance)
    log.info("%d anuncios seleccionados (%d marcas): los %d con más impresiones de cada marca%s",
             len(top), top["marca"].nunique(), args.top,
             f", entre sus {args.candidatos} URLs más repetidas" if args.candidatos else "")
    candidatas = (
        seleccionar_top_anuncios(df, n=args.candidatos, candidatos=args.candidatos, alcance=args.alcance)
        if args.candidatos else None
    )
    info = {
        "alcance": args.alcance,
        "urls_unicas": int(df[col.columna(df, *col.ADVERTISEMENT)].nunique()),
        "candidatos_por_grupo": args.candidatos,
        "candidatas": None if candidatas is None else len(candidatas),
        "top": args.top,
        "seleccionados": len(top),
        "marcas": int(top["marca"].nunique()),
    }
    registro["paso3"] = info

    manifiesto_csv = args.salida / f"{csv.stem}_top_anuncios.csv"
    salidas["top_anuncios"] = str(manifiesto_csv)
    if args.sin_descargas:
        info["modo"] = "omitido"
        manifiesto = top
    else:
        manifiesto = _descargar(args, top, args.salida / "anuncios", info)
        salidas["carpeta_anuncios"] = str(args.salida / "anuncios")
        info["estados"] = {k: int(v) for k, v in manifiesto["estado"].value_counts().items()}
        info["bytes"] = int(manifiesto["bytes"].sum())
        info["errores"] = manifiesto.loc[manifiesto["estado"] == "error", ["marca", "ranking", "advertisement", "error"]].to_dict("records")
        log.info("Descargas: %s", info["estados"])
        if not args.sin_bd:
            # Un error con la base (p. ej. bloqueada) no invalida las descargas ya hechas.
            try:
                info["bd"] = registrar_descargas(args.db, manifiesto, csv, info)
                salidas["base_datos"] = str(args.db)
            except Exception as exc:
                log.warning("No se pudieron registrar las descargas en %s: %s", args.db, exc)
                info["bd"] = {"db": str(args.db), "error": f"{type(exc).__name__}: {exc}"}
    manifiesto.to_csv(manifiesto_csv, index=False, encoding="utf-8-sig")
    info["principales"] = (
        manifiesto.sort_values("impresiones", ascending=False)
        .head(10)
        .reindex(columns=["marca", "ranking", "advertisement", "repeticiones", "impresiones", "estado"])
        .astype(object).where(lambda d: d.notna(), None)
        .to_dict("records")
    )

    # Código de salida != 0 si alguna descarga falló, para que un scheduler lo detecte.
    return 1 if info.get("estados", {}).get("error", 0) else 0


def _respaldo_automatico(args: argparse.Namespace, registro: dict) -> bool:
    """Respalda la base local en Neon al final de python main.py. Devuelve False si falló."""
    if args.sin_nube:
        return True
    if not args.neon_url:
        log.info("Respaldo en la nube omitido: NEON_DATABASE_URL no está configurado en .env")
        return True
    if not Path(args.db).exists():
        log.info("Respaldo en la nube omitido: no existe la base local %s", args.db)
        return True
    from .nube import ocultar_url, respaldar

    try:
        registro["nube"] = respaldar(args.db, args.neon_url)
        return True
    except Exception as exc:
        # Las descargas ya están hechas: un fallo de la nube se informa, pero no las deshace.
        log.error("Falló el respaldo en la nube: %s", exc)
        registro["nube"] = {"destino": ocultar_url(args.neon_url), "error": f"{type(exc).__name__}: {exc}"}
        return False


def actualizar_bd(args: argparse.Namespace, registro: dict) -> int:
    """--update / --formateo: carga el archivo en la base SQLite y termina."""
    archivo = _csv_entrada(args)
    modo = "formateo" if args.formateo else "update"
    df = leer_tabla(archivo)
    log.info("Leídas %d filas y %d columnas de %s", len(df), df.shape[1], archivo)
    registro["entrada"] = {"archivo": str(archivo), "filas": len(df), "columnas": int(df.shape[1])}
    registro["base_datos"] = cargar_bd(args.db, df, archivo, modo, _fecha_respaldo(args, archivo))
    registro["salidas"]["base_datos"] = str(args.db)
    return 0


def mostrar_resumen(salida: Path) -> int:
    datos = reg.leer(salida)
    texto = reg.formatear(datos) if datos else (
        f"No hay ejecuciones registradas en {salida}. Ejecuta primero: python main.py\n"
    )
    if sys.stdout is not None:
        sys.stdout.write(texto)
    return 0 if datos else 1


def respaldo_nube(args: argparse.Namespace, registro: dict) -> int:
    """--respaldo-nube: copia la base SQLite local a Neon."""
    from .nube import respaldar

    registro["nube"] = respaldar(args.db, args.neon_url)
    return 0


def _salida_utf8() -> None:
    """En Windows, si la salida se redirige a un archivo (o la captura un programador de
    tareas), Python usa cp1252 y fallaría con caracteres como → o ═. Ahí se fuerza UTF-8."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            if flujo is not None and not flujo.isatty() and (flujo.encoding or "").lower() != "utf-8":
                flujo.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _salida_utf8()
    _cargar_env()
    args = _argumentos(argv)
    if args.resume:
        return mostrar_resumen(args.salida)

    args.salida.mkdir(parents=True, exist_ok=True)
    archivo_log = _configurar_logs(args.salida)
    inicio = time.monotonic()
    registro: dict = {"inicio": datetime.now().isoformat(timespec="seconds"), "salidas": {}}
    try:
        if args.respaldo_nube:
            accion = respaldo_nube
        elif args.formateo and not args.csv:
            accion = ejecutar  # formateo completo de la carpeta: pasos 1-3 + base + nube
        elif args.update or args.formateo:
            accion = actualizar_bd
        else:
            accion = ejecutar
        codigo = accion(args, registro)
        registro["estado"] = "ok" if codigo == 0 else "con_errores"
    except Exception as exc:
        log.exception("La ejecución falló")
        registro.update(estado="fallo", error=f"{type(exc).__name__}: {exc}")
        codigo = 2
    finally:
        registro["fin"] = datetime.now().isoformat(timespec="seconds")
        registro["duracion_s"] = round(time.monotonic() - inicio, 2)
        registro["salidas"].update(
            log=str(archivo_log),
            registro=str(args.salida / reg.ARCHIVO_REGISTRO),
            resumen=str(args.salida / reg.ARCHIVO_RESUMEN),
        )
        reg.guardar(registro, args.salida)
        log.info("Listo (%s). Resumen: python main.py --resume", registro.get("estado"))
    return codigo


if __name__ == "__main__":
    sys.exit(main())
