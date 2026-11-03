import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def isolate_user_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ALIENHOME_DATA_DIR", str(tmp_path / "user-data"))
    from PySide6.QtCore import QSettings
    import dermademo.gui as gui
    from dermademo.preferences import Preferences
    monkeypatch.setattr(gui, "Preferences", lambda: Preferences(
        QSettings(str(tmp_path / "isolated.ini"), QSettings.Format.IniFormat)))
    yield
    from dermademo.diagnostic_logging import service
    if service() is not None:
        service().shutdown()
