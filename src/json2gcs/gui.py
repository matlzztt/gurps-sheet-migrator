"""A small tkinter front end — the "a few clicks" goal from docs/06.

This deliberately does **not** reimplement anything.  It assembles the same
argument list the command line takes and runs :func:`json2gcs.cli.main`,
capturing what it prints.  So every rule about what may be written, what is
refused, and what needs review is enforced in exactly one place, and the window
shows the same report the terminal does.

Tkinter is in the standard library, so this adds no dependency and nothing to
bundle beyond what PyInstaller already collects.
"""

from __future__ import annotations

import io
import queue
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from . import __version__, cli, foundry

PAD = {"padx": 8, "pady": 4}


@dataclass
class Options:
    """Everything the window collects. Plain data, so it can be tested."""

    export: str = ""
    base: str = ""
    output: str = ""
    gcs: str = ""
    synthesize: bool = False
    rename: bool = False
    include_lossy: bool = False
    drop_deletions: bool = False
    refresh_calc: bool = False
    verify: bool = False
    dry_run: bool = False


def build_argv(options: Options) -> list[str]:
    """Turn the form into the command line it stands for.

    The window never decides anything the command line would not: this is the
    only translation, and everything downstream of it is shared.
    """
    argv = ["convert", options.export]
    if options.synthesize:
        argv.append("--synthesize")
    elif options.base:
        argv += ["--base", options.base]
    if options.output:
        argv += ["-o", options.output]
    if options.gcs:
        argv += ["--gcs", options.gcs]
    if options.drop_deletions:
        argv += ["--deletions", "drop"]
    for flag, on in (
        ("--include-lossy", options.include_lossy),
        ("--rename", options.rename),
        ("--refresh-calc", options.refresh_calc),
        ("--verify", options.verify),
        ("--dry-run", options.dry_run),
    ):
        if on:
            argv.append(flag)
    return argv


@dataclass
class Suggestion:
    """What choosing an export implies about the rest of the form."""

    base: str = ""
    output: str = ""
    synthesize: bool = False
    status: str = ""


def suggest(export: Path) -> Suggestion:
    """Work out the base sheet and output path, and say what was worked out.

    Getting this right is most of what makes the tool a few clicks: an export
    dropped next to its sheet should need nothing else chosen.
    """
    try:
        actor = foundry.load(export)
    except (OSError, ValueError) as err:
        return Suggestion(status=f"Could not read that export: {err}")

    found = cli._find_base(actor, export)
    if found:
        return Suggestion(
            base=str(found),
            output=str(found.with_suffix(".merged.gcs")),
            synthesize=False,
            status=f"{actor.name} — found the base sheet beside the export.",
        )
    hint = f" (looked for {actor.import_name})" if actor.import_name else ""
    return Suggestion(
        output=str(export.with_suffix(".gcs")),
        synthesize=True,
        status=f"{actor.name} — no base sheet found{hint}; will build a new one.",
    )


class App:
    """The whole window. One export in, one sheet out."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"GURPS Sheet Migrator {__version__}")
        root.minsize(720, 560)

        self.export = tk.StringVar()
        self.base = tk.StringVar()
        self.output = tk.StringVar()
        self.gcs = tk.StringVar(value=str(cli.find_gcs() or ""))
        self.mode = tk.StringVar(value="merge")
        self.rename = tk.BooleanVar(value=False)
        self.include_lossy = tk.BooleanVar(value=False)
        self.drop_deletions = tk.BooleanVar(value=False)
        self.refresh_calc = tk.BooleanVar(value=bool(self.gcs.get()))
        self.verify = tk.BooleanVar(value=bool(self.gcs.get()))
        self.status = tk.StringVar(value="Choose a Foundry export to begin.")

        self._results: queue.Queue = queue.Queue()
        self._build()

    # -- layout ----------------------------------------------------------

    def _build(self) -> None:
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        row = 0

        row = self._file_row(
            frame, row, "Foundry export", self.export, self._pick_export,
            "The .json exported from Foundry",
        )
        row = self._file_row(
            frame, row, "Base GCS sheet", self.base, self._pick_base,
            "The original .gcs — found automatically when it sits beside the export",
        )

        modes = ttk.Frame(frame)
        modes.grid(row=row, column=1, sticky="w", **PAD)
        ttk.Radiobutton(
            modes, text="Merge into the base sheet", value="merge",
            variable=self.mode, command=self._mode_changed,
        ).pack(side="left")
        ttk.Radiobutton(
            modes, text="Build a new sheet from the export alone", value="synthesize",
            variable=self.mode, command=self._mode_changed,
        ).pack(side="left", padx=(12, 0))
        row += 1

        row = self._file_row(
            frame, row, "Write to", self.output, self._pick_output,
            "Never the base sheet itself",
        )
        row = self._file_row(
            frame, row, "GCS application", self.gcs, self._pick_gcs,
            "Optional — enables verification and correct derived values",
        )

        options = ttk.LabelFrame(frame, text="Options")
        options.grid(row=row, column=0, columnspan=3, sticky="ew", **PAD)
        for text, var, hint in (
            ("Let GCS compute derived values", self.refresh_calc,
             "runs the result back through GCS"),
            ("Have GCS verify the result", self.verify, ""),
            ("Also write lossy changes", self.include_lossy,
             "notes, and values GCS derives"),
            ("Remove rows missing from the export", self.drop_deletions,
             "they are ambiguous; off by default"),
            ("Rename the sheet to match the Foundry actor", self.rename, ""),
        ):
            label = f"{text}  ({hint})" if hint else text
            ttk.Checkbutton(options, text=label, variable=var).pack(anchor="w", padx=8)
        row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=3, sticky="ew", **PAD)
        self.preview_button = ttk.Button(
            buttons, text="Preview", command=lambda: self._run(dry_run=True)
        )
        self.preview_button.pack(side="left")
        self.convert_button = ttk.Button(
            buttons, text="Convert", command=lambda: self._run(dry_run=False)
        )
        self.convert_button.pack(side="left", padx=8)
        ttk.Label(buttons, textvariable=self.status).pack(side="left", padx=12)
        row += 1

        frame.rowconfigure(row, weight=1)
        self.out = tk.Text(frame, wrap="none", height=18, font=("Consolas", 9))
        self.out.grid(row=row, column=0, columnspan=3, sticky="nsew", **PAD)
        scroll = ttk.Scrollbar(frame, command=self.out.yview)
        scroll.grid(row=row, column=3, sticky="ns")
        self.out.configure(yscrollcommand=scroll.set, state="disabled")

        self._mode_changed()

    def _file_row(self, parent, row: int, label: str, var, command, hint: str) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", **PAD)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", **PAD)
        ttk.Button(parent, text="Browse…", command=command).grid(
            row=row, column=2, sticky="e", **PAD
        )
        if hint:
            ttk.Label(parent, text=hint, foreground="#666").grid(
                row=row + 1, column=1, sticky="w", padx=8
            )
            return row + 2
        return row + 1

    # -- pickers ---------------------------------------------------------

    def _pick_export(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Foundry actor export",
            filetypes=[("Foundry export", "*.json"), ("All files", "*.*")],
        )
        if not chosen:
            return
        self.export.set(chosen)
        self._suggest_from_export(Path(chosen))

    def _pick_base(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Original GCS sheet",
            filetypes=[("GCS sheet", "*.gcs"), ("All files", "*.*")],
        )
        if chosen:
            self.base.set(chosen)
            self.mode.set("merge")
            self._mode_changed()

    def _pick_output(self) -> None:
        chosen = filedialog.asksaveasfilename(
            title="Write the sheet to",
            defaultextension=".gcs",
            filetypes=[("GCS sheet", "*.gcs")],
        )
        if chosen:
            self.output.set(chosen)

    def _pick_gcs(self) -> None:
        chosen = filedialog.askopenfilename(title="The GCS application")
        if chosen:
            self.gcs.set(chosen)

    def _suggest_from_export(self, export: Path) -> None:
        """Fill in what can be worked out, and say what was worked out."""
        found = suggest(export)
        self.base.set(found.base)
        self.output.set(found.output)
        self.mode.set("synthesize" if found.synthesize else "merge")
        self.status.set(found.status)
        self._mode_changed()

    def _mode_changed(self) -> None:
        synthesizing = self.mode.get() == "synthesize"
        base = self.base.get()
        if synthesizing and base:
            self.base.set("")
        export = self.export.get()
        if synthesizing and export and not self.output.get():
            self.output.set(str(Path(export).with_suffix(".gcs")))

    # -- running ---------------------------------------------------------

    def _options(self, *, dry_run: bool) -> Options:
        return Options(
            export=self.export.get(),
            base=self.base.get(),
            output=self.output.get(),
            gcs=self.gcs.get(),
            synthesize=self.mode.get() == "synthesize",
            rename=self.rename.get(),
            include_lossy=self.include_lossy.get(),
            drop_deletions=self.drop_deletions.get(),
            refresh_calc=self.refresh_calc.get(),
            verify=self.verify.get(),
            dry_run=dry_run,
        )

    def _run(self, *, dry_run: bool) -> None:
        if not self.export.get():
            self.status.set("Choose a Foundry export first.")
            return
        self._set_busy(True)
        self.status.set("Working…")
        argv = build_argv(self._options(dry_run=dry_run))
        threading.Thread(target=self._work, args=(argv,), daemon=True).start()
        self.root.after(100, self._poll)

    def _work(self, argv: list[str]) -> None:
        """Run the CLI with its output captured. Never raises into the thread."""
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer), redirect_stderr(buffer):
                code = cli.main(argv)
        except SystemExit as exit_:  # argparse on a bad argument
            code = int(exit_.code or 0)
        except Exception:  # pragma: no cover - a bug, but it must reach the user
            buffer.write("\n" + traceback.format_exc())
            code = 3
        self._results.put((code, buffer.getvalue()))

    def _poll(self) -> None:
        try:
            code, text = self._results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll)
            return
        self._show(text or "(no output)")
        self._set_busy(False)
        self.status.set(
            {0: "Done.", 2: "Refused — see below."}.get(code, f"Finished with {code}.")
        )

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.convert_button.configure(state=state)
        self.preview_button.configure(state=state)

    def _show(self, text: str) -> None:
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text)
        self.out.configure(state="disabled")


def main() -> int:
    """Open the window. Returns once it is closed."""
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
