"""Ejecución sin terminal (Windows): doble clic, o `pythonw main.pyw`.

Hace el mismo trabajo que main.py, sin abrir ninguna ventana de consola. El navegador se
abre solo mientras descarga y se cierra al terminar. El detalle queda en output/logs y en
`python main.py --resume`.
"""

import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)

from admetricks_pipeline.cli import main  # noqa: E402

sys.exit(main())
