import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
VENV = HERE / "build_venv"
NAME = "GameLarper"


def run(*cmd) -> None:
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call([str(c) for c in cmd], cwd=HERE)


def installed(py: Path, module: str) -> bool:
    """True if `module` can be imported by the build venv's Python."""
    code = f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"
    return subprocess.call([str(py), "-c", code], cwd=HERE) == 0


def main() -> None:
    if not (HERE / f"{NAME}.py").exists():
        sys.exit(f"{NAME}.py must be in the repo root, one folder above Build/")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.exit("This Python has no tkinter. Install Python from python.org (tcl/tk is included by default).")

    if not VENV.exists():
        run(sys.executable, "-m", "venv", VENV)
    py = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    run(py, "-m", "pip", "install", "--upgrade", "pip")
    run(py, "-m", "pip", "install", "--upgrade",
        "pyinstaller", "discord.py-self", "curl_cffi", "tzdata")

    cmd = [str(py), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole", "--name", NAME]

    cmd += ["--collect-all", "discord"]

    for pkg in ("curl_cffi", "discord_protos"):
        if installed(py, pkg):
            cmd += ["--collect-all", pkg]
        else:
            print(f"NOTE: '{pkg}' is not installed in the build venv, so it is not bundled", flush=True)

    if installed(py, "tzdata"):
        cmd += ["--collect-data", "tzdata"]
    else:
        print("NOTE: 'tzdata' is not installed in the build venv, so it is not bundled", flush=True)

    icons = HERE / "icons"
    if icons.is_dir():
        cmd += ["--add-data", f"{icons}{os.pathsep}icons"]
        ico = icons / "app.ico"
        if ico.exists():
            cmd += ["--icon", str(ico)]

    cmd.append(f"{NAME}.py")
    run(*cmd)

    exe = HERE / "dist" / (NAME + (".exe" if os.name == "nt" else ""))
    if not exe.exists():
        sys.exit(f"Build finished but {exe} was not created")
    print(f"\nDone: {exe}  ({exe.stat().st_size / 1e6:.0f} MB)")
    shutil.rmtree(HERE / "build", ignore_errors=True)


if __name__ == "__main__":
    main()
