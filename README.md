<div align="center">

<img src="https://raw.githubusercontent.com/ItzMeShadow999/My-assets/main/Game-Larper.png" alt="Game Larper" width="150">

# Game Larper

**Pick games from Discord's detectable catalog, set how long each one "runs", and let Discord's own game detection do the rest.**

<p>
  <img src="https://img.shields.io/badge/Python-3.9+-E61919?style=flat-square&labelColor=0A0A0A&logo=python&logoColor=EAEAEA" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-E61919?style=flat-square&labelColor=0A0A0A" alt="Windows | Linux">
  <img src="https://img.shields.io/badge/Dependencies-none-4AF626?style=flat-square&labelColor=0A0A0A" alt="No dependencies">
  <img src="https://img.shields.io/badge/Single%20file-yes-E61919?style=flat-square&labelColor=0A0A0A" alt="Single file">
  <a href="https://github.com/ItzMeShadow999/Game-Larper/stargazers"><img src="https://img.shields.io/github/stars/ItzMeShadow999/Game-Larper?style=flat-square&labelColor=0A0A0A&color=E61919" alt="Stars"></a>
</p>

<p>
  <img src="https://img.shields.io/badge/Local%20stand--ins-0A0A0A?style=flat-square&labelColor=121212&color=3A3A3A" alt="Local stand-ins">
  <img src="https://img.shields.io/badge/No%20token-0A0A0A?style=flat-square&labelColor=121212&color=3A3A3A" alt="No token">
  <img src="https://img.shields.io/badge/No%20telemetry-0A0A0A?style=flat-square&labelColor=121212&color=3A3A3A" alt="No telemetry">
  <img src="https://img.shields.io/badge/10%2C000%2B%20games-0A0A0A?style=flat-square&labelColor=121212&color=3A3A3A" alt="10,000+ games">
</p>

<p>
  <img src="https://img.shields.io/badge/Rank-Certified%20Larper-E61919?style=flat-square&labelColor=0A0A0A" alt="Rank: Certified Larper">
  <img src="https://img.shields.io/badge/Hours%20played-definitely%20real-4AF626?style=flat-square&labelColor=0A0A0A" alt="Hours played: definitely real">
  <img src="https://img.shields.io/badge/Skill-unverified-E61919?style=flat-square&labelColor=0A0A0A" alt="Skill: unverified">
  <img src="https://img.shields.io/badge/Backlog-infinite-E61919?style=flat-square&labelColor=0A0A0A" alt="Backlog: infinite">
</p>

<a href="https://github.com/ItzMeShadow999/Game-Larper/releases/latest"><img src="https://img.shields.io/badge/Download-GameLarper.exe-E61919?style=for-the-badge&labelColor=0A0A0A" alt="Download GameLarper.exe"></a>

*Ten thousand+ games. One PC.*

</div>

<br>

## 01 / Features

-  Pulls the public detectable games catalog from Discord (10,000+ entries) and caches it as `games.json`.
-  Search the catalog, then add selected games, add all, or add a random batch.
-  Queue with remove, clear and shuffle.
-  Per-game run time in minutes, an "apply to all remaining" option, or leave it empty to run until you press Stop.
-  Starts a harmless stand-in process named like the game's real executable (for example `hades.exe`), so Discord detects it locally.
-  Custom path support for `Game Name\Game.exe` style detection, plus an optional path variant for every queued game.
-  Optional minimized stand-in windows, and the option to use your own exe as the stand-in.
-  Live log and a transfer speed graph for the catalog download.
-  Stand-ins are deleted again when a run stops or the app closes.
-  Dark brutalist UI, nothing to install, no account details needed.

## 02 / Download

Grab `GameLarper.exe` from the [latest release](https://github.com/ItzMeShadow999/Game-Larper/releases/latest) and run it. Windows SmartScreen may warn on first launch because the exe is not code signed.

## 03 / Run from source

```bash
python GameLarper.py
```

Python 3.9+ with tkinter. Only the standard library is used.

## 04 / Usage

1. Wait for the catalog to load (or press **Refresh**).
2. Search, then double-click a game or use **Add Selected**, **Add All** or **Add Random**.
3. Press **Start** and enter the minutes to run for each game.
4. Open Discord. Each game shows up as playing while it runs.
5. Press **Stop** when you are done, or let the timers finish.

## 05 / Files

| File | What it is |
| --- | --- |
| `GameLarper.py` | The whole app |
| `icons/` | Window and stub icons |
| `.github/workflows/release.yml` | Builds the exe and publishes the release |

Created next to the app while it runs:

| File | What it is |
| --- | --- |
| `games.json` | Cached Discord catalog |
| `larper.log` | Activity log and crash traces |
| `larper_window_stub_v4.exe` | Stand-in compiled once with the .NET compiler built into Windows |

Stand-ins are created in a temporary `larp_*` folder inside your Downloads folder and removed when they stop.

## 06 / Notes

-  Nothing is sent to Discord by this app. It only downloads the public catalog and starts local processes.
-  If the stand-in cannot be compiled, Windows system binaries such as `ping.exe` are copied and renamed instead.
-  On Linux a renamed copy of `sleep` is used.
-  Closing the app while games are running asks before it stops them.

## 07 / Rules of the Larp

1.  Never say you are larping. You have been "grinding" all week.
2.  Always pick a game you could plausibly own. Nobody believes a 3 hour session of a game that does not exist.
3.  Vary your library. A real player has a backlog, a comfort game and one embarrassing favorite.
4.  If someone asks for tips on the game, change the subject.

## 08 / Loadout

Every larper needs gear. The app tries your custom exe first (if set), then the compiled stub, then the system binaries, and uses the first one that works.

| Slot | Item | Notes |
| --- | --- | --- |
| Custom | Your own exe | Set it with **Choose Stub Exe**, it gets copied and renamed per game |
| Primary | `larper_window_stub_v4.exe` | Tiny window app compiled once on your PC with the .NET compiler built into Windows |
| Backup | `ping.exe`, `findstr.exe`, `sort.exe`, `find.exe` | Built in Windows binaries used when the primary cannot be built |
| Linux | `sleep infinity` | Renamed copy of the system `sleep` |
| Disguise | `Game Name\Game.exe` | Custom paths and the optional path variant for stricter detection |
| Camouflage | **Minimize Windows** | Stand-in windows start minimized so your taskbar stays quiet |

## 09 / Campaigns

Pick a playstyle and set the minutes in the duration prompt.

| Campaign | Setup | Vibe |
| --- | --- | --- |
| The Marathon | Add one game, leave minutes empty, press Start | Runs until you press **Stop**. Absolute dedication |
| The Weekend Warrior | Add 5 games, set 60 minutes, tick "use this time for all" | A healthy rotation, one after another |
| The Completionist | **Add All**, set 1 minute for all | Touch every game in the catalog. Very long run |
| The Gambler | **Add Random** with 25, then **Shuffle** | Let fate pick your main |
| The Specialist | **Add Custom Path** and type the exact `Folder\Game.exe` | For games that need a very specific path |

The app waits a few seconds between games, so the switch from one title to the next does not look robotic.

## 10 / FAQ

**Is this cheating?**
It is roleplay. Nobody has ever lost a ranked match to a stand-in process.

**Do I need to log in or give a token?**
No. The app never asks for your account, and it sends nothing to Discord.

**How does Discord know I am "playing"?**
Discord scans running processes on your PC and matches their names against its public detectable games list. The app starts a harmless process with a matching name. Discord does the rest.

**Where did all these `larp_*` folders come from?**
Each stand-in lives in its own temporary folder inside Downloads. They are deleted when the run stops. If the app is killed hard, you can delete leftovers yourself.

**Can I run it forever?**
Leave minutes empty and press Start. It runs until you press **Stop**.

**My game is not in the list.**
Use **Add Custom Path** and type the path ending Discord should see, for example `Cyberpunk 2077\Cyberpunk2077.exe`.

## 11 / Disclaimer

Game Larper fakes game activity on your own machine. Use it at your own risk. It is not affiliated with or endorsed by Discord.

<div align="center">

<sub>Made by <a href="https://github.com/ItzMeShadow999">ItzMeShadow999</a></sub>

</div>
