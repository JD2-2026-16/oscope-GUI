import sys

from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> None:
    # Create one global Qt application object for event loop + window management.
    qt_app = QApplication(sys.argv)
    # Build the main oscilloscope window (controls + waveform canvas).
    window = MainWindow()
    window.show()
    # Hand control to Qt until window closes; return process exit code to shell.
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
