#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import atexit
import base64
import contextvars
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
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

DEBUG_PORT = 9222
PLATFORMS = {
    "Windows": "Discord Client", "Xbox": "Discord Embedded", "PlayStation": "Discord Embedded",
    "Android": "Discord Android", "iOS": "Discord iOS", "Web": "Discord Web", "VR": "Discord VR",
}
_PLATFORM_JS = """((b) => {
  const W = window.webpackChunkdiscord_app, M = W.push([[Symbol()], {}, r => r.c]); W.pop();
  let G;
  for (const m of Object.values(M)) {
    try {
      const e = m.exports; if (!e || e === window) continue;
      for (const t of [e, e.default, ...Object.values(e)])
        if (t && t !== window && t[Symbol.toStringTag] !== "IntlMessagesProxy" && "getSocket" in Object(t)) { G = t; break; }
    } catch {}
    if (G) break;
  }
  const s = G.getSocket(), L = window.__larp || (window.__larp = { o: s.send });
  L.b = b;
  if (s.send === L.o) s.send = function (op, d, ...a) {
    if (op === 2 && d && d.properties) d = { ...d, properties: { ...d.properties, browser: L.b } };
    return Reflect.apply(L.o, this, [op, d, ...a]);
  };
  s.sessionId = null; s.seq = 0;
  const w = s.webSocket; if (w && w.readyState < WebSocket.CLOSING) w.close(1000);
  return "ok";
})(%s)"""

def _ws_recv(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("debug socket closed")
        buf += chunk
    return buf

def cdp_eval(expr: str) -> str:
    """Run JS inside Discord's page over the debug port (tiny stdlib websocket client)."""
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json", timeout=3))
    tab = next(t for t in tabs if t.get("type") == "page" and "discord.com" in t.get("url", "")
               and t.get("webSocketDebuggerUrl"))
    u = urlparse(tab["webSocketDebuggerUrl"])
    s = socket.create_connection((u.hostname, u.port), timeout=5)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += _ws_recv(s, 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(head.split(b"\r\n")[0].decode(errors="replace"))
        body = json.dumps({"id": 1, "method": "Runtime.evaluate",
                           "params": {"expression": expr, "returnByValue": True}}).encode()
        n, mask = len(body), os.urandom(4)
        ln = bytes([0x80 | n]) if n < 126 else b"\xfe" + n.to_bytes(2, "big") if n < 65536 else b"\xff" + n.to_bytes(8, "big")
        s.sendall(b"\x81" + ln + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(body)))

        b1 = _ws_recv(s, 2)[1] & 0x7F
        b1 = int.from_bytes(_ws_recv(s, 2), "big") if b1 == 126 else int.from_bytes(_ws_recv(s, 8), "big") if b1 == 127 else b1
        res = json.loads(_ws_recv(s, b1))["result"]
        if "exceptionDetails" in res:
            raise RuntimeError(res["exceptionDetails"].get("exception", {}).get("description", "script error"))
        return str(res["result"].get("value"))
    finally:
        s.close()

def set_platform(name: str) -> None:
    cdp_eval(_PLATFORM_JS % json.dumps(PLATFORMS[name]))

TOKEN_SNIPPET = ("copy(((w=webpackChunkdiscord_app).push([[Symbol()],{},o=>{try{Object.values(o.c).some("
                 "e=>e.exports?.setToken&&(w.t=e.exports.getToken()))}catch{}}]),w.t))")
TOKEN_FILE = APP_DIR / "token.dat"
_KEYRING = ["service", "gamelarper", "account", "default"]
GATEWAY_HOST = "gateway.discord.gg"
GATEWAY_PATH = "/?v=9&encoding=json"

GW_PROPS = {
    "Windows": {
        "os": "Windows",
        "browser": "Discord Client",
        "device": "",
        "system_locale": "en-US",
        "browser_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "browser_version": "124.0.0.0",
        "os_version": "10",
        "referrer": "",
        "referring_domain": ""
    },
    "Xbox": {
        "os": "Console",
        "browser": "Discord Embedded",
        "device": "Xbox Series X",
        "system_locale": "en-US",
        "browser_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; Xbox; Xbox Series X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2486.0 Safari/537.36 Edge/13.10586",
        "browser_version": "",
        "os_version": "",
        "referrer": "",
        "referring_domain": ""
    },
    "PlayStation": {
        "os": "Console",
        "browser": "Discord Embedded",
        "device": "PlayStation 5",
        "system_locale": "en-US",
        "browser_user_agent": "Mozilla/5.0 (PlayStation 5 3.11) AppleWebKit/605.1.15 (KHTML, like Gecko)",
        "browser_version": "",
        "os_version": "",
        "referrer": "",
        "referring_domain": ""
    },
    "Android": {
        "os": "Android",
        "browser": "Discord Android",
        "device": "Pixel 8",
        "system_locale": "en-US",
        "browser_user_agent": "Discord-Android/214116;ROM:13;Device:Pixel 8",
        "browser_version": "214.116",
        "os_version": "13",
        "referrer": "",
        "referring_domain": ""
    },
    "iOS": {
        "os": "iOS",
        "browser": "Discord iOS",
        "device": "iPhone16,2",
        "system_locale": "en-US",
        "browser_user_agent": "Discord/268.0 CFNetwork/1474 Darwin/23.0.0",
        "browser_version": "268.0",
        "os_version": "17.4.1",
        "referrer": "",
        "referring_domain": ""
    },
    "Web": {
        "os": "Windows",
        "browser": "Discord Web",
        "device": "",
        "system_locale": "en-US",
        "browser_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "browser_version": "124.0.0.0",
        "os_version": "10",
        "referrer": "",
        "referring_domain": ""
    },
    "VR": {
        "os": "Console",
        "browser": "Discord VR",
        "device": "VR-Headset",
        "system_locale": "en-US",
        "browser_user_agent": "DiscordVR/12.45",
        "browser_version": "23.7.91",
        "os_version": "10.0.45",
        "referrer": "",
        "referring_domain": ""
    }
}

class TokenStoreError(Exception):
    pass

def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inb = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(out)):
        raise TokenStoreError("Windows DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

def _secret_tool(*args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    if not shutil.which("secret-tool"):
        raise TokenStoreError("secret-tool (libsecret) not found, so nothing was saved")
    return subprocess.run(["secret-tool", *args], input=stdin, capture_output=True, timeout=15)

def token_save(token: str) -> None:
    if IS_WINDOWS:
        TOKEN_FILE.write_text(base64.b64encode(_dpapi(token.encode(), True)).decode(), encoding="ascii")
        return
    r = _secret_tool("store", "--label=Game Larper", *_KEYRING, stdin=token.encode())
    if r.returncode:
        raise TokenStoreError("the keyring refused the token (is a keyring unlocked?)")

def token_load() -> str | None:
    if IS_WINDOWS:
        if not TOKEN_FILE.exists():
            return None
        return _dpapi(base64.b64decode(TOKEN_FILE.read_text(encoding="ascii")), False).decode()
    r = _secret_tool("lookup", *_KEYRING)
    return r.stdout.decode().strip() or None if r.returncode == 0 else None

def token_forget() -> None:
    if IS_WINDOWS:
        TOKEN_FILE.unlink(missing_ok=True)
    else:
        _secret_tool("clear", *_KEYRING)

_PLATFORM_CTX = contextvars.ContextVar("larper_platform", default="Windows")
_IDENTIFY_PATCHED = False

DEPS_DIR = APP_DIR / "larper_deps"  
_PROBE = ("import discord, sys\nfrom discord.gateway import DiscordWebSocket\n"
          "sys.exit(0 if 'self' in str(getattr(discord, '__title__', '')) else 3)")
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0

def _use_deps_dir() -> None:
    """Put the private deps folder first on sys.path (before discord is ever imported in this process)."""
    d = str(DEPS_DIR)
    if DEPS_DIR.exists() and d not in sys.path:
        sys.path.insert(0, d)
        import importlib
        importlib.invalidate_caches()

def _pip_python() -> str:
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)

def selfbot_missing() -> str | None:
    """None if discord.py-self is usable, else a short reason. Checked in a subprocess so a wrong or broken
    global 'discord' package never gets imported into this process."""
    _use_deps_dir()
    if FROZEN:  
        try:
            import discord
            from discord.gateway import DiscordWebSocket  
            return None if "self" in str(getattr(discord, "__title__", "")) else "wrong 'discord' package"
        except Exception as exc:
            return str(exc)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(x for x in (str(DEPS_DIR) if DEPS_DIR.exists() else "", env.get("PYTHONPATH", "")) if x)
    try:
        r = subprocess.run([_pip_python(), "-c", _PROBE], capture_output=True, text=True, timeout=90, env=env,
                           creationflags=_NOWIN)
    except Exception as exc:
        return f"could not check: {exc}"
    if r.returncode == 0:
        return None
    if r.returncode == 3:
        return "the wrong 'discord' package is first on the path (need discord.py-self)"
    lines = r.stderr.strip().splitlines()
    return lines[-1] if lines else "import failed"

def install_selfbot() -> tuple[bool, str]:
    """pip install discord.py-self into DEPS_DIR (never touches or removes anything in the global Python).
    A broken private folder is wiped and retried once."""
    if FROZEN:
        return False, "this .exe was built without discord.py-self (rebuild it with build_exe.bat)"
    detail = ""
    for attempt in (1, 2):
        try:
            r = subprocess.run([_pip_python(), "-m", "pip", "install", "--upgrade", "--no-input",
                                "--disable-pip-version-check", "--target", str(DEPS_DIR), "discord.py-self"],
                               capture_output=True, text=True, timeout=900, creationflags=_NOWIN)
            out = (r.stderr.strip() or r.stdout.strip()).splitlines()
            detail = out[-1] if out else f"pip exit code {r.returncode}"
        except Exception as exc:
            detail = str(exc)
        _use_deps_dir()
        missing = selfbot_missing()
        if missing is None:
            return True, ""
        detail = f"{detail} | {missing}"
        if attempt == 1:
            shutil.rmtree(DEPS_DIR, ignore_errors=True)  
    return False, detail

def _install_identify_patch() -> None:
    """Make every gateway IDENTIFY use the platform of the current context (as spoofer's spoof.py does)."""
    global _IDENTIFY_PATCHED
    if _IDENTIFY_PATCHED:
        return
    from discord.gateway import DiscordWebSocket

    async def identify(self) -> None:
        await self.send(json.dumps({"op": self.IDENTIFY, "d": {
            "token": self.token, "capabilities": 4093, "properties": GW_PROPS[_PLATFORM_CTX.get()],
            "compress": False,
            "client_state": {"guild_versions": {}, "highest_last_message_id": "0", "read_state_version": 0,
                             "user_guild_settings_version": -1, "user_settings_version": -1,
                             "private_channels_version": "0", "api_code_version": 0}}}))

    DiscordWebSocket.identify = identify
    _IDENTIFY_PATCHED = True

class GatewaySession(threading.Thread):
    """One discord.py-self client reporting one platform and playing the games assigned to it."""

    def __init__(self, token: str, platform: str, log) -> None:
        super().__init__(daemon=True)
        self._token, self.platform, self._log = token, platform, log
        self.games: list[Game] = []
        self.ready = threading.Event()
        self.stop_evt = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None

    def set_games(self, games: list[Game]) -> None:
        self.games = list(games)
        if self._loop and self._client and self.ready.is_set():
            asyncio.run_coroutine_threadsafe(self._apply(), self._loop)

    def finish(self) -> None:
        """Clear the presence, then close the session."""
        self.games = []
        self.stop_evt.set()
        loop, client = self._loop, self._client
        if loop and client:
            async def stop() -> None:
                await self._apply()
                await client.close()
            asyncio.run_coroutine_threadsafe(stop(), loop)

    async def _apply(self) -> None:
        import discord
        acts = [discord.Activity(type=discord.ActivityType.playing, name=g.name,
                                 application_id=int(g.app_id) if g.app_id.isdigit() else None)
                for g in self.games]
        try:
            await self._client.change_presence(activities=acts)
        except Exception as exc:
            self._log(f"[{self.platform}] presence not applied: {exc}")

    async def _main(self) -> None:
        import discord
        sess = self

        class PlatformClient(discord.Client):
            async def on_ready(self) -> None:
                sess.ready.set()
                sess._log(f"[{sess.platform}] session ready")
                if sess.stop_evt.is_set():
                    await self.close()
                    return
                await sess._apply()

        self._loop = asyncio.get_running_loop()
        self._client = PlatformClient()
        try:
            await self._client.start(self._token)
        except discord.LoginFailure:
            self._log(f"FAILED [{self.platform}] token rejected by Discord")
        finally:
            self.ready.clear()

    def run(self) -> None:
        _PLATFORM_CTX.set(self.platform)  
        try:
            _use_deps_dir()
            _install_identify_patch()
            asyncio.run(self._main())
        except Exception as exc:
            self._log(f"FAILED [{self.platform}] {exc}")

MAX_QUEUE_RANDOM = 500
GRAPH_W = 420
SLIDE_MS = 450
SLIDE_HOLD_MS = 1200
SPIN_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"

@dataclass
class Game:
    name: str
    exe: str
    app_id: str = ""

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
        games.append(Game(name=name, exe=chosen, app_id=str(entry.get("id") or "")))
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
        return max(1, self.cv.winfo_height() 

    def _index(self, y: int) -> int:
        i = self.top + y 
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
        i = max(0, min(len(self.items) - 1, self.top + y 
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
            cv.create_text(8, y + self.RH 
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
        cv.create_text(14, y0 + self.RH 
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
        fit = int((right - left) 
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
            prev = int(shown[0][0]) 
            for k, (t, _v) in enumerate(shown):
                cur = int(t) 
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
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) 
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) 
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
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) 
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) 
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
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) 
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) 
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

class PlatformDialog(tk.Toplevel):
    """Platform label for one game. result = (platform, use_for_rest), or None if cancelled."""

    def __init__(self, parent, game: str, idx: int, total: int, initial: str) -> None:
        super().__init__(parent, bg=BG)
        self.title("Platform")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.val = tk.StringVar(value=initial)
        self.rest = tk.BooleanVar(value=False)

        body = tk.Frame(self, bg=BG)
        body.pack(padx=16, pady=14)
        _label(body, f"[{idx}/{total}]  {game}", fg=ACCENT, bold=True, anchor="w").pack(fill="x")
        _label(body, "PLAY ON (STAND-IN MODE NEEDS DISCORD DEBUG PORT, SEE README)", fg=DIM, size=8, anchor="w").pack(
            fill="x", pady=(8, 4))
        for name in PLATFORMS:
            tk.Radiobutton(body, text=name, value=name, variable=self.val, bg=BG, fg=FG, font=mono(),
                           selectcolor=BG, activebackground=BG, activeforeground=ACCENT,
                           highlightthickness=0, anchor="w").pack(fill="x")
        if idx < total:
            label = "USE THIS PLATFORM FOR ALL GAMES" if idx == 1 else "USE THIS PLATFORM FOR ALL REMAINING GAMES"
            ttk.Checkbutton(body, text=label, variable=self.rest, style="Brut.TCheckbutton").pack(
                anchor="w", pady=(10, 0))
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="CANCEL", style="Brut.TButton", command=self.destroy).pack(side="right")
        ttk.Button(row, text="OK ▸", style="Accent.Brut.TButton", command=self._ok).pack(side="right", padx=6)

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) 
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) 
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        dark_titlebar(self)
        self.grab_set()

    def _ok(self) -> None:
        self.result = (self.val.get(), self.rest.get())
        self.destroy()

class TokenDialog(tk.Toplevel):
    """Token entry. result = ("save", token) | ("forget", "") | None."""

    def __init__(self, parent, has_saved: bool) -> None:
        super().__init__(parent, bg=BG)
        self.title("Token")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self._parent = parent
        self.val = tk.StringVar()

        body = tk.Frame(self, bg=BG)
        body.pack(padx=16, pady=14)
        _label(body, "THROWAWAY ACCOUNTS ONLY. A TOKEN IS FULL ACCESS TO THE ACCOUNT.", fg=ACCENT, bold=True,
               anchor="w").pack(fill="x")
        _label(body, "1. IN THE DISCORD CLIENT OPEN DEVTOOLS (CTRL+SHIFT+I) > CONSOLE\n"
                     "2. PASTE THE SNIPPET BELOW, PRESS ENTER (IT COPIES YOUR TOKEN)\n"
                     "3. PASTE THE TOKEN IN THE FIELD AT THE BOTTOM",
               fg=DIM, size=8, anchor="w", justify="left").pack(fill="x", pady=(8, 6))
        box = tk.Text(body, height=4, width=72, wrap="word", bg=PANEL, fg=FG, font=mono(9), relief="flat",
                      borderwidth=0, highlightthickness=0, padx=8, pady=6)
        box.insert("1.0", TOKEN_SNIPPET)
        box.config(state="disabled")
        box.pack(fill="x")
        ttk.Button(body, text="COPY SNIPPET", style="Brut.TButton", command=self._copy).pack(anchor="w", pady=(6, 10))
        ent = tk.Entry(body, textvariable=self.val, show="\u2022", width=72, bg=PANEL, fg=FG, font=mono(10),
                       insertbackground=ACCENT, relief="flat", highlightthickness=1, highlightbackground=LINE,
                       highlightcolor=ACCENT)
        ent.pack(fill="x", ipady=4)
        _label(body, "STORED ENCRYPTED ON THIS DEVICE (WINDOWS DPAPI / LINUX KEYRING). NEVER LOGGED.",
               fg=DIM, size=8, anchor="w").pack(fill="x", pady=(6, 0))
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="CANCEL", style="Brut.TButton", command=self.destroy).pack(side="right")
        ttk.Button(row, text="SAVE ▸", style="Accent.Brut.TButton", command=self._ok).pack(side="right", padx=6)
        if has_saved:
            ttk.Button(row, text="FORGET SAVED TOKEN", style="Brut.TButton", command=self._forget).pack(side="left")

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) 
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) 
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        dark_titlebar(self)
        self.grab_set()
        ent.focus_set()

    def _copy(self) -> None:
        self._parent.clipboard_clear()
        self._parent.clipboard_append(TOKEN_SNIPPET)

    def _ok(self) -> None:
        tok = self.val.get().strip().strip("\"'")
        if len(tok) >= 20 and " " not in tok:
            self.result = ("save", tok)
            self.destroy()

    def _forget(self) -> None:
        self.result = ("forget", "")
        self.destroy()

def center_window(root: tk.Tk, width: int, height: int) -> None:
    """Size the window and place it in the middle of the screen (shrinks to fit small screens)."""
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    width, height = min(width, sw - 40), min(height, sh - 80)
    x = (sw - width) 
    y = (sh - height) 
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
        self.plat_now = "Windows"  
        self.worker: threading.Thread | None = None

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.apply_filter())
        self.random_var = tk.StringVar(value="25")
        self.status_var = tk.StringVar(value="Loading catalog...")
        self.count_var = tk.StringVar(value="")
        self.folder_var = tk.BooleanVar(value=False)
        self.min_var = tk.BooleanVar(value=False)
        self.tok_mode = tk.BooleanVar(value=False)
        self.token: str | None = None
        self._deps_state: str | None = None

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
        _label(head, "◆ DISCORD DETECTABLE CATALOG   ▸ LOCAL STAND-INS   ▸ NO TELEMETRY   ▸ TOKEN OPTIONAL (ENCRYPTED)",
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
        ttk.Checkbutton(opts, text="TOKEN MODE (REAL SESSIONS)", variable=self.tok_mode,
                        style="Brut.TCheckbutton").pack(side="left")
        ttk.Button(opts, text="TOKEN...", style="Brut.TButton", command=self.edit_token).pack(side="left", padx=10)

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
            pct = done * 100 
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
        use_token = False
        if self.tok_mode.get():
            ready = self._token_ready()
            if ready is None:  
                return
            use_token = ready
        skip_plat = self.tok_mode.get() and not use_token  
        batch = list(self.queue)
        durations: list[float | None] = []
        platforms: list[str] = []
        text, plat, minutes = DEFAULT_MINUTES, "Windows", None
        time_all = plat_all = False
        for i, g in enumerate(batch, 1):  
            if not time_all:
                dlg = DurationDialog(self.root, g.name, i, len(batch), text)
                self.root.wait_window(dlg)
                if dlg.result is None:
                    return
                text, minutes, time_all = dlg.result
            durations.append(minutes)
            if not plat_all and not skip_plat:
                pdlg = PlatformDialog(self.root, g.name, i, len(batch), plat)
                self.root.wait_window(pdlg)
                if pdlg.result is None:
                    return
                plat, plat_all = pdlg.result
            platforms.append(plat)

        global MINIMIZE_WINDOWS
        MINIMIZE_WINDOWS = bool(self.min_var.get())
        self.stop_evt.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.state_lbl.config(text="[ ACTIVE ]", fg=GREEN)
        target = self._run_token if use_token else self._run
        self.worker = threading.Thread(target=target, args=(batch, durations, platforms), daemon=True)
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
        cols = max(30, (self.log_box.winfo_width() - 24) 
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

    def edit_token(self) -> None:
        try:
            has = token_load() is not None
        except Exception:
            has = False
        dlg = TokenDialog(self.root, has)
        self.root.wait_window(dlg)
        if not dlg.result:
            return
        kind, tok = dlg.result
        if kind == "forget":
            try:
                token_forget()
            except Exception as exc:
                show_error(self.root, "Token", str(exc))
            self.token = None
            self.status_var.set("Saved token removed.")
            return
        self.token = tok
        try:
            token_save(tok)
            self.status_var.set("Token saved, encrypted for this user.")
        except Exception as exc:
            show_error(self.root, "Token not saved", f"{exc}\n\nIt will be used for this session only.")

    def _token_ready(self) -> bool | None:
        """True: token mode can run. False: skip platform larp (reason is logged).
        None: dependencies are being installed; start() is called again when that finishes."""
        missing = selfbot_missing()
        if missing:
            if self._deps_state == "failed":
                self._deps_state = None  
                self.log(f"dependencies still missing ({missing}) | skipping platform larp")
                return False
            self._begin_install(missing)
            return None
        if not self.token:
            try:
                self.token = token_load()
            except Exception as exc:
                self.log(f"saved token not readable: {exc}")
        if not self.token:
            self.log("no token provided (add one with TOKEN...) | skipping platform larp")
            return False
        return True

    def _begin_install(self, missing: str) -> None:
        self.start_btn.config(state="disabled")
        self.status_var.set("Installing discord.py-self (one time)...")
        self.log(f"dependencies not found ({missing}). Installing discord.py-self into {DEPS_DIR.name}/ "
                 "(one time; your global Python is not touched)")

        def work() -> None:
            ok, detail = install_selfbot()

            def done() -> None:
                self.start_btn.config(state="normal")
                if ok:
                    self._deps_state = None
                    self.log("discord.py-self installed")
                else:
                    self._deps_state = "failed"
                    self.log(f"FAILED auto-install: {detail}")
                self.start()
            self.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _run_token(self, batch: list[Game], durations: list[float | None], platforms: list[str]) -> None:
        sessions: dict[str, GatewaySession] = {}

        def sess(plat: str) -> GatewaySession:
            s = sessions.get(plat)
            if s is None:
                s = sessions[plat] = GatewaySession(self.token or "", plat, self.log)
                s.start()
                s.ready.wait(15)
                if not s.ready.is_set():
                    self.log(f"[{plat}] not ready (bad token, or Discord refused the session)")
            return s

        try:
            if all(d is None for d in durations):
                groups: dict[str, list[Game]] = {}
                for g, p in zip(batch, platforms):
                    groups.setdefault(p, []).append(g)
                for p, gs in groups.items():
                    sess(p).set_games(gs)
                    self.log(f"[{p}] playing " + ", ".join(g.name for g in gs))
                self._set_status(f"Running {len(batch)} game(s) on {len(groups)} platform session(s) until you press Stop.")
                while not self.stop_evt.is_set() and any(s.is_alive() for s in sessions.values()):
                    n = sum(s.ready.is_set() for s in sessions.values())
                    self.live(f"{len(batch)} games", f"{n}/{len(sessions)} sessions --%")
                    self.stop_evt.wait(0.5)
            else:
                total = len(batch)
                for i, g in enumerate(batch, 1):
                    if self.stop_evt.is_set():
                        break
                    s = sess(platforms[i - 1])
                    s.set_games([g])
                    self.log(f"[{platforms[i - 1]}] playing {g.name}")
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
                        if not s.is_alive():
                            self.log(f"{g.name} session ended early")
                            break
                        pct = "--" if end is None else f"{min(99, int((1 - left / (minutes * 60)) * 100)):3d}"
                        self.live(g.name, f"{i}/{total} {pct}%")
                        self._set_status(f"[{i}/{total}] {g.name} on {platforms[i - 1]}  {left_txt}")
                        self.stop_evt.wait(1.0)
                    self.end_live()
                    s.set_games([])
                    self.log(f"stopped {g.name}")
                    if i < total and not self.stop_evt.is_set():
                        self.stop_evt.wait(GAP_SECONDS)
        finally:
            self.end_live()
            for s in sessions.values():
                s.finish()
            for s in sessions.values():
                s.join(4)
            self.root.after(0, self._finished)

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

    def _platform(self, name: str) -> None:
        """Switch the platform Discord reports. Skipped if already set; a failure never stops the run."""
        if name == self.plat_now:
            return
        try:
            set_platform(name)
        except Exception as exc:
            self.log(f"platform {name} not applied: {exc} (start Discord with --remote-debugging-port={DEBUG_PORT})")
            return
        self.plat_now = name
        self.log(f"platform -> {name}")
        self.stop_evt.wait(GAP_SECONDS)  

    def _run(self, batch: list[Game], durations: list[float | None], platforms: list[str]) -> None:
        try:
            if all(d is None for d in durations):
                if len(set(platforms)) > 1:  
                    self.log(f"all games run together, using {platforms[0]} for all")
                self._platform(platforms[0])
                started =[f for g in batch for f in self._launch_all(g)]
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
                    self._platform(platforms[i - 1])
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
            self._platform("Windows")  
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
