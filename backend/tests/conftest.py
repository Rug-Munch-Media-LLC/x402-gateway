"""Bind ``app`` to this checkout so tests never pick up a host application."""

from __future__ import annotations

import sys
import types
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
BACKEND_DIR = str(APP_DIR.parent)
TESTS_DIR = str(Path(__file__).resolve().parent)

for directory in (TESTS_DIR, BACKEND_DIR):
    if directory not in sys.path:
        sys.path.insert(0, directory)

if "app" not in sys.modules:
    package = types.ModuleType("app")
    package.__path__ = [str(APP_DIR)]
    sys.modules["app"] = package
