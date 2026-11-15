r"""POS PPK Processor - post-process Applanix POS LV data against NOAA CORS.

    app.py                          normal use
    app.py --surveyor --out F:\     started by another app, which controls recording over
                                    stdin/stdout (docs/Comunication4OtherApps.md)
"""
import argparse
import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def parse_args(argv):
    ap = argparse.ArgumentParser(description="POS PPK Processor")
    ap.add_argument("--surveyor", action="store_true",
                    help="controlled by another app over stdin/stdout; starts recording at once")
    ap.add_argument("--out", default="", help="with --surveyor: drive or folder to record under (e.g. F:\\)")
    args, _qt_args = ap.parse_known_args(argv)
    if args.surveyor and not args.out:
        ap.error("--surveyor needs --out")
    return args


def main() -> int:
    args = parse_args(sys.argv[1:])
    app = QApplication(sys.argv)
    app.setApplicationName("POS PPK Processor")
    win = MainWindow(surveyor_out=args.out if args.surveyor else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
