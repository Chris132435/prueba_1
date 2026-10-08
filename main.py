"""Ejecuta el pipeline de Admetricks.

    python main.py                      # procesa el CSV más reciente de data/raw
    python main.py ruta/al/archivo.csv  # procesa ese archivo
    python main.py --resume             # resumen de la última ejecución

Para ejecutarlo sin terminal en Windows, haz doble clic en main.pyw (o usa pythonw).
"""

import os
import sys
from pathlib import Path

# Las rutas por defecto (data/raw, output) son relativas a la carpeta del proyecto,
# también cuando se lanza con doble clic o desde el Programador de tareas.
os.chdir(Path(__file__).resolve().parent)

from admetricks_pipeline.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
