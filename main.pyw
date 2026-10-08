import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)

from admetricks_pipeline.cli import main

sys.exit(main())
