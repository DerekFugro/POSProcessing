"""Runs the pipeline on a background thread so the window stays responsive."""
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from posproc.pipeline import Cancelled, Pipeline


class PipelineWorker(QObject):
    log = Signal(str)
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, settings, pcs_files, csv_files, out_dir, rate_hz):
        super().__init__()
        self.settings, self.pcs_files, self.csv_files = settings, pcs_files, csv_files
        self.out_dir, self.rate_hz = out_dir, rate_hz
        self._cancel = False

    def cancel(self):
        self._cancel = True

    @Slot()
    def run(self):
        try:
            pipe = Pipeline(self.settings, log=self.log.emit,
                            progress=lambda f, m: self.progress.emit(float(f), m),
                            cancelled=lambda: self._cancel)
            result = pipe.run(self.pcs_files, self.csv_files, self.out_dir, self.rate_hz)
            self.finished.emit(result)
        except Cancelled:
            self.failed.emit("Cancelled.")
        except Exception as exc:
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc))
