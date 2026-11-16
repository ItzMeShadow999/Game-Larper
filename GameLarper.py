#!/usr/bin/env python3
"""
Game Larper

Pick games from Discord's public detectable games catalog, choose how long
each one "runs", and the app starts a harmless stand-in process named like the
game's real executable (for example hades.exe) so Discord's own game detection
picks it up locally. Nothing is sent to Discord by this app. No token, no
cookie, no telemetry.

Run:    python GameLarper.py
Build:  pip install pyinstaller
        pyinstaller --onefile --noconsole GameLarper.py

Windows and Linux. Python 3.9+. Only the standard library (tkinter included).
"""
from __future__ import annotations

import atexit
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from tkinter import font as tkfont, ttk
import tkinter as tk

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
CACHE_PATH = APP_DIR / "games.json"


def _work_base() -> str | None:
    """Folder where stand-in exes are created: the user's Downloads folder."""
    dl = Path.home() / "Downloads"
    try:
        dl.mkdir(parents=True, exist_ok=True)
        return str(dl)
    except Exception:
        return None

CATALOG_URLS = (
    "https://cdn.discordapp.com/detectables/games.json",
    "https://discord.com/api/v9/applications/detectable",
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

IS_WINDOWS = os.name == "nt"
PLATFORM_KEY = "win32" if IS_WINDOWS else "linux"
DEFAULT_MINUTES = "1"
GAP_SECONDS = 3.0
MAX_QUEUE_RANDOM = 500
GRAPH_W = 420
SLIDE_MS = 450
SLIDE_HOLD_MS = 1200
SPIN_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


@dataclass
class Game:
    name: str
    exe: str



_NAME_RE = re.compile(rb'"name":\s*"([^"]+)"')


def _download_catalog(progress=None) -> list:
    """Download the catalog. progress(done_bytes, total_bytes) is called per chunk (total 0 = unknown)."""
    last_err: Exception | None = None
    for url in CATALOG_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                buf = bytearray()
                while True:
                    chunk = resp.read(16384)
                    if not chunk:
                        break
                    buf += chunk
                    if progress:
                        m = _NAME_RE.findall(bytes(buf[-4096:]))
                        progress(len(buf), total, m[-1].decode("utf-8", "replace") if m else "")
                return json.loads(buf.decode("utf-8"))
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Could not download the games catalog: {last_err}")


def _clean_exe_name(raw: str) -> str | None:
    name = raw.strip()
    if name.startswith(">"):
        name = name[1:]
    name = name.replace("\\", "/").lstrip("/")
    parts = [p for p in name.split("/") if p]
    if not parts or any(p in (".", "..") or ":" in p for p in parts):
        return None
    return "/".join(parts)


def parse_catalog(raw: list) -> list[Game]:
    games: list[Game] = []
    seen: set[str] = set()
    for entry in raw:
        name = entry.get("name")
        execs = entry.get("executables") or []
        if not name or not execs:
            continue
        chosen = None
        for ex in execs:
            if ex.get("os") != PLATFORM_KEY or ex.get("is_launcher"):
                continue
            cleaned = _clean_exe_name(ex.get("name", ""))
            if cleaned:
                chosen = cleaned
                break
        if not chosen:
            continue
        key = f"{name}|{chosen}".lower()
        if key in seen:
            continue
        seen.add(key)
        games.append(Game(name=name, exe=chosen))
    games.sort(key=lambda g: g.name.lower())
    return games


def load_catalog(force: bool = False, progress=None) -> list[Game]:
    raw = None
    if CACHE_PATH.exists() and not force:
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = None
    if raw is None:
        raw = _download_catalog(progress)
        try:
            CACHE_PATH.write_text(json.dumps(raw), encoding="utf-8")
        except Exception:
            pass
    return parse_catalog(raw)


def folder_variant(game: Game) -> str | None:
    """Path ending of the form 'Game Folder/exe', used by Discord for folder-name matching."""
    folder = "".join(c for c in game.name if c not in '<>:"/\\|?*').strip().rstrip(".")
    base = game.exe.split("/")[-1]
    if not folder or not base:
        return None
    cand = f"{folder}/{base}"
    return cand if cand.lower() != game.exe.lower() else None



OWN_STUB_NAME = "larper_window_stub_v4.exe"


def _find_icons_dir() -> Path:
    """Find the icons folder: next to the app, inside a PyInstaller bundle, next to the script, or cwd."""
    cands = [APP_DIR / "icons"]
    mp = getattr(sys, "_MEIPASS", None)
    if mp:
        cands.append(Path(mp) / "icons")
    cands += [Path(__file__).resolve().parent / "icons", Path.cwd() / "icons"]
    for c in cands:
        if c.is_dir():
            return c
    return cands[0]


ICONS_DIR = _find_icons_dir()
STUB_SOURCE = r'''using System;
using System.Drawing;
using System.Windows.Forms;
static class P {
  [STAThread] static void Main(string[] a) {
    Application.EnableVisualStyles();
    Form f = new Form();
    f.Text = System.IO.Path.GetFileNameWithoutExtension(Application.ExecutablePath);
    try { f.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
    f.ClientSize = new Size(320, 90);
    f.StartPosition = FormStartPosition.CenterScreen;
    Label l = new Label(); l.Text = "Running"; l.Dock = DockStyle.Fill;
    l.TextAlign = ContentAlignment.MiddleCenter; f.Controls.Add(l);
    if (a.Length > 0 && a[0] == "min") f.WindowState = FormWindowState.Minimized;
    Application.Run(f);
  }
}
'''
MINIMIZE_WINDOWS = False
CUSTOM_STUB = ""


def _find_csc() -> str | None:
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    for fw in ("Framework64", "Framework"):
        path = os.path.join(windir, "Microsoft.NET", fw, "v4.0.30319", "csc.exe")
        if os.path.exists(path):
            return path
    return None


def ensure_own_stub() -> Path | None:
    """Compile a tiny windowless exe once with the .NET compiler built into Windows."""
    if not IS_WINDOWS:
        return None
    out = APP_DIR / OWN_STUB_NAME
    if out.exists() and out.stat().st_size > 0:
        return out
    csc = _find_csc()
    if not csc:
        return None
    src = APP_DIR / "larper_stub.cs"
    try:
        src.write_text(STUB_SOURCE, encoding="utf-8")
        base = [csc, "/nologo", "/optimize+", "/target:winexe",
                "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", f"/out:{out}"]
        ico = ICONS_DIR / "stub.ico"
        attempts = ([base + [f"/win32icon:{ico}", str(src)]] if ico.exists() else []) + [base + [str(src)]]
        res = None
        for cmd in attempts:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if res.returncode == 0 and out.exists():
                break
        if res.returncode != 0 or not out.exists():
            return None
        return out
    except Exception:
        return None
    finally:
        for f in (src,):
            try:
                f.unlink()
            except Exception:
                pass


def _stub_candidates() -> list[tuple[str, list[str], bool]]:
    """Return (binary to copy, args, needs_stdin_pipe) in order of preference."""
    out: list[tuple[str, list[str], bool]] = []
    if CUSTOM_STUB and os.path.exists(CUSTOM_STUB):
        out.append((CUSTOM_STUB, [], False))
    own = ensure_own_stub()
    if own:
        out.append((str(own), ["min"] if MINIMIZE_WINDOWS else [], False))
    if IS_WINDOWS:
        sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        for exe, args, pipe in (
            ("ping.exe", ["-t", "127.0.0.1"], False),
            ("findstr.exe", ["x"], True),
            ("sort.exe", [], True),
            ("find.exe", ["x"], True),
        ):
            path = os.path.join(sys32, exe)
            if os.path.exists(path):
                out.append((path, args, pipe))
    else:
        sleep = shutil.which("sleep")
        if sleep:
            out.append((sleep, ["infinity"], False))
    if not FROZEN:
        out.append((sys.executable, ["-c", "import time; time.sleep(10**9)"], False))
    return out


class FakeGame:
    def __init__(self, game: Game) -> None:
        self.game = game
        self.root: Path | None = None
        self.proc: subprocess.Popen | None = None
        self.path = ""
        self.stub = ""

    def start(self) -> None:
        errors: list[str] = []
        for stub, args, pipe in _stub_candidates():
            self.root = Path(tempfile.mkdtemp(prefix="larp_", dir=_work_base()))
            target = self.root / self.game.exe
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stub, target)
                if os.path.basename(stub) == OWN_STUB_NAME:
                    with open(target, "ab") as fh:
                        fh.write(os.urandom(random.randint(256, 4096)))
                if not IS_WINDOWS:
                    target.chmod(0o755)
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
                self.proc = subprocess.Popen(
                    [str(target), *args],
                    cwd=str(target.parent),
                    stdin=subprocess.PIPE if pipe else subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                time.sleep(1.0)
                if self.proc.poll() is None:
                    self.path = str(target)
                    self.stub = os.path.basename(stub)
                    return
                errors.append(f"{os.path.basename(stub)} exited with code {self.proc.returncode}")
            except Exception as exc:
                errors.append(f"{os.path.basename(stub)}: {exc}")
            self.stop()
        raise RuntimeError("; ".join(errors) or "no stand-in binary available")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc is not None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=3)
            except Exception:
                pass
            self.proc = None
        if self.root is not None:
            for _ in range(10):
                try:
                    shutil.rmtree(self.root)
                    break
                except Exception:
                    time.sleep(0.3)
            else:
                shutil.rmtree(self.root, ignore_errors=True)
            self.root = None


ACTIVE: list[FakeGame] = []
ACTIVE_LOCK = threading.Lock()


def _cleanup_all() -> None:
    with ACTIVE_LOCK:
        items = list(ACTIVE)
        ACTIVE.clear()
    for item in items:
        item.stop()


atexit.register(_cleanup_all)



def _rrect(px, py, x0, y0, x1, y1, r) -> bool:
    cx = min(max(px, x0 + r), x1 - r)
    cy = min(max(py, y0 + r), y1 - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def _circ(px, py, cx, cy, r) -> bool:
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def icon_color(px: float, py: float) -> str | None:
    """Colour of the app icon at a point in a 64x64 space, or None for transparent."""
    if not _rrect(px, py, 0, 0, 64, 64, 14):
        return None
    in_body = (
        _rrect(px, py, 9, 20, 55, 42, 9)
        or _circ(px, py, 17, 40, 10)
        or _circ(px, py, 47, 40, 10)
    )
    if in_body:
        if (17 <= px <= 28 and 28.5 <= py <= 33.5) or (20.5 <= px <= 25.5 and 24 <= py <= 38):
            return "#312e81"
        if _circ(px, py, 43, 29, 3.4):
            return "#f472b6"
        if _circ(px, py, 49, 34, 3.4):
            return "#34d399"
        return "#ffffff"
    return "#4f46e5"



def load_app_icons() -> list:
    """Load icon_*.png from the icons folder; fall back to the drawn icon if missing."""
    out = []
    try:
        for p in sorted(ICONS_DIR.glob("icon_*.png"), reverse=True):
            out.append(tk.PhotoImage(file=str(p)))
    except Exception:
        out = []
    return out


def build_icon(size: int) -> tk.PhotoImage:
    img = tk.PhotoImage(width=size, height=size)
    k = 64.0 / size
    for y in range(size):
        x = 0
        while x < size:
            col = icon_color((x + 0.5) * k, (y + 0.5) * k)
            x2 = x + 1
            while x2 < size and icon_color((x2 + 0.5) * k, (y + 0.5) * k) == col:
                x2 += 1
            if col:
                img.put(col, to=(x, y, x2, y + 1))
            x = x2
    return img


def set_window_icon(root) -> None:
    """Windows: force the title-bar and taskbar icon straight from icons/app.ico (WM_SETICON)."""
    if not IS_WINDOWS:
        return
    ico = ICONS_DIR / "app.ico"
    if not ico.exists():
        return
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.GetParent.restype = wintypes.HWND
        u.GetParent.argtypes = [wintypes.HWND]
        u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        root.update_idletasks()
        hwnd = u.GetParent(root.winfo_id()) or root.winfo_id()
        small = u.LoadImageW(None, str(ico), 1, u.GetSystemMetrics(49), u.GetSystemMetrics(50), 0x10)
        big = u.LoadImageW(None, str(ico), 1, u.GetSystemMetrics(11), u.GetSystemMetrics(12), 0x10)
        if small:
            u.SendMessageW(hwnd, 0x80, 0, small)
        if big:
            u.SendMessageW(hwnd, 0x80, 1, big)
        root._win_icons = (small, big)
    except Exception:
        pass


def set_app_identity() -> None:
    """Give the process its own taskbar identity so Windows does not group it under Python."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GameLarper.App")
    except Exception:
        pass



BG, PANEL, FG, DIM = "#0A0A0A", "#121212", "#EAEAEA", "#7A7A7A"
LINE, ACCENT, GREEN = "#3A3A3A", "#E61919", "#4AF626"
MONO, HEAD = "Courier", "Helvetica"


def mono(size: int = 9, bold: bool = False) -> tuple:
    return (MONO, size, "bold") if bold else (MONO, size)


def apply_theme(root: tk.Tk) -> None:
    global MONO, HEAD
    fams = set(tkfont.families(root))
    MONO = next((f for f in ("JetBrains Mono", "IBM Plex Mono", "Cascadia Mono", "Consolas",
                             "DejaVu Sans Mono", "Courier New") if f in fams), "Courier")
    HEAD = next((f for f in ("Archivo Black", "Arial Black", "Impact", "Segoe UI Black",
                             "DejaVu Sans") if f in fams), "Helvetica")
    for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name).configure(family=MONO, size=9)
    root.configure(bg=BG)
    for pat, val in (
        ("*Background", BG), ("*Foreground", FG),
        ("*Entry.selectBackground", ACCENT), ("*Entry.selectForeground", BG),
        ("*Entry.insertBackground", ACCENT), ("*Entry.relief", "flat"),
        ("*Entry.highlightThickness", 1), ("*Entry.highlightBackground", LINE),
        ("*Entry.highlightColor", FG),
        ("*Button.relief", "flat"), ("*Button.borderWidth", 1), ("*Button.highlightThickness", 0),
        ("*Button.activeBackground", FG), ("*Button.activeForeground", BG),
    ):
        root.option_add(pat, val)

    st = ttk.Style()
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=FG, font=mono(), borderwidth=0, focuscolor=BG)

    st.configure("Brut.TButton", background=BG, foreground=FG, bordercolor="#4A4A4A",
                 lightcolor=BG, darkcolor=BG, borderwidth=1, relief="raised",
                 padding=(8, 4), anchor="center", font=mono(9, True), focusthickness=0)
    st.map("Brut.TButton",
           background=[("disabled", BG), ("pressed", ACCENT), ("active", FG)],
           foreground=[("disabled", "#4A4A4A"), ("pressed", BG), ("active", BG)],
           bordercolor=[("disabled", "#2A2A2A"), ("active", FG)],
           lightcolor=[("pressed", ACCENT), ("active", FG)],
           darkcolor=[("pressed", ACCENT), ("active", FG)])

    st.configure("Accent.Brut.TButton", background=ACCENT, foreground=BG, bordercolor=ACCENT,
                 lightcolor=ACCENT, darkcolor=ACCENT)
    st.map("Accent.Brut.TButton",
           background=[("disabled", "#2A0A0A"), ("pressed", FG), ("active", FG)],
           foreground=[("disabled", "#5A2A2A"), ("active", BG)],
           bordercolor=[("disabled", "#2A0A0A"), ("active", FG)],
           lightcolor=[("disabled", "#2A0A0A"), ("active", FG)],
           darkcolor=[("disabled", "#2A0A0A"), ("active", FG)])

    st.configure("Brut.TEntry", fieldbackground=BG, foreground=FG, insertcolor=ACCENT,
                 bordercolor="#4A4A4A", lightcolor="#4A4A4A", darkcolor="#4A4A4A", padding=4,
                 selectbackground=ACCENT, selectforeground=BG)
    st.map("Brut.TEntry", bordercolor=[("focus", FG)], lightcolor=[("focus", FG)],
           darkcolor=[("focus", FG)])

    st.configure("Brut.TCheckbutton", background=BG, foreground=FG, font=mono(),
                 indicatorbackground=BG, indicatorforeground=BG, upperbordercolor=FG,
                 lowerbordercolor=FG, indicatormargin=(0, 0, 6, 0), padding=2)
    st.map("Brut.TCheckbutton", background=[("active", BG)],
           indicatorbackground=[("selected", ACCENT), ("active", PANEL)],
           indicatorforeground=[("selected", BG)])

    st.layout("Brut.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
        "sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    st.configure("Brut.Vertical.TScrollbar", background="#4A4A4A", troughcolor=BG, bordercolor=BG,
                 lightcolor="#4A4A4A", darkcolor="#4A4A4A", gripcount=0, width=10, relief="flat")
    st.map("Brut.Vertical.TScrollbar",
           background=[("pressed", ACCENT), ("active", FG)],
           lightcolor=[("pressed", ACCENT), ("active", FG)],
           darkcolor=[("pressed", ACCENT), ("active", FG)])


def dark_titlebar(root: tk.Tk) -> None:
    """Windows 10/11: dark caption, red hairline border, light caption text. Silent no-op elsewhere."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        for attr, val in ((19, 1), (20, 1), (35, 0x000A0A0A), (34, 0x001919E6), (36, 0x00EAEAEA)):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ctypes.c_int(val)), 4)
    except Exception:
        pass


def _label(parent, text: str = "", fg: str = FG, bg: str = BG, size: int = 9,
           bold: bool = False, **kw) -> tk.Label:
    return tk.Label(parent, text=text, fg=fg, bg=bg, font=mono(size, bold), **kw)


def _panel(parent, title: str) -> tuple:
    """Titled panel: (outer, body, right_label). A red tab marks the header."""
    outer = tk.Frame(parent, bg=BG)
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(2, weight=1)
    head = tk.Frame(outer, bg=PANEL)
    head.grid(row=0, column=0, sticky="ew")
    tk.Frame(head, bg=ACCENT, width=4).pack(side="left", fill="y")
    _label(head, title, bg=PANEL, bold=True).pack(side="left", padx=8, pady=5)
    right = _label(head, "", fg=DIM, bg=PANEL, size=8)
    right.pack(side="right", padx=8)
    tk.Frame(outer, bg=LINE, height=1).grid(row=1, column=0, sticky="ew")
    body = tk.Frame(outer, bg=BG)
    body.grid(row=2, column=0, sticky="nsew")
    return outer, body, right


def _boxed(parent, title: str) -> tuple:
    """A panel inside a 1px line-coloured frame."""
    box = tk.Frame(parent, bg=LINE)
    outer, body, right = _panel(box, title)
    outer.pack(fill="both", expand=True, padx=1, pady=1)
    return box, body, right


class RowList(tk.Frame):
    """Canvas list with the Listbox calls the app uses (insert, delete, curselection, bind).
    The hovered row pops out: bottom-left and top-right corners cut, red bar on both sides."""
    RH, CUT = 22, 6

    def __init__(self, parent) -> None:
        super().__init__(parent, bg=BG)
        self.items: list[str] = []
        self.sel: set[int] = set()
        self.top, self.hot, self.anchor = 0, -1, 0
        self._base: set[int] = set()
        self._paint = True
        self._pending = False
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        box = tk.Frame(self, bg=LINE)
        box.grid(row=0, column=0, sticky="nsew")
        cv = self.cv = tk.Canvas(box, bg=BG, highlightthickness=0, bd=0, takefocus=1)
        cv.pack(fill="both", expand=True, padx=1, pady=1)
        cv.bind("<FocusIn>", lambda _e: box.config(bg=FG))
        cv.bind("<FocusOut>", lambda _e: box.config(bg=LINE))
        self.sb = ttk.Scrollbar(self, orient="vertical", style="Brut.Vertical.TScrollbar",
                                command=self._yview)
        self.sb.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        cv.bind("<Configure>", lambda _e: self._redraw())
        cv.bind("<Motion>", lambda e: self._set_hot(self._index(e.y)))
        cv.bind("<Leave>", lambda _e: self._set_hot(-1))
        cv.bind("<Button-1>", self._click)
        cv.bind("<B1-Motion>", self._drag)
        cv.bind("<MouseWheel>", lambda e: self._scroll(-3 if e.delta > 0 else 3))
        cv.bind("<Button-4>", lambda _e: self._scroll(-3))
        cv.bind("<Button-5>", lambda _e: self._scroll(3))
        cv.bind("<Up>", lambda _e: self._key(-1))
        cv.bind("<Down>", lambda _e: self._key(1))
        cv.bind("<space>", lambda _e: self._space())

    def bind(self, seq, func) -> None:
        self.cv.bind(seq, func, add="+")

    def delete(self, *_args) -> None:
        self.items.clear()
        self.sel.clear()
        self.top, self.hot = 0, -1
        self._redraw()

    def insert(self, _where, text: str) -> None:
        self.items.append(text)
        self._redraw()

    def curselection(self) -> tuple:
        return tuple(sorted(self.sel))

    def _visible(self) -> int:
        return max(1, self.cv.winfo_height() // self.RH)

    def _index(self, y: int) -> int:
        i = self.top + y // self.RH
        return i if y >= 0 and i < len(self.items) else -1

    def _clamp_top(self) -> None:
        self.top = max(0, min(self.top, max(0, len(self.items) - self._visible())))

    def _scroll(self, d: int) -> None:
        self.top += d
        self._clamp_top()
        cv = self.cv
        x, y = cv.winfo_pointerx() - cv.winfo_rootx(), cv.winfo_pointery() - cv.winfo_rooty()
        inside = 0 <= x < cv.winfo_width() and 0 <= y < cv.winfo_height()
        self.hot = self._index(y) if inside else -1
        self._redraw()

    def _yview(self, *a) -> None:
        if a[0] == "moveto":
            self.top = int(float(a[1]) * len(self.items))
            self._scroll(0)
        elif a[0] == "scroll":
            self._scroll(int(a[1]) * (self._visible() if a[2] == "pages" else 1))

    def _set_hot(self, i: int) -> None:
        if i != self.hot:
            self.hot = i
            self._redraw()

    def _click(self, e) -> None:
        self.cv.focus_set()
        i = self._index(e.y)
        if i < 0:
            return
        if e.state & 0x1:
            a, b = sorted((self.anchor, i))
            self.sel |= set(range(a, b + 1))
        else:
            self._paint = i not in self.sel
            self._base = set(self.sel)
            self.anchor = i
            self.sel = self._base | {i} if self._paint else self._base - {i}
        self._redraw()

    def _drag(self, e) -> None:
        if not self.items or e.state & 0x1:
            return
        y = e.y
        if y < 0 or y > self.cv.winfo_height():
            self._scroll(-1 if y < 0 else 1)
        i = max(0, min(len(self.items) - 1, self.top + y // self.RH))
        a, b = sorted((self.anchor, i))
        rng = set(range(a, b + 1))
        self.sel = self._base | rng if self._paint else self._base - rng
        self._redraw()

    def _space(self) -> str:
        if self.items:
            self.sel ^= {self.anchor}
            self._redraw()
        return "break"

    def _key(self, d: int) -> str:
        if self.items:
            i = max(0, min(len(self.items) - 1, self.anchor + d))
            self.sel, self.anchor = {i}, i
            if i < self.top:
                self.top = i
            elif i >= self.top + self._visible():
                self.top = i - self._visible() + 1
            self._redraw()
        return "break"

    def _redraw(self) -> None:
        if not self._pending:
            self._pending = True
            self.after_idle(self._draw)

    def _draw(self) -> None:
        self._pending = False
        cv, n, vis = self.cv, len(self.items), self._visible()
        self._clamp_top()
        w = cv.winfo_width()
        cv.delete("all")
        for i in range(self.top, min(n, self.top + vis + 1)):
            if i == self.hot:
                continue
            y = (i - self.top) * self.RH
            on = i in self.sel
            if on:
                cv.create_rectangle(0, y, w, y + self.RH, fill=ACCENT, outline="")
            cv.create_text(8, y + self.RH // 2, text=self.items[i], anchor="w", font=mono(),
                           fill=BG if on else FG)
        if self.top <= self.hot < min(n, self.top + vis + 1):
            self._pop(self.hot, w)
        self.sb.set(self.top / n if n else 0, min(1, (self.top + vis) / n) if n else 1)

    def _pop(self, i: int, w: int) -> None:
        cv, c, k, on = self.cv, self.CUT, 3, i in self.sel
        x0, x1 = 0, w
        y0 = (i - self.top) * self.RH
        y1 = y0 + self.RH

        def cut(a, b, p, q):
            return [a, b, p - c, b, p, b + c, p, q, a + c, q, a, q - c]

        cv.create_polygon(cut(x0, y0, x1, y1), fill=FG if on else "#6A6A6A", outline="")
        cv.create_polygon(cut(x0 + 1, y0 + 1, x1 - 1, y1 - 1), fill=ACCENT if on else "#1C1C1C", outline="")
        bar = FG if on else ACCENT
        left = [(x0, y0), (x0 + k, y0), (x0 + k, y1 - c + k), (x0, y1 - c)]
        right = [(x0 + x1 - px, y0 + y1 - py) for px, py in left]
        cv.create_polygon(left, fill=bar, outline="")
        cv.create_polygon(right, fill=bar, outline="")
        cv.create_text(14, y0 + self.RH // 2, text=self.items[i], anchor="w", font=mono(),
                       fill=BG if on else FG)


def _listbox(parent, row: int) -> RowList:
    lb = RowList(parent)
    lb.grid(row=row, column=0, sticky="nsew", padx=8)
    return lb


def _stripes(cv: tk.Canvas) -> None:
    """Hazard stripes, redrawn on resize."""
    w, h, step = cv.winfo_width(), cv.winfo_height(), 12
    cv.delete("all")
    for x in range(-h, w + h, step * 2):
        cv.create_polygon(x, h, x + step, h, x + step + h, 0, x + h, 0, fill=ACCENT, outline="")


def _barcode(cv: tk.Canvas) -> None:
    rng = random.Random(26)
    x = 0
    while x < 96:
        w = rng.choice((1, 1, 2, 3))
        cv.create_rectangle(x, 0, x + w, 30, fill=FG, outline="")
        x += w + rng.choice((1, 2, 2, 3))



class SpeedGraph(tk.Canvas):
    """Live download-speed graph (ping-monitor look) in the red theme.

    Header: last / min / max / avg / jtr / p95. Left axis: speed. Bottom axis: clock time.
    feed() may be called from worker threads (it only stores a number); the rest runs in Tk.
    """

    KEEP = 300
    STEP_MS = 100
    WINDOW = 0.5
    PX = 3

    def __init__(self, parent, width: int = 420, height: int = 150) -> None:
        super().__init__(parent, bg=BG, width=width, height=height, highlightthickness=0, borderwidth=0)
        self.samples: deque = deque(maxlen=self.KEEP)
        self._hist: deque = deque()
        self._done = 0
        self.active = False
        self.frozen = False
        self.state = "IDLE"
        self.bind("<Configure>", lambda _e: self.redraw())
        self.after(self.STEP_MS, self._tick)


    def feed(self, done_bytes: int) -> None:
        self._done = done_bytes

    def begin(self) -> None:
        self.samples.clear()
        self._hist.clear()
        self._hist.append((time.time(), 0))
        self._done = 0
        self.active = True
        self.state = "DOWNLOADING"
        self.redraw()

    def finish(self) -> None:
        if self.active:
            self.active = False
            self.state = "DONE"
            self.redraw()

    def _tick(self) -> None:
        if self.active:
            now, b = time.time(), self._done
            self._hist.append((now, b))
            while len(self._hist) > 1 and now - self._hist[0][0] > self.WINDOW:
                self._hist.popleft()
            t0, b0 = self._hist[0]
            if now > t0:
                self.samples.append((now, (b - b0) / (now - t0)))
            self.redraw()
        self.after(self.STEP_MS, self._tick)


    @staticmethod
    def _fmt(v: float) -> str:
        if v >= 1048576:
            return f"{v / 1048576:.2f}M"
        if v >= 1024:
            return f"{v / 1024:.0f}K"
        return f"{v:.0f}B"

    def redraw(self) -> None:
        if self.frozen:
            return
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 120 or h < 70:
            return
        left, top, right, bottom = 50, 22, w - 10, h - 18
        fit = int((right - left) // self.PX) + 1
        shown = list(self.samples)[-fit:]
        vals = [v for _, v in shown]
        n = len(vals)

        if n:
            jtr = sum(abs(vals[i] - vals[i - 1]) for i in range(1, n)) / (n - 1) if n > 1 else 0.0
            p95 = sorted(vals)[int(0.95 * (n - 1))]
            stats = [("last", vals[-1]), ("min", min(vals)), ("max", max(vals)),
                     ("avg", sum(vals) / n), ("jtr", jtr), ("p95", p95)]
        else:
            stats = [(k, None) for k in ("last", "min", "max", "avg", "jtr", "p95")]
        colw = (w - 16) / len(stats)
        for i, (k, v) in enumerate(stats):
            self.create_text(8 + i * colw, 5, anchor="nw", fill=ACCENT, font=mono(7, True),
                             text=f"{k} {self._fmt(v) if v is not None else '--'}")

        ymax = max((max(vals) * 1.15) if n else 0, 65536)
        for i in range(5):
            y = bottom - (bottom - top) * i / 4
            if i:
                self.create_line(left, y, right, y, fill="#2A1010", dash=(2, 4))
            self.create_text(left - 5, y, anchor="e", fill=FG, font=mono(7), text=self._fmt(ymax * i / 4))
        self.create_line(left, top, left, bottom, fill=FG)
        self.create_line(left, bottom, right, bottom, fill=FG)

        if n >= 2:
            pts = []
            for k, v in enumerate(vals):
                x = left + k * self.PX
                y = bottom - (bottom - top) * min(v / ymax, 1.0)
                self.create_line(x, y, x, bottom - 1, fill="#5C1010", dash=(1, 2))
                pts += [x, y]
            self.create_line(*pts, fill=ACCENT, width=1)
            prev = int(shown[0][0]) // 5
            for k, (t, _v) in enumerate(shown):
                cur = int(t) // 5
                if cur != prev:
                    x = left + k * self.PX
                    if left + 22 <= x <= right - 22:
                        self.create_line(x, bottom, x, bottom + 3, fill=FG)
                        self.create_text(x, h - 8, fill=FG, font=mono(7),
                                         text=time.strftime("%H:%M:%S", time.localtime(t)))
                prev = cur
        elif not self.active:
            self.create_text((left + right) / 2, (top + bottom) / 2, fill=DIM, font=mono(9, True),
                             text="NO TRANSFER")
        self.create_text(right - 4, top + 4, anchor="ne", fill=ACCENT if self.active else DIM,
                         font=mono(7, True), text=f"\u25cf {self.state}  (BYTES/S)")


class MsgDialog(tk.Toplevel):
    """Dark-mode stand-in for tkinter.messagebox (OK, or a YES/NO confirm)."""

    def __init__(self, parent, title: str, message: str, kind: str = "info", yesno: bool = False) -> None:
        super().__init__(parent, bg=BG)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = False if yesno else None
        head_fg = ACCENT if kind == "error" else FG

        body = tk.Frame(self, bg=BG)
        body.pack(padx=18, pady=16)
        _label(body, title.upper(), fg=head_fg, bold=True, anchor="w").pack(fill="x")
        _label(body, message, fg=FG, anchor="w", justify="left", wraplength=320).pack(
            fill="x", pady=(8, 0))
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(16, 0))
        if yesno:
            ttk.Button(row, text="NO", style="Brut.TButton", command=self._no).pack(side="right")
            ttk.Button(row, text="YES", style="Accent.Brut.TButton", command=self._yes).pack(
                side="right", padx=6)
            self.protocol("WM_DELETE_WINDOW", self._no)
            self.bind("<Return>", lambda _e: self._yes())
            self.bind("<Escape>", lambda _e: self._no())
        else:
            ttk.Button(row, text="OK \u25b8", style="Accent.Brut.TButton", command=self.destroy).pack(
                side="right")
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.bind("<Return>", lambda _e: self.destroy())
            self.bind("<Escape>", lambda _e: self.destroy())

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        dark_titlebar(self)
        self.grab_set()
        self.focus_set()

    def _yes(self) -> None:
        self.result = True
        self.destroy()

    def _no(self) -> None:
        self.result = False
        self.destroy()


def show_info(parent, title: str, message: str) -> None:
    parent.wait_window(MsgDialog(parent, title, message, kind="info"))


def show_error(parent, title: str, message: str) -> None:
    parent.wait_window(MsgDialog(parent, title, message, kind="error"))


def ask_yesno(parent, title: str, message: str) -> bool:
    dlg = MsgDialog(parent, title, message, yesno=True)
    parent.wait_window(dlg)
    return dlg.result


class StringDialog(tk.Toplevel):
    """Dark-mode stand-in for tkinter.simpledialog.askstring."""

    def __init__(self, parent, title: str, message: str, initial: str = "") -> None:
        super().__init__(parent, bg=BG)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.val = tk.StringVar(value=initial)

        body = tk.Frame(self, bg=BG)
        body.pack(padx=18, pady=16)
        _label(body, title.upper(), fg=ACCENT, bold=True, anchor="w").pack(fill="x")
        _label(body, message, fg=DIM, size=8, anchor="w", justify="left", wraplength=320).pack(
            fill="x", pady=(8, 6))
        entry = ttk.Entry(body, textvariable=self.val, style="Brut.TEntry", font=mono(), width=36)
        entry.pack(fill="x")
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="CANCEL", style="Brut.TButton", command=self.destroy).pack(side="right")
        ttk.Button(row, text="OK \u25b8", style="Accent.Brut.TButton", command=self._ok).pack(
            side="right", padx=6)

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        dark_titlebar(self)
        entry.focus_set()
        entry.select_range(0, "end")
        self.grab_set()

    def _ok(self) -> None:
        self.result = self.val.get().strip()
        self.destroy()


def ask_string(parent, title: str, message: str, initial: str = "") -> str | None:
    dlg = StringDialog(parent, title, message, initial)
    parent.wait_window(dlg)
    return dlg.result or None


class DurationDialog(tk.Toplevel):
    """Minutes for one game. result = (text, minutes or None, use_for_rest), or None if cancelled."""

    def __init__(self, parent, game: str, idx: int, total: int, initial: str) -> None:
        super().__init__(parent, bg=BG)
        self.title("Duration")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.val = tk.StringVar(value=initial)
        self.rest = tk.BooleanVar(value=False)

        body = tk.Frame(self, bg=BG)
        body.pack(padx=16, pady=14)
        _label(body, f"[{idx}/{total}]  {game}", fg=ACCENT, bold=True, anchor="w").pack(fill="x")
        _label(body, "MINUTES TO RUN (EMPTY = UNTIL YOU PRESS STOP)", fg=DIM, size=8, anchor="w").pack(
            fill="x", pady=(8, 4))
        entry = ttk.Entry(body, textvariable=self.val, style="Brut.TEntry", font=mono(), width=12)
        entry.pack(anchor="w")
        if idx < total:
            label = "USE THIS TIME FOR ALL GAMES" if idx == 1 else "USE THIS TIME FOR ALL REMAINING GAMES"
            ttk.Checkbutton(body, text=label, variable=self.rest, style="Brut.TCheckbutton").pack(
                anchor="w", pady=(10, 0))
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="CANCEL", style="Brut.TButton", command=self.destroy).pack(side="right")
        ttk.Button(row, text="OK ▸", style="Accent.Brut.TButton", command=self._ok).pack(side="right", padx=6)

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        dark_titlebar(self)
        entry.focus_set()
        entry.select_range(0, "end")
        self.grab_set()

    def _ok(self) -> None:
        text = self.val.get().strip()
        minutes = None
        if text:
            try:
                minutes = float(text)
                if minutes <= 0:
                    raise ValueError
            except ValueError:
                show_error(self, "Duration", "Enter a positive number of minutes, or leave it empty.")
                return
        self.result = (text, minutes, self.rest.get())
        self.destroy()


def center_window(root: tk.Tk, width: int, height: int) -> None:
    """Size the window and place it in the middle of the screen (shrinks to fit small screens)."""
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    width, height = min(width, sw - 40), min(height, sh - 80)
    x = (sw - width) // 2
    y = (sh - height) // 2 - 60
    root.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Game Larper")
        try:
            self._icons = load_app_icons() or [build_icon(64), build_icon(32)]
            root.iconphoto(True, *self._icons)

        except Exception:
            pass
        center_window(root, 1040, 760)
        root.minsize(960, 660)

        self.games: list[Game] = []
        self.visible: list[Game] = []
        self.queue: list[Game] = []
        self.stop_evt = threading.Event()
        self.worker: threading.Thread | None = None

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.apply_filter())
        self.random_var = tk.StringVar(value="25")
        self.status_var = tk.StringVar(value="Loading catalog...")
        self.count_var = tk.StringVar(value="")
        self.folder_var = tk.BooleanVar(value=False)
        self.min_var = tk.BooleanVar(value=False)

        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        threading.Thread(target=self._load_games_thread, args=(not CACHE_PATH.exists(),), daemon=True).start()


    def _build_ui(self) -> None:
        root = self.root
        self.status_up = tk.StringVar(value=self.status_var.get().upper())
        self.status_var.trace_add("write", lambda *_: self.status_up.set(self.status_var.get().upper()))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        head = tk.Frame(root, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        head.columnconfigure(1, weight=1)
        title = tk.Frame(head, bg=BG)
        title.grid(row=0, column=0, sticky="w")
        tk.Label(title, text="GAME", fg=FG, bg=BG, font=(HEAD, 26, "bold")).pack(side="left")
        tk.Label(title, text="LARPER", fg=ACCENT, bg=BG, font=(HEAD, 26, "bold")).pack(side="left", padx=(10, 0))
        _label(title, "™", fg=DIM, size=10).pack(side="left", anchor="n", pady=(6, 0))
        _label(head, "◆ DISCORD DETECTABLE CATALOG   ▸ LOCAL STAND-INS   ▸ NO TOKEN, NO TELEMETRY",
               fg=DIM, size=8).grid(row=1, column=0, sticky="w")
        right = tk.Frame(head, bg=BG)
        right.grid(row=0, column=2, rowspan=2, sticky="e")
        bars = tk.Canvas(right, width=96, height=30, bg=BG, highlightthickness=0)
        bars.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        _barcode(bars)
        _label(right, "REV 2.6", size=8, bold=True).grid(row=0, column=1, sticky="w")
        _label(right, "UNIT / D-01", fg=DIM, size=8).grid(row=1, column=1, sticky="w")
        self.state_lbl = _label(right, "[ IDLE ]", fg=DIM, size=9, bold=True)
        self.state_lbl.grid(row=0, column=2, rowspan=2, padx=(14, 0))

        stripes = tk.Canvas(root, height=8, bg=BG, highlightthickness=0)
        stripes.grid(row=1, column=0, sticky="ew", padx=14)
        stripes.bind("<Configure>", lambda _e: _stripes(stripes))

        main = tk.Frame(root, bg=LINE)
        main.grid(row=2, column=0, sticky="nsew", padx=14, pady=(10, 0))
        main.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1, uniform="col")
        main.columnconfigure(1, weight=1, uniform="col")

        cat, cbody, cright = _panel(main, "01 / CATALOG")
        cat.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        cright.config(textvariable=self.count_var)
        cbody.columnconfigure(0, weight=1)
        cbody.rowconfigure(1, weight=1)
        bar = tk.Frame(cbody, bg=BG)
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        bar.columnconfigure(1, weight=1)
        _label(bar, "▸ SEARCH", fg=ACCENT, bold=True).grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(bar, textvariable=self.search_var, style="Brut.TEntry", font=mono()).grid(
            row=0, column=1, sticky="ew")
        ttk.Button(bar, text="REFRESH", style="Brut.TButton", command=self.refresh_catalog).grid(
            row=0, column=2, padx=(8, 0))
        self.results = _listbox(cbody, 1)
        self.results.bind("<Double-Button-1>", lambda _e: self.add_selected())
        foot = tk.Frame(cbody, bg=BG)
        foot.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        ttk.Button(foot, text="ADD SELECTED", style="Brut.TButton", command=self.add_selected).pack(side="left")
        ttk.Button(foot, text="ADD ALL", style="Brut.TButton", command=self.add_all_shown).pack(
            side="left", padx=6)
        ttk.Entry(foot, textvariable=self.random_var, width=4, style="Brut.TEntry", font=mono()).pack(side="left", padx=(8, 0))
        ttk.Button(foot, text="ADD RANDOM", style="Brut.TButton", command=self.add_random).pack(
            side="left", padx=6)

        que, qbody, self.queue_title = _panel(main, "02 / QUEUE")
        que.grid(row=0, column=1, sticky="nsew", padx=1, pady=1)
        self.queue_title.config(text="000 ITEMS")
        qbody.columnconfigure(0, weight=1)
        qbody.rowconfigure(1, weight=1)
        tool = tk.Frame(qbody, bg=BG)
        tool.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        ttk.Button(tool, text="REMOVE", style="Brut.TButton", command=self.remove_selected).pack(side="left")
        ttk.Button(tool, text="CLEAR", style="Brut.TButton", command=self.clear_queue).pack(side="left", padx=6)
        ttk.Button(tool, text="SHUFFLE", style="Brut.TButton", command=self.shuffle_queue).pack(side="left")
        _label(tool, "DEL = REMOVE", fg=DIM, size=8).pack(side="right")
        self.queue_box = _listbox(qbody, 1)
        self.queue_box.bind("<Delete>", lambda _e: self.remove_selected())
        qfoot = tk.Frame(qbody, bg=BG)
        qfoot.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        self.stop_btn = ttk.Button(qfoot, text="STOP ▪", style="Brut.TButton", command=self.stop, state="disabled")
        self.stop_btn.pack(side="right")
        self.start_btn = ttk.Button(qfoot, text="START ▸", style="Accent.Brut.TButton", command=self.start)
        self.start_btn.pack(side="right", padx=6)

        obox, obody, _ = _boxed(root, "03 / OPTIONS")
        obox.grid(row=3, column=0, sticky="ew", padx=14, pady=(10, 0))
        opts = tk.Frame(obody, bg=BG)
        opts.pack(fill="x", padx=8, pady=8)
        ttk.Checkbutton(opts, text="ALSO RUN 'GAME NAME\\EXE' PATH VARIANT", variable=self.folder_var,
                        style="Brut.TCheckbutton").pack(side="left")
        ttk.Button(opts, text="ADD CUSTOM PATH...", style="Brut.TButton", command=self.add_custom).pack(
            side="left", padx=10)
        ttk.Checkbutton(opts, text="MINIMIZE WINDOWS", variable=self.min_var,
                        style="Brut.TCheckbutton").pack(side="left")
        ttk.Button(opts, text="CHOOSE STUB EXE...", style="Brut.TButton", command=self.choose_stub).pack(
            side="left", padx=10)

        lbox, lbody, _ = _boxed(root, "04 / LOG")
        lbox.grid(row=4, column=0, sticky="ew", padx=14, pady=(10, 0))
        lbody.columnconfigure(0, weight=1)
        self.log_box = tk.Text(lbody, height=7, width=40, state="disabled", wrap="none", bg=BG, fg=FG, font=mono(11),
                               relief="flat", borderwidth=0, highlightthickness=0, padx=8, pady=6,
                               insertbackground=ACCENT, selectbackground=ACCENT, selectforeground=BG)
        self.log_box.grid(row=0, column=0, sticky="nsew")
        self.graph_sep = tk.Frame(lbody, bg=LINE, width=1)
        self.graph_sep.grid(row=0, column=1, sticky="ns")
        self.graph = SpeedGraph(lbody, width=GRAPH_W)
        self.graph.grid(row=0, column=2, sticky="nsew")
        self._graph_open = False
        self._graph_w = 0
        self.graph.grid_remove()
        self.graph_sep.grid_remove()
        self._graph_job = None
        self._close_job = None
        self.log_box.tag_configure("ts", foreground=DIM)
        self.log_box.tag_configure("err", foreground=ACCENT)
        self.log_box.tag_configure("live", foreground=FG, font=mono(13, True))
        self._live_label: tuple[str, str] | None = None
        self._cw = max(1, tkfont.Font(font=mono(13, True)).measure("0"))
        self._spin = 0
        self._tick()

        sbox = tk.Frame(root, bg=LINE)
        sbox.grid(row=5, column=0, sticky="ew", padx=14, pady=(10, 12))
        inner = tk.Frame(sbox, bg=PANEL)
        inner.pack(fill="x", padx=1, pady=1)
        _label(inner, "▸", fg=ACCENT, bg=PANEL, bold=True).pack(side="left", padx=(8, 6), pady=5)
        tk.Label(inner, textvariable=self.status_up, anchor="w", fg=FG, bg=PANEL, font=mono()).pack(
            side="left", fill="x", expand=True)


    def _load_games_thread(self, force: bool) -> None:
        est = CACHE_PATH.stat().st_size if CACHE_PATH.exists() else 6 * 1048576

        started: list[bool] = []

        def prog(done: int, total: int, name: str) -> None:
            if not started:
                started.append(True)
                self.root.after(0, self._download_begin)
            self.graph.feed(done)
            total = total or max(est, done)
            pct = done * 100 // total if total else 0
            self.live(name or "games.json", f"{done / 1048576:.1f}/{total / 1048576:.1f} MB {min(pct, 99):3d}%")

        before = CACHE_PATH.stat().st_mtime_ns if CACHE_PATH.exists() else None
        if force or before is None:
            self.live("games.json", "0.0/0.0 MB   0%")
        try:
            games = load_catalog(force, prog)
            self.end_live()
            if started:
                self.root.after(0, self._download_done)
            after = CACHE_PATH.stat().st_mtime_ns if CACHE_PATH.exists() else None
            mb = CACHE_PATH.stat().st_size / 1048576 if after is not None else 0.0
            if force or after != before:
                self.log(f"games.json downloaded ({mb:.2f} MB)")
            else:
                self.log(f"games.json already exists, skipped download ({mb:.2f} MB)")
        except Exception as exc:
            self.end_live()
            if started:
                self.root.after(0, self.graph.finish)
            self.log(f"FAILED games.json: {exc}")
            try:
                games = load_catalog(False)
                self.log("using cached games.json")
            except Exception as exc2:
                self.root.after(0, lambda: self.status_var.set(str(exc2)))
                return
        self.root.after(0, lambda: self._set_games(games))

    def _download_begin(self) -> None:
        if self._close_job:
            self.root.after_cancel(self._close_job)
            self._close_job = None
        self._slide_graph(True)
        self.graph.begin()

    def _download_done(self) -> None:
        self.graph.finish()
        self._close_job = self.root.after(SLIDE_HOLD_MS, lambda: self._slide_graph(False))

    def _slide_graph(self, opening: bool) -> None:
        """Slide the graph panel shut toward the right edge; the log box grows into its place in the same motion."""
        if opening == self._graph_open:
            return
        self._graph_open = opening
        if self._graph_job:
            self.root.after_cancel(self._graph_job)
            self._graph_job = None
        g = self.graph
        if opening:
            self.graph_sep.grid()
            g.grid()
        g.frozen = True
        start, end = self._graph_w, (GRAPH_W if opening else 0)
        t0 = time.time()

        def step() -> None:
            k = min(1.0, (time.time() - t0) * 1000 / SLIDE_MS)
            e = k * k * (3 - 2 * k)
            self._graph_w = int(start + (end - start) * e)
            g.config(width=max(1, self._graph_w))
            if k < 1:
                self._graph_job = self.root.after(15, step)
                return
            self._graph_job = None
            if opening:
                g.frozen = False
                g.redraw()
            else:
                g.grid_remove()
                self.graph_sep.grid_remove()

        step()

    def _set_games(self, games: list[Game]) -> None:
        self.games = games
        self.apply_filter()
        self.status_var.set(f"Catalog loaded: {len(games)} games with a {PLATFORM_KEY} executable.")

    def refresh_catalog(self) -> None:
        self.status_var.set("Refreshing catalog...")
        threading.Thread(target=self._load_games_thread, args=(True,), daemon=True).start()

    def apply_filter(self) -> None:
        term = self.search_var.get().strip().lower()
        self.visible = [g for g in self.games if term in g.name.lower()] if term else list(self.games)
        self.results.delete(0, "end")
        for g in self.visible:
            self.results.insert("end", g.name)
        self.count_var.set(f"{len(self.visible)} SHOWN")


    def _refresh_queue(self) -> None:
        self.queue_box.delete(0, "end")
        for g in self.queue:
            self.queue_box.insert("end", f"{g.name}   [{g.exe.split('/')[-1]}]")
        self.queue_title.config(text=f"{len(self.queue):03d} ITEMS")

    def _add(self, games: list[Game]) -> None:
        have = {(g.name, g.exe) for g in self.queue}
        for g in games:
            if (g.name, g.exe) not in have:
                self.queue.append(g)
                have.add((g.name, g.exe))
        self._refresh_queue()

    def add_selected(self) -> None:
        self._add([self.visible[i] for i in self.results.curselection()])

    def add_all_shown(self) -> None:
        self._add(list(self.visible))

    def add_random(self) -> None:
        try:
            n = int(self.random_var.get())
        except ValueError:
            show_error(self.root, "Random", "Enter a whole number.")
            return
        n = max(1, min(n, MAX_QUEUE_RANDOM, len(self.visible)))
        if not self.visible:
            return
        self._add(random.sample(self.visible, n))

    def choose_stub(self) -> None:
        global CUSTOM_STUB
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Pick an exe to copy and rename for each game",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            CUSTOM_STUB = path
            self.log(f"custom stub set: {path}")

    def add_custom(self) -> None:
        raw = ask_string(
            self.root,
            "Custom path",
            "Path ending Discord should see, for example:\nCyberpunk 2077\\Cyberpunk2077.exe",
        )
        if not raw:
            return
        rel = _clean_exe_name(raw)
        if not rel:
            show_error(self.root, "Custom path", "That path is not valid.")
            return
        self._add([Game(name=rel, exe=rel)])

    def remove_selected(self) -> None:
        idx = set(self.queue_box.curselection())
        self.queue = [g for i, g in enumerate(self.queue) if i not in idx]
        self._refresh_queue()

    def clear_queue(self) -> None:
        self.queue = []
        self._refresh_queue()

    def shuffle_queue(self) -> None:
        random.shuffle(self.queue)
        self._refresh_queue()


    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.queue:
            show_info(self.root, "Queue empty", "Add at least one game first.")
            return
        batch = list(self.queue)
        durations: list[float | None] = []
        text = DEFAULT_MINUTES
        for i, g in enumerate(batch, 1):
            dlg = DurationDialog(self.root, g.name, i, len(batch), text)
            self.root.wait_window(dlg)
            if dlg.result is None:
                return
            text, minutes, rest = dlg.result
            durations.append(minutes)
            if rest:
                durations += [minutes] * (len(batch) - i)
                break

        global MINIMIZE_WINDOWS
        MINIMIZE_WINDOWS = bool(self.min_var.get())
        self.stop_evt.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.state_lbl.config(text="[ ACTIVE ]", fg=GREEN)
        self.worker = threading.Thread(target=self._run, args=(batch, durations), daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_evt.set()
        self.status_var.set("Stopping...")

    def _set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status_var.set(text))

    def log(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {text}"
        try:
            with open(APP_DIR / "larper.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
        self.root.after(0, lambda: self._log_ui(line))

    def _log_ui(self, line: str) -> None:
        self.log_box.config(state="normal")
        self._clear_live()
        ts, _, msg = line.partition("  ")
        self.log_box.insert("end", ts + "  ", "ts")
        self.log_box.insert("end", msg + "\n", "err" if msg.startswith("FAILED") else "")
        self._draw_live()
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def live(self, name: str, stats: str = "") -> None:
        self._live_label = (name, stats)

    def end_live(self) -> None:
        self._live_label = None

    def _clear_live(self) -> None:
        r = self.log_box.tag_ranges("live")
        if r:
            self.log_box.delete(r[0], r[1])

    def _draw_live(self) -> None:
        if not self._live_label:
            return
        name, stats = self._live_label
        cols = max(30, (self.log_box.winfo_width() - 24) // self._cw - 3)
        room = cols - len(stats) - 1
        if len(name) > room:
            name = name[: max(1, room - 3)] + "..."
        frame = SPIN_FRAMES[self._spin % len(SPIN_FRAMES)]
        self.log_box.insert("end", f"{name.ljust(room)} {stats} {frame}", "live")

    def _tick(self) -> None:
        self._spin += 1
        self.log_box.config(state="normal")
        self._clear_live()
        self._draw_live()
        self.log_box.see("end")
        self.log_box.xview_moveto(0)
        self.log_box.config(state="disabled")
        self.root.after(80, self._tick)

    def _launch(self, game: Game) -> FakeGame | None:
        fake = FakeGame(game)
        try:
            fake.start()
        except Exception as exc:
            fake.stop()
            self.log(f"FAILED {game.name}: {exc}")
            return None
        with ACTIVE_LOCK:
            ACTIVE.append(fake)
        self.log(f"started {game.name} via {fake.stub} pid={fake.proc.pid if fake.proc else '?'} path={fake.path}")
        return fake

    def _launch_all(self, game: Game) -> list[FakeGame]:
        out: list[FakeGame] = []
        first = self._launch(game)
        if first:
            out.append(first)
        if self.folder_var.get():
            alt = folder_variant(game)
            if alt:
                second = self._launch(Game(name=game.name, exe=alt))
                if second:
                    out.append(second)
        return out

    def _release(self, fake: FakeGame) -> None:
        fake.stop()
        with ACTIVE_LOCK:
            if fake in ACTIVE:
                ACTIVE.remove(fake)

    def _run(self, batch: list[Game], durations: list[float | None]) -> None:
        try:
            if all(d is None for d in durations):
                started = [f for g in batch for f in self._launch_all(g)]
                self._set_status(f"Running {len(started)} game(s) until you press Stop.")
                n = len(started)
                while not self.stop_evt.is_set():
                    self.live(f"{n} games", f"{sum(f.alive() for f in started)}/{n} alive --%")
                    self.stop_evt.wait(0.5)
                for f in started:
                    self._release(f)
            else:
                total = len(batch)
                for i, g in enumerate(batch, 1):
                    if self.stop_evt.is_set():
                        break
                    fakes = self._launch_all(g)
                    if not fakes:
                        continue
                    minutes = durations[i - 1]
                    end = None if minutes is None else time.time() + minutes * 60
                    while not self.stop_evt.is_set():
                        if end is None:
                            left_txt = "until Stop"
                        else:
                            left = end - time.time()
                            if left <= 0:
                                break
                            left_txt = f"{int(left)}s left"
                        if not any(f.alive() for f in fakes):
                            self.log(f"{g.name} process exited early")
                            break
                        pct = "--" if end is None else f"{min(99, int((1 - left / (minutes * 60)) * 100)):3d}"
                        self.live(g.exe.split("/")[-1], f"{i}/{total} {pct}%")
                        self._set_status(
                            f"[{i}/{total}] {g.name} ({g.exe.split('/')[-1]})  {left_txt}"
                        )
                        self.stop_evt.wait(1.0)
                    self.end_live()
                    for f in fakes:
                        self._release(f)
                    self.log(f"stopped {g.name}")
                    if i < total and not self.stop_evt.is_set():
                        self._set_status(f"[{i}/{total}] done: {g.name}")
                        self.stop_evt.wait(GAP_SECONDS)
        finally:
            self.end_live()
            _cleanup_all()
            self.root.after(0, self._finished)

    def _finished(self) -> None:
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.state_lbl.config(text="[ IDLE ]", fg=DIM)
        self.status_var.set("Idle.")

    def on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not ask_yesno(self.root, "Quit", "Games are still running. Stop them and quit?"):
                return
            self.stop_evt.set()
        _cleanup_all()
        self.root.destroy()


def _relaunch_without_console() -> None:
    """If started from a console (py.exe / python.exe), restart under pythonw.exe and exit."""
    if not IS_WINDOWS or FROZEN or os.environ.get("LARPER_CHILD"):
        return
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        return
    pyw = exe.with_name("pythonw.exe")
    if not pyw.exists():
        return
    env = dict(os.environ, LARPER_CHILD="1")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [str(pyw), str(Path(__file__).resolve()), *sys.argv[1:]],
            env=env, creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return
    sys.exit(0)


def _log_crash(exc_type, exc, tb) -> None:
    import traceback
    try:
        with open(APP_DIR / "larper.log", "a", encoding="utf-8") as fh:
            fh.write("CRASH\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
    except Exception:
        pass


def main() -> None:
    _relaunch_without_console()
    sys.excepthook = _log_crash
    set_app_identity()
    root = tk.Tk()
    apply_theme(root)
    App(root)
    dark_titlebar(root)
    set_window_icon(root)
    root.after(300, lambda: set_window_icon(root))
    root.mainloop()


if __name__ == "__main__":
    main()