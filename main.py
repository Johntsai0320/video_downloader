"""Desktop application entry point for Universal Video Hub."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from src.desktop.styles import MODERN_STYLE
from src.desktop.window import VideoDownloaderGUI


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(MODERN_STYLE)
    window = VideoDownloaderGUI()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
