r"""POS PPK Processor - post-process Applanix POS LV data against NOAA CORS.

    app.py                          normal use
    app.py --surveyor --out F:\     started by another app, which controls recording over
                                    stdin/stdout (docs/Comunication4OtherApps.md)
    app.py --cli --pcs ... --out ... [--csv ...] [--rate 10]
                                    processing without the window (same as python -m posproc.cli);
                                    lets the installed POSProcessing.exe process from a script

The installed program is POSProcessing.exe with the same options.
"""
import argparse
import sys


def parse_args(argv):
    ap = argparse.ArgumentParser(description="POS PPK Processor")
    ap.add_argument("--surveyor", action="store_true",
                    help="controlled by another app over stdin/stdout; starts recording at once")
    ap.add_argument("--out", default="", help="with --surveyor: drive or folder to record under (e.g. F:\\)")
    args, _qt_args = ap.parse_known_args(argv)
    if args.surveyor and not args.out:
        ap.error("--surveyor needs --out")
    return args


def run_cli(argv) -> int:
    from posproc.cli import main as cli_main
    from posproc.stdio import attach_std_output

    attach_std_output()               # windowless exe: print to the caller's pipe / redirected file
    return cli_main(argv)


def main() -> int:
    if sys.argv[1:2] == ["--cli"]:
        return run_cli(sys.argv[2:])
    args = parse_args(sys.argv[1:])
    from PySide6.QtWidgets import QApplication

    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("POS PPK Processor")
    win = MainWindow(surveyor_out=args.out if args.surveyor else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
