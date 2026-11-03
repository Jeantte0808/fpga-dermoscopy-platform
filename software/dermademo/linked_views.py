"""Synchronize navigation in original-image coordinates, never processing data."""

from PySide6.QtCore import QObject


class LinkedViews(QObject):
    def __init__(self, view_provider, parent=None):
        super().__init__(parent)
        self.view_provider = view_provider
        self.enabled = False
        self._updating = False

    def register(self, view):
        view.navigation_changed.connect(lambda state, source=view: self.changed(source, state))

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.align()

    def align(self):
        views = [v for v in self.view_provider() if v.has_image]
        if views:
            self.changed(views[0], views[0].navigation_state())

    def changed(self, source, state):
        if not self.enabled or self._updating or not source.has_image:
            return
        self._updating = True
        try:
            for view in self.view_provider():
                if view is not source and view.has_image:
                    view.apply_navigation(state)
        finally:
            self._updating = False
