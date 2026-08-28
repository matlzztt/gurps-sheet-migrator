# PyInstaller build. Produces a single self-contained executable.
#
#     python -m PyInstaller json2gcs.spec
#
# Run it with no arguments and it opens the window; run it with arguments and
# it behaves exactly like the `json2gcs` command.
#
# `data/default.gcs` is **not optional**: it is GCS's own default sheet and
# `convert --synthesize` cannot run without it. It is the one thing here that
# a plain `--onefile` invocation would silently leave out, which is most of
# the reason this spec file exists rather than a command line.

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("json2gcs", includes=["data/*.gcs"])

analysis = Analysis(
    ["src/json2gcs/__main__.py"],
    pathex=["src"],
    datas=datas,
    hiddenimports=["json2gcs.gui"],  # reached only through cmd_gui's late import
    excludes=[
        # Nothing here is used, and each drags in a lot.
        "numpy", "pandas", "matplotlib", "PIL", "pytest", "setuptools",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="json2gcs",
    console=False,  # a GUI app; the CLI still works when run from a shell
    upx=False,
    strip=False,
    onefile=True,
)
