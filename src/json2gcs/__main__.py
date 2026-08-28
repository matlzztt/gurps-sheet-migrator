"""Entry point for ``python -m json2gcs`` and for the packaged executable.

With no arguments it opens the window; with arguments it is the command line.

The import below is absolute rather than relative on purpose: PyInstaller runs
this file as a top-level script with no package context, so ``from .cli`` works
under ``python -m`` and fails in the built executable.
"""

from __future__ import annotations

import multiprocessing

from json2gcs.cli import main

if __name__ == "__main__":
    # Harmless when not frozen, and required when it is: without it a bundled
    # executable re-runs itself instead of starting a child process.
    multiprocessing.freeze_support()
    raise SystemExit(main())
