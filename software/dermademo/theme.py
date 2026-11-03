"""One application theme manager; Fluent aliases and native Chinese fonts."""

import os
from pathlib import Path

from PySide6.QtGui import QFontDatabase

from ._fluent.theme import FluentThemeManager
from ._fluent.tokens import TokenRepository
from .resources import resource_path


class WorkbenchTheme(FluentThemeManager):
    def _build_font(self, theme):
        font = super()._build_font(theme)
        if os.name == "nt":
            families = set(QFontDatabase.families())
            for family in ("Microsoft YaHei UI", "Microsoft YaHei"):
                if family in families:
                    font.setFamily(family)
                    break
        return font


def install_theme(app) -> WorkbenchTheme:
    root = resource_path("theme")
    manager = WorkbenchTheme(app, TokenRepository.from_skill_root(root),
                             prefer_fluent_font=False, apply_body_pixel_size=True)
    manager.apply()
    return manager
