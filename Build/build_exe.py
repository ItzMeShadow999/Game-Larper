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
    run(py, "-m", "pip", "install", "--upgrade", "pip", "pyinstaller", "discord.py-self")

    cmd = [py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole", "--name", NAME,
           "--collect-all", "discord", "--collect-all", "curl_cffi", "--collect-all", "discord_protos",
           "--collect-data", "tzdata"]
    icons = HERE / "icons"
    if icons.is_dir():
        cmd += ["--add-data", f"{icons}{os.pathsep}icons"]
        ico = icons / "app.ico"
        if ico.exists():
            cmd += ["--icon", str(ico)]
    cmd.append(f"{NAME}.py")
    run(*cmd)

    exe = HERE / "dist" / (NAME + (".exe" if os.name == "nt" else ""))
    print(f"\nDone: {exe}  ({exe.stat().st_size / 1e6:.0f} MB)")
    shutil.rmtree(HERE / "build", ignore_errors=True)


if __name__ == "__main__":
    main()
