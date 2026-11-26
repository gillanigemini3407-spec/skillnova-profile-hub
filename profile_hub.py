#!/usr/bin/env python3
"""SkillNova Profile Hub v0.1 — lightweight Chrome profile + app launcher.

One small local web app. No pip installs, standard library only.
No background service, no server left running after exit.

  python3 profile_hub.py          # http://127.0.0.1:8000
  python3 profile_hub.py --port 8080
  python3 launcher.py             # double-click entry, opens browser

What it does:
  * Auto-discovers Chrome profiles from the Chrome User Data folder
    (Windows: %LOCALAPPDATA%\\Google\\Chrome\\User Data,
     Linux:   ~/.config/google-chrome).
  * Lets you pin chosen profiles (and other apps) into custom groups,
    rename them (hub-only names — Chrome's own names are never touched),
    reorder them, colour them, and launch them with one click.
  * Before launching a profile it sets session.restore_on_startup = 1
    in that profile's Preferences (edit-then-launch), so Chrome reopens
    the tabs where you left them. Nothing else in Chrome's files is
    ever written.

Environment overrides (for testing):
  PROFILE_HUB_CHROME_DIR   Chrome User Data folder to scan.
  PROFILE_HUB_DRY_RUN=1    Launch dry-run: do the restore edit, but
                           do not spawn any process — return the exact
                           command that would have run instead.
  PROFILE_HUB_CONFIG       Config file path (default: config.json next
                           to this file).

Endpoints:
  GET  /                       UI
  GET  /api/profiles           discovered Chrome profiles (+ skipped)
  GET  /api/restore-status     per-profile restore_on_startup flag
  GET  /api/config             saved groups/items/view
  POST /api/config             save groups/items/view (JSON body)
  POST /api/launch             {"kind": "profile"|"app", "id": ...}
                               launches the item (or dry-runs it)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.1"

BASE = Path(os.environ.get("PROFILE_HUB_BASE") or
            Path(__file__).resolve().parent)


def _persistent_config_path() -> Path:
    """Stable per-user config home, outside the portable folder.

    Updates replace the portable folder, so the saved profiles and
    groups must not live only beside the exe. Windows keeps them in
    %LOCALAPPDATA%\\SkillNovaProfileHub; Linux in ~/.local/share.
    """
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "SkillNovaProfileHub" / "config.json"
    return Path.home() / ".local" / "share" / "skillnova-profile-hub" / "config.json"


_CONFIG_OVERRIDE = os.environ.get("PROFILE_HUB_CONFIG")
CONFIG_PATH = Path(_CONFIG_OVERRIDE) if _CONFIG_OVERRIDE else _persistent_config_path()
LEGACY_CONFIG_PATH = None if _CONFIG_OVERRIDE else (BASE / "config.json")

DEFAULT_COLORS = ["#1877F2", "#FF0000", "#25F4EE", "#34A853",
                  "#E1306C", "#F59E0B", "#8B5CF6", "#14B8A6"]


# ---------------------------------------------------------------- discovery

def chrome_user_data_dir() -> Path:
    """Where Chrome keeps its profiles on this machine."""
    override = os.environ.get("PROFILE_HUB_CHROME_DIR")
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or ""
        return Path(local) / "Google" / "Chrome" / "User Data"
    return Path.home() / ".config" / "google-chrome"


def _read_preferences(profile_path: Path):
    """Return (preferences dict, error string or None)."""
    prefs_file = profile_path / "Preferences"
    if not prefs_file.is_file():
        return None, "no Preferences file"
    try:
        data = json.loads(prefs_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"unreadable Preferences: {exc}"
    if not isinstance(data, dict):
        return None, "Preferences is not a JSON object"
    return data, None


def _is_profile_dir(name: str) -> bool:
    return name == "Default" or (name.startswith("Profile ")
                                  and name[8:].isdigit())


def _read_info_cache(user_data_dir: Path) -> dict:
    """Chrome's own profile list from Local State (profile.info_cache)."""
    try:
        data = json.loads((user_data_dir / "Local State")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cache = ((data.get("profile") or {}).get("info_cache") or {})
    return cache if isinstance(cache, dict) else {}


_GENERIC_NAMES = ("Your Chrome", "Guest Profile", "System Profile")


def _is_generic_name(name) -> bool:
    if not isinstance(name, str) or not name.strip():
        return True
    if name in _GENERIC_NAMES:
        return True
    if name.startswith("Person ") and name[7:].isdigit():
        return True
    return False


def _clean_name(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _account_info(prefs, info=None) -> tuple:
    """(full name, email) of the account on this profile, best effort.

    Only a real full name counts here. Chrome also stores username-like
    labels (for example "shahtiktokpk") for profiles that are not
    signed in to Chrome; those must not replace the exact Chrome
    profile name (for example "Noor BM").
    """
    full_name, email = "", ""
    accounts = prefs.get("account_info") if isinstance(prefs, dict) else None
    if isinstance(accounts, list) and accounts:
        first = accounts[0] if isinstance(accounts[0], dict) else {}
        full_name = _clean_name(first.get("full_name"))
        email = _clean_name(first.get("email"))
    profile = (prefs.get("profile") or {}) if isinstance(prefs, dict) else {}
    if not email:
        email = _clean_name(profile.get("user_name"))
    if isinstance(info, dict):
        if not full_name:
            full_name = (_clean_name(info.get("gaia_name"))
                         or _clean_name(info.get("full_name")))
        if not email:
            email = _clean_name(info.get("user_name"))
    return full_name, email


def _profile_name(prefs, info, dir_name) -> str:
    """The local Chrome profile name, kept for the subline."""
    profile_name = ((prefs.get("profile") or {}).get("name")
                    if isinstance(prefs, dict) else None)
    info_name = info.get("name") if isinstance(info, dict) else None
    return _clean_name(profile_name) or _clean_name(info_name) or dir_name


def _is_username_like(name: str, email: str) -> bool:
    if not name or not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].strip().lower()
    compact = "".join(ch for ch in name.lower() if ch.isalnum())
    local_compact = "".join(ch for ch in local if ch.isalnum())
    return bool(compact and compact == local_compact)


def _signed_in_signal(info) -> tuple:
    """(has_signal, signed_in) from Local State profile.info_cache."""
    if not isinstance(info, dict) or not info:
        return False, False
    has_signal = any(key in info for key in (
        "gaia_id", "gaia_name", "is_consented_primary_account",
        "user_name"))
    signed_in = bool(_clean_name(info.get("gaia_id"))) or (
        info.get("is_consented_primary_account") is True)
    return has_signal, signed_in


def _display_name(prefs, info, dir_name) -> str:
    """The exact name Chrome shows for this profile.

    Chrome shows the signed-in account's full name for signed-in
    profiles (for example "Shah • Gillani"). For profiles that are
    not signed in to Chrome, Chrome shows the local profile name
    exactly (for example "Noor BM"), even when an account username
    such as "shahtiktokpk" is stored on the profile.
    """
    full_name, email = _account_info(prefs, info)
    profile_name = _profile_name(prefs, info, dir_name)
    account_real = (bool(full_name) and not _is_generic_name(full_name)
                    and not _is_username_like(full_name, email))
    has_signal, signed_in = _signed_in_signal(info)
    if signed_in and account_real:
        return full_name
    if profile_name and not _is_generic_name(profile_name):
        if not has_signal and account_real:
            return full_name
        return profile_name
    if account_real:
        return full_name
    if full_name and not _is_generic_name(full_name):
        return full_name
    if email:
        return email
    return profile_name or dir_name


def _profile_sort_key(dir_name: str):
    if dir_name == "Default":
        return (0, 0, "")
    if dir_name.startswith("Profile ") and dir_name[8:].isdigit():
        return (1, int(dir_name[8:]), "")
    return (2, 0, dir_name)


def _lockfile_locked(lock_path: Path) -> bool:
    """True when Chrome actually holds the Windows profile lockfile.

    The lockfile can stay behind after Chrome exits, so its mere
    existence does not mean the profile is running — treating a
    stale lockfile as "running" would skip the restore edit forever
    and tabs would never restore. On Windows, test the lock itself:
    if we can lock the first byte, Chrome is not holding it.
    """
    try:
        import msvcrt
    except ImportError:
        return lock_path.is_file()
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return True  # cannot even open it — treat as in use
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        return True
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    os.close(fd)
    return False


def _profile_running(profile_path: Path) -> bool:
    """True when Chrome is using this profile right now.

    Chrome marks a live profile with a lock file. On Linux/Mac it is
    a SingletonLock symlink whose target does not exist as a path
    (e.g. "hostname-12345"), so Path.exists() is False for it — use
    lexists(), which is True for the link itself. On Windows Chrome
    locks a plain "lockfile" in the profile folder; the file can
    remain after exit, so the lock itself is tested, not existence.
    Getting this wrong either way breaks tab restore: editing
    Preferences while Chrome owns them gets overwritten on exit,
    and skipping the edit for a closed profile leaves restore off.
    """
    if os.path.lexists(profile_path / "SingletonLock"):
        return True
    lock_path = profile_path / "lockfile"
    return lock_path.is_file() and _lockfile_locked(lock_path)


def discover_profiles(user_data_dir=None):
    """Scan the Chrome User Data folder.

    Returns {"profiles": [...], "skipped": [...], "user_data_dir": str}.
    A profile counts when its Preferences JSON parses; the profile name
    comes from Preferences -> profile -> name. Folders without a
    readable Preferences are listed in "skipped", never a crash.
    """
    user_data_dir = Path(user_data_dir or chrome_user_data_dir())
    profiles, skipped = [], []
    if not user_data_dir.is_dir():
        return {"profiles": profiles,
                "skipped": [{"dir": str(user_data_dir),
                             "reason": "Chrome User Data folder not found"}],
                "user_data_dir": str(user_data_dir)}
    info_cache = _read_info_cache(user_data_dir)
    candidates = {}
    for entry in user_data_dir.iterdir():
        if entry.is_dir() and _is_profile_dir(entry.name):
            candidates[entry.name] = entry
    for dir_name in info_cache:
        if dir_name in ("Guest Profile", "System Profile"):
            continue
        path = user_data_dir / dir_name
        if path.is_dir():
            candidates.setdefault(dir_name, path)
    for dir_name in sorted(candidates, key=_profile_sort_key):
        entry = candidates[dir_name]
        info = info_cache.get(dir_name) or {}
        prefs, err = _read_preferences(entry)
        if prefs is None:
            # Still show profiles Chrome knows about (Local State),
            # even when Preferences cannot be read right now.
            full_name, email = _account_info({}, info)
            if info.get("name") or full_name or email:
                profiles.append({
                    "dir": dir_name,
                    "name": _display_name({}, info, dir_name),
                    "profile_name": _profile_name({}, info, dir_name),
                    "account_name": full_name,
                    "email": email,
                    "restore_on_startup": None,
                    # Hub launches force last-session restore.
                    "restore_on": True,
                    "running": _profile_running(entry),
                })
                continue
            skipped.append({"dir": dir_name, "reason": err})
            print(f"[profile-hub] skip {dir_name}: {err}", flush=True)
            continue
        full_name, email = _account_info(prefs, info)
        session = prefs.get("session") or {}
        restore = session.get("restore_on_startup")
        profiles.append({
            "dir": dir_name,
            "name": _display_name(prefs, info, dir_name),
            "profile_name": _profile_name(prefs, info, dir_name),
            "account_name": full_name,
            "email": email,
            "restore_on_startup": restore,
            # Effective hub behaviour: launches force last-session
            # restore, so tabs come back even if the raw Chrome
            # startup preference is off.
            "restore_on": True,
            "running": _profile_running(entry),
        })
    return {"profiles": profiles, "skipped": skipped,
            "user_data_dir": str(user_data_dir)}


def restore_status(user_data_dir=None):
    """Per-profile restore_on_startup flag, nothing else."""
    found = discover_profiles(user_data_dir)
    return {"profiles": [
        {"dir": p["dir"], "name": p["name"],
         "restore_on_startup": p["restore_on_startup"],
         "restore_on": p["restore_on"], "running": p["running"]}
        for p in found["profiles"]],
        "skipped": found["skipped"],
        "user_data_dir": found["user_data_dir"]}


# ------------------------------------------------------- restore-on-startup

def ensure_restore_on_startup(profile_dir_name: str, user_data_dir=None,
                              value=1):
    """Set session.restore_on_startup in one profile's Preferences.

    value=1: Chrome restores the last session itself.
    value=5: Chrome opens the new-tab page and does NOT restore —
    used when the hub opens the saved session URLs directly, so the
    two restore paths never run together and duplicate every tab.

    Returns {"ok": bool, "changed": bool, "reason": str}.
    Skipped (ok=True, changed=False) when the profile is currently
    running — Chrome would overwrite the file on exit anyway.
    Nothing else in the file is touched.
    """
    user_data_dir = Path(user_data_dir or chrome_user_data_dir())
    profile_path = user_data_dir / profile_dir_name
    if _profile_running(profile_path):
        return {"ok": True, "changed": False,
                "reason": "profile running — edit skipped, Chrome owns the file"}
    prefs, err = _read_preferences(profile_path)
    if prefs is None:
        return {"ok": False, "changed": False, "reason": err}
    session = prefs.get("session")
    if not isinstance(session, dict):
        session = {}
        prefs["session"] = session
    if session.get("restore_on_startup") == value:
        return {"ok": True, "changed": False,
                "reason": f"restore_on_startup already {value}"}
    session["restore_on_startup"] = value
    try:
        (profile_path / "Preferences").write_text(
            json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "changed": False, "reason": str(exc)}
    return {"ok": True, "changed": True,
            "reason": f"restore_on_startup set to {value}"}


# ------------------------------------------------------------------- config

def default_config():
    return {"version": VERSION, "view": "group", "groups": [], "items": []}


def _normalize_config(cfg):
    """Keep config shape sane and every item id unique.

    A rename must always hit the exact item the user opened. If an
    old config ever carried a missing or duplicated item id, repair
    it here instead of letting Edit rename the wrong profile. The
    hub-only custom_name is preserved exactly as saved; Chrome's own
    profile name is never read or written by this function.
    """
    if not isinstance(cfg.get("groups"), list):
        cfg["groups"] = []
    if not isinstance(cfg.get("items"), list):
        cfg["items"] = []
    seen = set()
    for item in cfg["items"]:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id or item_id in seen:
            item_id = new_id()
            item["id"] = item_id
        seen.add(item_id)
        if not isinstance(item.get("custom_name"), str):
            item["custom_name"] = ""
        if not isinstance(item.get("group_ids"), list):
            item["group_ids"] = []
    return cfg


def _read_config_file(path):
    if path is None or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    cfg = default_config()
    cfg.update({k: v for k, v in data.items() if k in cfg})
    return _normalize_config(cfg)


def _config_has_data(cfg) -> bool:
    return bool(cfg and (cfg.get("items") or cfg.get("groups")))


def load_config():
    cfg = _read_config_file(CONFIG_PATH)
    if cfg is None:
        legacy = _read_config_file(LEGACY_CONFIG_PATH)
        if legacy is not None:
            # First run of the persistent-config build: carry the old
            # beside-the-exe config over so profiles/groups survive.
            save_config(legacy)
            return legacy
        return default_config()
    if not _config_has_data(cfg):
        legacy = _read_config_file(LEGACY_CONFIG_PATH)
        if _config_has_data(legacy):
            save_config(legacy)
            return legacy
    return cfg


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    return cfg


def new_id():
    return uuid.uuid4().hex[:12]


# ------------------------------------------------------------------- launch

def find_chrome():
    """Chrome executable on this machine, or None."""
    if sys.platform.startswith("win"):
        candidates = []
        for base in (os.environ.get("ProgramFiles"),
                     os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates.append(
                    Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
        for cand in candidates:
            if cand.is_file():
                return str(cand)
        return shutil.which("chrome") or shutil.which("chrome.exe")
    for name in ("google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _snss_current_tab_urls(raw: bytes, limit=50):
    """Exact current tab URLs from one SNSS session file.

    Replays Chrome's session commands: each open tab contributes
    ONLY its currently selected navigation URL — never its
    back/forward history entries, never closed tabs or windows,
    and never duplicates. Quantity is preserved: two tabs showing
    the same URL restore as two tabs.
    """
    if len(raw) < 8 or raw[:4] != b"SNSS":
        return []

    def u32(buf, off):
        if off + 4 > len(buf):
            return None
        return int.from_bytes(buf[off:off + 4], "little")

    def pickle_string(buf, off):
        length = u32(buf, off)
        if length is None or length > 100000 or off + 4 + length > len(buf):
            return None
        return buf[off + 4:off + 4 + length].decode("utf-8", "ignore")

    tabs = {}
    closed_windows = set()

    def tab(tab_id):
        return tabs.setdefault(tab_id, {"window": None, "index": 0,
                                        "navs": {}, "selected": None,
                                        "closed": False})

    pos = 8
    while pos + 3 <= len(raw):
        size = int.from_bytes(raw[pos:pos + 2], "little")
        if size < 1 or pos + 2 + size > len(raw):
            break
        cmd_id = raw[pos + 2]
        payload = raw[pos + 3:pos + 2 + size]
        pos += 2 + size
        try:
            if cmd_id == 0:  # SetTabWindow: window_id, tab_id
                window_id, tab_id = u32(payload, 0), u32(payload, 4)
                if tab_id is not None:
                    tab(tab_id)["window"] = window_id
            elif cmd_id == 2:  # SetTabIndexInWindow: tab_id, index
                tab_id, index = u32(payload, 0), u32(payload, 4)
                if tab_id is not None and index is not None:
                    tab(tab_id)["index"] = index
            elif cmd_id == 6:  # UpdateTabNavigation (pickle)
                tab_id, hist_idx = u32(payload, 4), u32(payload, 8)
                url = pickle_string(payload, 12)
                if tab_id is not None and hist_idx is not None and url:
                    tab(tab_id)["navs"][hist_idx] = url
            elif cmd_id == 7:  # SetSelectedNavigationIndex
                tab_id, hist_idx = u32(payload, 0), u32(payload, 4)
                if tab_id is not None and hist_idx is not None:
                    tab(tab_id)["selected"] = hist_idx
            elif cmd_id == 16:  # TabClosed: tab_id
                tab_id = u32(payload, 0)
                if tab_id is not None:
                    tab(tab_id)["closed"] = True
            elif cmd_id == 17:  # WindowClosed: window_id
                window_id = u32(payload, 0)
                if window_id is not None:
                    closed_windows.add(window_id)
        except Exception:
            continue

    open_tabs = []
    for entry in tabs.values():
        if entry["closed"] or entry["window"] in closed_windows:
            continue
        navs = entry["navs"]
        if not navs:
            continue
        selected = entry["selected"]
        url = navs.get(selected) if selected in navs else navs[max(navs)]
        if url.startswith(("http://", "https://")):
            open_tabs.append((entry["window"] or 0, entry["index"], url))
    open_tabs.sort(key=lambda item: (item[0], item[1]))
    return [url for _, _, url in open_tabs[:limit]]


def _session_urls(profile_path: Path, limit=50):
    """Exact open-tab URLs from Chrome's saved session file.

    Parses the newest <profile>/Sessions/Session_* file (older
    builds: "Last Session" / "Current Session" in the profile
    root) as SNSS and returns one URL per tab that was open at
    the end of the last session — no history entries, no closed
    tabs, no duplicates. Returns [] when nothing parseable is
    found, so the caller falls back to Chrome's own restore.
    """
    candidates = []
    sessions_dir = profile_path / "Sessions"
    if sessions_dir.is_dir():
        candidates += [p for p in sessions_dir.iterdir()
                       if p.is_file() and p.name.startswith("Session_")]
    for legacy in ("Last Session", "Current Session"):
        path = profile_path / legacy
        if path.is_file():
            candidates.append(path)
    if not candidates:
        return []
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    try:
        raw = candidates[0].read_bytes()
    except OSError:
        return []
    return _snss_current_tab_urls(raw, limit)


def build_chrome_command(chrome_exe, profile_dir_name, urls=None):
    # --restore-last-session overrides Chrome's startup preference for
    # this launch, so the profile's open tabs come back automatically
    # even if the Preferences edit was ignored or overwritten.
    cmd = [chrome_exe, f"--profile-directory={profile_dir_name}"]
    if urls:
        # Hub-managed restore: open the saved tab URLs directly.
        # launch_profile sets restore_on_startup=5 for this launch
        # so Chrome does not also restore the same session and
        # duplicate every tab.
        cmd += urls
    else:
        cmd.append("--restore-last-session")
    return cmd


def launch_profile(profile_dir_name, user_data_dir=None):
    """Edit-then-launch one Chrome profile. Returns a result dict."""
    chrome_exe = find_chrome()
    profile_path = (Path(user_data_dir or chrome_user_data_dir())
                    / profile_dir_name)
    # Hub-managed restore: for a closed profile, open the saved
    # session URLs directly so old tabs come back on reopen without
    # depending on Chrome's own restore. Exactly ONE restore path
    # may run: when the hub opens the URLs, Chrome's own startup
    # restore is switched off (restore_on_startup=5), otherwise
    # Chrome restores the same session too and every tab doubles
    # on each reopen (1 -> 2 -> 4 -> 8). For a profile already
    # loaded in a running Chrome, keep restore_on_startup=1 and
    # the restore flag (focuses/restores without duplicating tabs
    # in an already-open window).
    urls = []
    if not _profile_running(profile_path):
        urls = _session_urls(profile_path)
    edit = ensure_restore_on_startup(profile_dir_name, user_data_dir,
                                     value=5 if urls else 1)
    # The launch flag forces session restore even if the Preferences
    # edit could not be applied, so a failed edit must not block the
    # launch. The edit result still rides along for transparency.
    if chrome_exe is None:
        return {"ok": False,
                "error": "Chrome not found on this machine.",
                "command": None, "restore_edit": edit}
    cmd = build_chrome_command(chrome_exe, profile_dir_name, urls)
    if os.environ.get("PROFILE_HUB_DRY_RUN") == "1":
        return {"ok": True, "dry_run": True, "command": cmd,
                "restore_edit": edit}
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(cmd, close_fds=True)
        else:
            subprocess.Popen(cmd, start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": cmd,
                "restore_edit": edit}
    return {"ok": True, "dry_run": False, "command": cmd,
            "restore_edit": edit}


def launch_app(app_path):
    """Open another application (exe / shortcut / script)."""
    path = Path(app_path)
    if os.environ.get("PROFILE_HUB_DRY_RUN") == "1":
        return {"ok": True, "dry_run": True, "command": [str(path)]}
    try:
        if sys.platform.startswith("win") and hasattr(os, "startfile"):
            os.startfile(str(path))  # handles .exe and .lnk shortcuts
            return {"ok": True, "dry_run": False, "command": [str(path)]}
        subprocess.Popen([str(path)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": [str(path)]}
    return {"ok": True, "dry_run": False, "command": [str(path)]}


# -------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console quiet
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Never let the browser cache the UI: after index.html is
        # replaced, a refresh must load the new page, not a stale one.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_file(BASE / "index.html", "text/html; charset=utf-8")
            return
        if self.path == "/api/profiles":
            self._send_json(discover_profiles())
            return
        if self.path == "/api/restore-status":
            self._send_json(restore_status())
            return
        if self.path == "/api/config":
            self._send_json(load_config())
            return
        if self.path == "/api/colors":
            self._send_json({"colors": DEFAULT_COLORS})
            return
        self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "Bad JSON."}, 400)
            return
        if not isinstance(data, dict):
            self._send_json({"error": "Bad JSON."}, 400)
            return
        if self.path == "/api/config":
            cfg = default_config()
            if isinstance(data.get("view"), str) and data["view"] in ("group", "list"):
                cfg["view"] = data["view"]
            if isinstance(data.get("groups"), list):
                cfg["groups"] = data["groups"]
            if isinstance(data.get("items"), list):
                cfg["items"] = data["items"]
            cfg = _normalize_config(cfg)
            save_config(cfg)
            self._send_json(cfg)
            return
        if self.path == "/api/launch":
            kind, item_id = data.get("kind"), data.get("id")
            cfg = load_config()
            item = next((i for i in cfg["items"] if i.get("id") == item_id), None)
            if item is None or item.get("kind") != kind:
                self._send_json({"error": "Item not found in config."}, 404)
                return
            if kind == "profile":
                result = launch_profile(item.get("profile_dir") or "")
            elif kind == "app":
                result = launch_app(item.get("app_path") or "")
            else:
                self._send_json({"error": "Unknown kind."}, 400)
                return
            self._send_json(result, 200 if result.get("ok") else 500)
            return
        self._send_json({"error": "Not found"}, 404)


def main() -> int:
    p = argparse.ArgumentParser(
        description=f"SkillNova Profile Hub v{VERSION} — local web server")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SkillNova Profile Hub v{VERSION} — open http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop. Nothing keeps running after exit.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
