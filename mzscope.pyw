"""Arranca MicroZohm Scope sin ventana de consola (doble clic; Windows lo abre con pythonw.exe).
Los errores se escriben en outputs/mzscope.log."""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
runpy.run_module("mzscope", run_name="__main__")
