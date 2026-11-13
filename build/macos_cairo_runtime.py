"""Tell Cairo's CFFI loader to use the library in the frozen macOS app."""

import ctypes.util
import sys
from pathlib import Path


_bundled_cairo = Path(sys._MEIPASS) / "libcairo.2.dylib"
_system_find_library = ctypes.util.find_library


def _find_library(name):
    if name in {"cairo-2", "cairo", "libcairo-2"} and _bundled_cairo.is_file():
        return str(_bundled_cairo)
    return _system_find_library(name)


ctypes.util.find_library = _find_library
