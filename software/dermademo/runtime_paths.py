"""Qt-independent runtime locations. Importing creates no files or threads."""

import os
from pathlib import Path
import sys


def bundle_root() -> Path:
    return Path(__file__).resolve().parent.parent


def distribution_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else bundle_root()


def data_root() -> Path:
    override = os.environ.get("ALIENHOME_DATA_DIR")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError("ALIENHOME_DATA_DIR must be absolute")
        return path
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) if os.name == "nt" else Path.home() / ".local" / "share"
    return base / "AlienHome" / "DermaImageLab"


def documents_root() -> Path:
    if os.name == "nt":
        import ctypes
        from uuid import UUID
        folder_id = ctypes.create_string_buffer(UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le)
        pointer = ctypes.c_wchar_p()
        try:
            if ctypes.windll.shell32.SHGetKnownFolderPath(folder_id, 0, None, ctypes.byref(pointer)) == 0:
                return Path(pointer.value)
        finally:
            if pointer:
                ctypes.windll.ole32.CoTaskMemFree(pointer)
    return Path.home() / "Documents"


def default_output() -> Path:
    return documents_root() / "AlienHomeDerma" / "outputs" if getattr(sys, "frozen", False) else Path("outputs")
