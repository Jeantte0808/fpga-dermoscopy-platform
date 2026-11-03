"""GUI entry point: initialize diagnostics before importing Qt and resources."""

import argparse
from pathlib import Path

from . import __version__
from .diagnostic_logging import entry_session, record_event


@entry_session("gui")
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="皮损影像工作台")
    parser.add_argument("image", nargs="?", type=Path, help="可选 PNG/JPEG 图片")
    parser.add_argument("--sample", action="store_true", help="载入明确标注的合成样例")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--smoke-output", type=Path, help="显式自动验收：新建目录，执行合成图流程并退出；不连接板卡")
    args = parser.parse_args(argv)
    from PySide6.QtCore import QTimer
    if args.smoke_output:
        from PySide6.QtCore import QSettings
        args.smoke_output = args.smoke_output.resolve()
        args.smoke_output.mkdir(parents=True, exist_ok=False)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(args.smoke_output / "settings"))
    from .gui import MainWindow, create_application
    app = create_application()
    window = MainWindow(delivery_mode=True)
    window.show()
    record_event("gui_ready")
    if args.smoke_output:
        from .release_smoke import SmokeRun
        window._smoke_run = SmokeRun(app, window, args.smoke_output)
    elif args.image:
        def open_image():
            window.open_formal_device()
            window.formal_dialog.prepare_selected_image(args.image)
        QTimer.singleShot(0, open_image)
    elif args.sample:
        QTimer.singleShot(0, lambda: window.open_maintenance_tool('sample'))
    return app.exec()
