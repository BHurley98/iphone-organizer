"""Portable dependency setup; no host Python installation required."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / 'packages'
sys.path[:0] = [str(PACKAGES), str(PACKAGES / 'win32'), str(PACKAGES / 'win32/lib')]
DLL_HANDLES = []
if os.name == 'nt':
    for p in [PACKAGES / 'pywin32_system32', ROOT / 'runtime/DLLs']:
        if p.is_dir():
            DLL_HANDLES.append(os.add_dll_directory(str(p)))

