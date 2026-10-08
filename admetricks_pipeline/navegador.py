"""Paso 3 (descarga): abre cada anuncio en Brave o Edge, uno por uno, y lo descarga.

1. Se detecta el navegador predeterminado del sistema (Windows, macOS o Linux).
2. Si es Brave o Edge se usa ese; si no, el primero instalado entre Brave y Edge.
3. Se abre el navegador (con un perfil temporal, sin tocar el del usuario) y, en orden,
   para cada anuncio: se abre una pestaña con la URL, el propio navegador descarga el
   archivo, se espera a que la descarga termine, se cierra la pestaña y se pasa al
   siguiente. Al final se cierra el navegador.

El control del navegador se hace con Playwright (protocolo DevTools de Chromium, que Brave
y Edge comparten), usando el ejecutable instalado: no descarga navegadores propios.
"""

from __future__ import annotations

import logging
import os
import platform
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .descargas import armar_manifiesto, finalizar_archivo, resultado_inicial, ruta_destino

log = logging.getLogger(__name__)

SOPORTADOS = ("brave", "edge")

_EJECUTABLES = {
    "brave": {
        "Windows": [
            r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
        "Darwin": ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"],
        "Linux": ["brave-browser", "brave", "brave-browser-stable"],
    },
    "edge": {
        "Windows": [
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
            r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
        ],
        "Darwin": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        "Linux": ["microsoft-edge", "microsoft-edge-stable"],
    },
}

# Descarga el documento abierto en la pestaña usando la red del propio navegador.
# Es la misma URL de la pestaña (mismo origen), así que no hay bloqueo CORS.
_JS_DESCARGAR = """async (nombre) => {
    document.querySelectorAll('video, audio').forEach(m => { try { m.pause(); } catch (e) {} });
    const resp = await fetch(location.href, {cache: 'force-cache'});
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = nombre;
    (document.body || document.documentElement).appendChild(a);
    a.click();
}"""


@dataclass
class Navegador:
    nombre: str  # brave, edge o personalizado
    ejecutable: str
    por_defecto: str | None  # navegador predeterminado detectado en el sistema
    motivo: str


def identificar(texto: str | None) -> str | None:
    """Traduce un ProgId / bundle id / .desktop a un nombre corto de navegador."""
    t = (texto or "").strip().lower()
    if not t:
        return None
    for clave, nombre in (
        ("brave", "brave"),
        ("msedge", "edge"),
        ("edgemac", "edge"),
        ("microsoft-edge", "edge"),
        ("chrome", "chrome"),
        ("chromium", "chromium"),
        ("firefox", "firefox"),
        ("safari", "safari"),
        ("opera", "opera"),
    ):
        if clave in t:
            return nombre
    return t


def navegador_por_defecto() -> str | None:
    """Nombre corto del navegador predeterminado del sistema, o None si no se pudo detectar."""
    sistema = platform.system()
    try:
        if sistema == "Windows":
            import winreg

            for esquema in ("https", "http"):
                clave = (
                    r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
                    rf"\{esquema}\UserChoice"
                )
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as k:
                        return identificar(winreg.QueryValueEx(k, "ProgId")[0])
                except OSError:
                    continue
        elif sistema == "Darwin":
            plist = (
                Path.home()
                / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
            )
            with open(plist, "rb") as fh:
                for h in plistlib.load(fh).get("LSHandlers", []):
                    if h.get("LSHandlerURLScheme") in ("https", "http"):
                        return identificar(h.get("LSHandlerRoleAll"))
        else:
            r = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
            )
            return identificar(r.stdout)
    except Exception as exc:
        log.debug("No se pudo detectar el navegador predeterminado: %s", exc)
    return None


def buscar_ejecutable(nombre: str) -> str | None:
    """Ruta del ejecutable de ``nombre`` (brave/edge) instalado en este equipo."""
    for candidato in _EJECUTABLES.get(nombre, {}).get(platform.system(), []):
        ruta = os.path.expandvars(candidato)
        if os.path.isabs(ruta):
            if os.path.isfile(ruta):
                return ruta
        elif encontrado := shutil.which(ruta):
            return encontrado
    return None


def elegir_navegador(preferencia: str = "auto", ruta: str | None = None) -> Navegador | None:
    """Elige el navegador con el que se harán las descargas.

    - ``ruta``: usa ese ejecutable (cualquier navegador basado en Chromium).
    - ``preferencia`` = brave / edge: usa ese, si está instalado.
    - ``auto``: el predeterminado del sistema si es Brave o Edge; si no, el primero
      instalado entre Brave y Edge.
    """
    por_defecto = navegador_por_defecto()
    if ruta:
        return Navegador("personalizado", ruta, por_defecto, "ruta indicada con --navegador-ruta")

    if preferencia in SOPORTADOS:
        orden = [preferencia]
    else:
        orden = ([por_defecto] if por_defecto in SOPORTADOS else []) + [
            n for n in SOPORTADOS if n != por_defecto
        ]

    for nombre in orden:
        ejecutable = buscar_ejecutable(nombre)
        if ejecutable:
            if preferencia in SOPORTADOS:
                motivo = f"pedido con --navegador {preferencia}"
            elif nombre == por_defecto:
                motivo = "es el navegador predeterminado del sistema"
            else:
                motivo = (
                    f"el predeterminado ({por_defecto or 'no detectado'}) no es Brave ni Edge; "
                    f"se usa {nombre}, que está instalado"
                )
            return Navegador(nombre, ejecutable, por_defecto, motivo)

    log.warning(
        "No se encontró Brave ni Edge instalado (predeterminado: %s)", por_defecto or "no detectado"
    )
    return None


def _descargar_en_pestana(contexto, url: str, archivo: Path, timeout_ms: float) -> None:
    """Abre ``url`` en una pestaña nueva, la descarga con el navegador y espera a que termine."""
    from playwright.sync_api import Error as PlaywrightError

    pagina = contexto.new_page()
    temporal = archivo.with_name(archivo.name + ".part")
    try:
        with pagina.expect_download(timeout=timeout_ms) as info:
            try:
                resp = pagina.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PlaywrightError as exc:
                # El servidor mandó el archivo como adjunto: la navegación ya es la descarga.
                if "Download is starting" not in str(exc):
                    raise
            else:
                if resp is not None and not resp.ok:
                    raise RuntimeError(f"HTTP {resp.status} {resp.status_text}")
                pagina.evaluate(_JS_DESCARGAR, archivo.name)
        descarga = info.value
        descarga.save_as(temporal)  # bloquea hasta que la descarga termina
        if descarga.failure():
            raise RuntimeError(f"descarga fallida: {descarga.failure()}")
    except Exception:
        temporal.unlink(missing_ok=True)
        raise
    finally:
        pagina.close()


def descargar_con_navegador(
    top: pd.DataFrame,
    destino: str | Path,
    navegador: Navegador,
    timeout: float = 60,
    reintentos: int = 2,
    sobrescribir: bool = False,
    oculto: bool = False,
) -> pd.DataFrame:
    """Descarga, en orden y de a uno, el archivo de cada anuncio de ``top`` usando el
    navegador indicado. Devuelve el mismo manifiesto que la descarga HTTP."""
    from playwright.sync_api import sync_playwright

    destino = Path(destino)
    resultados: list[dict] = []
    pendientes = []
    for fila in top.itertuples(index=False):
        archivo = ruta_destino(destino, fila.marca, fila.ranking, fila.advertisement)
        resultados.append(resultado_inicial(fila.advertisement, archivo, sobrescribir))
        pendientes.append((fila, archivo))

    if not any(r["estado"] == "" for r in resultados):
        return armar_manifiesto(top, resultados)

    with sync_playwright() as pw:
        log.info("Abriendo %s (%s)", navegador.nombre, navegador.ejecutable)
        browser = pw.chromium.launch(executable_path=navegador.ejecutable, headless=oculto)
        try:
            contexto = browser.new_context(accept_downloads=True)
            total = len(pendientes)
            for i, ((fila, archivo), resultado) in enumerate(zip(pendientes, resultados), 1):
                if resultado["estado"]:
                    continue
                archivo.parent.mkdir(parents=True, exist_ok=True)
                log.info("[%d/%d] %s #%d → %s", i, total, fila.marca, fila.ranking, fila.advertisement)
                for intento in range(1, reintentos + 2):
                    try:
                        _descargar_en_pestana(contexto, fila.advertisement, archivo, timeout * 1000)
                        finalizar_archivo(archivo.with_name(archivo.name + ".part"), archivo, resultado)
                        break
                    except Exception as exc:
                        mensaje = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
                        resultado.update(estado="error", error=f"{type(exc).__name__}: {mensaje}")
                        if intento <= reintentos:
                            log.warning("Reintento %d de %s: %s", intento, fila.advertisement, mensaje)
        finally:
            browser.close()
    return armar_manifiesto(top, resultados)
