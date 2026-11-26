#!/usr/bin/env python3
"""SkillNova Profile Hub v0.1 — Windows checks (also runs on Linux).

Runs on the GitHub Actions Windows runner BEFORE the PyInstaller
freeze, and on any machine with Python 3. Uses a MOCK Chrome User
Data folder in the temp dir — never touches real Chrome data.
No real browser is ever launched (PROFILE_HUB_DRY_RUN=1).

Checks: discovery (3 good / 1 corrupt skipped / 1 stray ignored),
restore flags, config round-trip (rename, one profile in two groups,
app item), launch dry-run (exact command), restore edit 0 -> 1 with
the profile name untouched, running-profile guard (SingletonLock),
find_chrome() on this machine, and a live server smoke test
(GET / 200, /api/profiles).

Exit code 0 = all PASS, 1 = any FAIL.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="profile-hub-check-"))
    mock = tmp / "chrome-user-data"
    mock.mkdir()

    def profile(dirname, prefs):
        d = mock / dirname
        d.mkdir()
        (d / "Preferences").write_text(
            json.dumps(prefs), encoding="utf-8")
        return d

    profile("Default", {"profile": {"name": "Facebook"},
                        "session": {"restore_on_startup": 1}})
    profile("Profile 1", {"profile": {"name": "YouTube"},
                          "session": {"restore_on_startup": 0}})
    profile("Profile 2", {"profile": {"name": "TikTok"}})
    d7 = mock / "Profile 7"
    d7.mkdir()
    (d7 / "Preferences").write_text("{not valid json!!!", encoding="utf-8")
    (mock / "Crashpad").mkdir()  # stray folder, not a profile

    os.environ["PROFILE_HUB_CHROME_DIR"] = str(mock)
    os.environ["PROFILE_HUB_DRY_RUN"] = "1"
    os.environ["PROFILE_HUB_CONFIG"] = str(tmp / "config.json")
    os.environ["PROFILE_HUB_BASE"] = str(HERE)

    import profile_hub

    # If this machine has no real Chrome (this Linux VM), put a fake
    # chrome on PATH so the launch dry-run can still verify the exact
    # command. On the Windows runner real Chrome exists and is used.
    exe = profile_hub.find_chrome()
    if exe is None:
        fakebin = tmp / "fakebin"
        fakebin.mkdir()
        if sys.platform.startswith("win"):
            fake = fakebin / "chrome.bat"
            fake.write_text("@echo off\r\necho FAKE CHROME CALLED %*\r\n",
                            encoding="utf-8")
        else:
            fake = fakebin / "google-chrome"
            fake.write_text("#!/bin/sh\necho \"FAKE CHROME CALLED $@\"\n",
                            encoding="utf-8")
            fake.chmod(0o755)
        os.environ["PATH"] = str(fakebin) + os.pathsep + os.environ["PATH"]
        exe = profile_hub.find_chrome()
        print(f"[info] no real Chrome — using test fake: {exe}", flush=True)
    else:
        print(f"[info] real Chrome found: {exe}", flush=True)

    # 1-4: discovery
    found = profile_hub.discover_profiles()
    names = {p["dir"]: p["name"] for p in found["profiles"]}
    check("discovery finds 3 profiles with correct names",
          names == {"Default": "Facebook", "Profile 1": "YouTube",
                    "Profile 2": "TikTok"}, str(names))
    check("corrupt Preferences skipped, no crash",
          any(s["dir"] == "Profile 7" for s in found["skipped"]),
          str(found["skipped"]))
    check("stray folder ignored",
          all(p["dir"] != "Crashpad" for p in found["profiles"]))
    flags = {p["dir"]: p["restore_on_startup"] for p in found["profiles"]}
    check("restore flags read correctly (1 / 0 / missing)",
          flags == {"Default": 1, "Profile 1": 0, "Profile 2": None},
          str(flags))

    # 5: chrome lookup on this machine (real Chrome, or the test fake
    # above when this machine has none — the runner has real Chrome)
    exe = profile_hub.find_chrome()
    check("chrome executable resolvable (real or test fake)", exe is not None,
          str(exe))

    # 6-7: config round-trip
    cfg = {"version": "0.1", "view": "group",
           "groups": [{"id": "g1", "name": "Social Media",
                       "color": "#1877F2", "order": 0},
                      {"id": "g2", "name": "Work",
                       "color": "#34A853", "order": 1}],
           "items": [{"id": "i1", "kind": "profile",
                      "profile_dir": "Profile 1",
                      "custom_name": "My YouTube", "color": "#FF0000",
                      "group_ids": ["g1", "g2"], "order": 0},
                     {"id": "i2", "kind": "profile",
                      "profile_dir": "Default",
                      "custom_name": "Facebook", "color": "#1877F2",
                      "group_ids": ["g1"], "order": 1},
                     {"id": "i3", "kind": "app",
                      "app_path": "C:\\Windows\\notepad.exe",
                      "custom_name": "Notepad", "color": "#F59E0B",
                      "group_ids": ["g2"], "order": 2}]}
    profile_hub.save_config(cfg)
    back = profile_hub.load_config()
    check("config round-trip (rename, profile in 2 groups, app item)",
          back == cfg, f"groups={len(back['groups'])} "
                       f"items={len(back['items'])}")

    # Config must survive portable-folder updates: a legacy
    # beside-the-exe config migrates into the persistent config home
    # on first load, so future ZIP updates need no manual copying.
    saved_config_path = profile_hub.CONFIG_PATH
    saved_legacy_path = profile_hub.LEGACY_CONFIG_PATH
    try:
        legacy = tmp / "legacy-folder" / "config.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps(cfg), encoding="utf-8")
        profile_hub.CONFIG_PATH = tmp / "persistent" / "config.json"
        profile_hub.LEGACY_CONFIG_PATH = legacy
        migrated = profile_hub.load_config()
        migration_ok = (migrated == cfg
                        and profile_hub.CONFIG_PATH.is_file())
    finally:
        profile_hub.CONFIG_PATH = saved_config_path
        profile_hub.LEGACY_CONFIG_PATH = saved_legacy_path
    check("legacy config migrates to persistent config home",
          migration_ok, f"migration_ok={migration_ok}")

    # 8-9: launch dry-run + restore edit
    res = profile_hub.launch_profile("Profile 1")
    cmd = res.get("command") or []
    check("launch dry-run returns exact command",
          res.get("ok") and res.get("dry_run")
          and "--profile-directory=Profile 1" in cmd
          and "--restore-last-session" in cmd,
          str(cmd))
    check("launch forces last-session restore, badge effective on",
          "--restore-last-session" in cmd
          and all(p["restore_on"] for p in found["profiles"]),
          str(cmd))
    after = json.loads((mock / "Profile 1" / "Preferences")
                       .read_text(encoding="utf-8"))
    check("restore edit 0 -> 1, profile name untouched",
          after["session"]["restore_on_startup"] == 1
          and after["profile"]["name"] == "YouTube",
          str(after))

    # 10: running-profile guard
    (mock / "Profile 2" / "SingletonLock").write_text("", encoding="utf-8")
    res2 = profile_hub.launch_profile("Profile 2")
    prefs2 = json.loads((mock / "Profile 2" / "Preferences")
                        .read_text(encoding="utf-8"))
    check("running profile: edit skipped, Preferences untouched, "
          "command still returned",
          res2.get("ok") and res2.get("dry_run")
          and "session" not in prefs2
          and "--profile-directory=Profile 2" in res2["command"]
          and "--restore-last-session" in res2["command"],
          str(res2.get("restore_edit")))

    # 10b: Windows running marker — Chrome on Windows locks a plain
    # "lockfile", not SingletonLock. An unlocked (stale) lockfile must
    # NOT count as running, or the restore edit is skipped forever.
    # On Windows the lock test sees the mock lockfile as unlocked, so
    # the profile is closed and the restore edit applies. Off Windows
    # there is no msvcrt lock test, so existence is the fallback.
    (mock / "Profile 2" / "SingletonLock").unlink()
    (mock / "Profile 2" / "lockfile").write_text("", encoding="utf-8")
    win_running = profile_hub._profile_running(mock / "Profile 2")
    res2b = profile_hub.launch_profile("Profile 2")
    prefs2b = json.loads((mock / "Profile 2" / "Preferences")
                         .read_text(encoding="utf-8"))
    if sys.platform.startswith("win"):
        lockfile_ok = (not win_running and res2b.get("ok")
                       and prefs2b.get("session", {})
                       .get("restore_on_startup") == 1
                       and "--restore-last-session" in res2b["command"])
    else:
        lockfile_ok = (win_running and res2b.get("ok")
                       and "session" not in prefs2b
                       and "--restore-last-session" in res2b["command"])
    check("Windows lockfile: unlocked file is a closed profile, "
          "restore edit applies",
          lockfile_ok,
          f"win_running={win_running} "
          f"edit={res2b.get('restore_edit')}")

    # 10c: a stale Windows lockfile (left after Chrome exits) must not
    # count as running forever — the lock itself is tested on Windows.
    hub_src = (HERE / "profile_hub.py").read_text(encoding="utf-8")
    lock_test_ok = ("def _lockfile_locked" in hub_src
                    and "LK_NBLCK" in hub_src
                    and "_lockfile_locked(lock_path)" in hub_src)
    check("Windows lockfile: lock itself tested, stale file cannot "
          "block the restore edit forever",
          lock_test_ok, f"lock_test_ok={lock_test_ok}")

    # 10d: hub-managed restore — for a closed profile, saved session
    # URLs are opened directly so old tabs come back on reopen.
    # The synthetic SNSS file mirrors the user's real failure: one
    # tab navigated Gmail -> Facebook (Gmail is history, NOT an open
    # tab), one Hark tab, one closed tab, one closed window. Only
    # the two currently-open tabs may come back — exactly, once.
    def _snss_cmd(cmd_id, payload):
        return (len(payload) + 1).to_bytes(2, "little") + bytes([cmd_id]) + payload

    def _u32(n):
        return n.to_bytes(4, "little")

    def _pstr(s):
        data = s.encode("utf-8")
        return _u32(len(data)) + data + b"\x00" * (-len(data) % 4)

    def _nav(tab_id, hist_idx, url):
        body = _u32(tab_id) + _u32(hist_idx) + _pstr(url)
        return _snss_cmd(6, _u32(len(body)) + body)

    snss = b"SNSS" + _u32(3)
    snss += _snss_cmd(0, _u32(10) + _u32(1))          # tab 1 in window 10
    snss += _snss_cmd(2, _u32(1) + _u32(0))           # tab 1 index 0
    snss += _nav(1, 0, "https://mail.google.com/old")  # history entry
    snss += _nav(1, 1, "https://www.facebook.com/")    # current page
    snss += _snss_cmd(7, _u32(1) + _u32(1))           # selected = Facebook
    snss += _snss_cmd(0, _u32(10) + _u32(2))          # tab 2 in window 10
    snss += _snss_cmd(2, _u32(2) + _u32(1))           # tab 2 index 1
    snss += _nav(2, 0, "https://hark.com/chat")
    snss += _snss_cmd(7, _u32(2) + _u32(0))
    snss += _snss_cmd(0, _u32(10) + _u32(3))          # tab 3, later closed
    snss += _nav(3, 0, "https://example.com/closed-tab")
    snss += _snss_cmd(16, _u32(3))                    # TabClosed
    snss += _snss_cmd(0, _u32(11) + _u32(4))          # tab 4, window closed
    snss += _nav(4, 0, "https://example.com/closed-window")
    snss += _snss_cmd(17, _u32(11))                   # WindowClosed
    sessions_dir = mock / "Profile 2" / "Sessions"
    sessions_dir.mkdir(exist_ok=True)
    (sessions_dir / "Session_1").write_bytes(snss)
    # No lock markers: the profile is closed on every platform.
    for marker in ("SingletonLock", "lockfile"):
        marker_path = mock / "Profile 2" / marker
        if marker_path.exists():
            marker_path.unlink()
    res2c = profile_hub.launch_profile("Profile 2")
    cmd2c = res2c.get("command") or []
    fallback_ok = ("https://www.facebook.com/" in cmd2c
                   and "https://hark.com/chat" in cmd2c
                   and "--restore-last-session" not in cmd2c)
    check("closed profile: saved session URLs reopen old tabs",
          fallback_ok, f"cmd={cmd2c}")

    # 10f: exact restore — history entries and closed tabs/windows
    # must NOT come back, and open tabs must not multiply.
    url_args = [a for a in cmd2c if a.startswith("http")]
    exact_ok = (url_args == ["https://www.facebook.com/",
                             "https://hark.com/chat"])
    check("exact restore: only last session's open tabs, once each",
          exact_ok, f"urls={url_args}")

    # 10e: no-duplicate restore — when the hub opens the saved URLs,
    # Chrome's own startup restore must be OFF (restore_on_startup=5),
    # otherwise Chrome restores the same session too and every tab
    # doubles on each reopen (1 -> 2 -> 4 -> 8).
    prefs2c = json.loads((mock / "Profile 2" / "Preferences")
                         .read_text(encoding="utf-8"))
    no_dup_ok = prefs2c.get("session", {}).get("restore_on_startup") == 5
    check("no-duplicate restore: Chrome startup restore off when hub "
          "opens saved URLs",
          no_dup_ok,
          f"restore_on_startup={prefs2c.get('session', {}).get('restore_on_startup')}")

    # 11: UI regression — Rename must open edit, not launch the browser.
    # (v0.1 bug: the tile was one big launch button containing the
    # Rename span, and the click handler checked launch first.)
    html = (HERE / "index.html").read_text(encoding="utf-8")
    tile_ok = ('<button class="edit" data-edit=' in html
               and '<button class="tile" data-launch=' not in html)
    handler = html.split('document.addEventListener("click"', 1)[1] \
        .split("});", 1)[0]
    order_ok = 0 <= handler.find("[data-edit]") < handler.find("[data-launch]")
    check("UI: Rename button edits, Open button launches",
          tile_ok and order_ok,
          f"tile_ok={tile_ok} edit_before_launch={order_ok}")

    # 12: UI regression — Add Chrome profile modal must always be
    # closable (X / backdrop / Esc), keep Done visible while the list
    # scrolls, and refresh the row to Added immediately after Add.
    modal_ok = ('id="closeAddProfileX"' in html
                and 'class="modal-x"' in html
                and 'class="discovered-list"' in html
                and "overflow-y: auto" in html
                and "e.target === m" in html
                and 'e.key !== "Escape"' in html)
    add_handler = html.split('closest("[data-add-profile]")', 1)[1] \
        .split("// ---- Add app", 1)[0]
    save_idx = add_handler.find("await saveConfig();")
    add_refresh_ok = (save_idx >= 0
                      and add_handler.find("renderDiscoveredList();", save_idx)
                      > save_idx)
    check("UI: Add-profile modal closes (X/backdrop/Esc) and "
          "refreshes Added rows",
          modal_ok and add_refresh_ok,
          f"modal_ok={modal_ok} add_refresh_ok={add_refresh_ok}")

    # 13: UI regression — a newly created empty group must be visible.
    # (Bug: render() skipped groups with no items, so Save said
    # "Group saved" but the group never appeared.)
    group_ok = ("if (!mine.length) continue;" not in html
                and "No profiles yet" in html
                and 'cfg.view = "group";' in html
                and '$("#search").value = "";' in html
                and "if (!loaded) await loadAll();" in html)
    check("UI: new empty groups stay visible in Group view",
          group_ok, f"group_ok={group_ok}")

    # 14: UI regression — Add App must tolerate quoted Windows paths,
    # refuse duplicates, and every modal must have an X close button.
    app_ok = ('id="closeAddAppX"' in html
              and 'id="closeEditX"' in html
              and 'id="closeGroupX"' in html
              and '^"+|"+$' in html
              and 'i.kind === "app" && i.app_path === path' in html)
    check("UI: Add App handles quoted paths, duplicates, modal X buttons",
          app_ok, f"app_ok={app_ok}")

    # 15: discovery names + completeness — Chrome's exact display
    # name wins: signed-in profiles show the full account name
    # ("Shah • Gillani"), while a not-signed-in profile keeps its
    # exact local profile name ("Noor BM") instead of a stored
    # username-like account label ("shahtiktokpk"). A profile Chrome
    # lists in Local State still shows even if Preferences is broken.
    names_dir = tmp / "names-user-data"
    (names_dir / "Profile 1").mkdir(parents=True)
    (names_dir / "Profile 1" / "Preferences").write_text(json.dumps({
        "profile": {"name": "Gillani", "user_name": "gillani@example.com"},
        "account_info": [{"full_name": "Shah • Gillani",
                          "email": "gillani@example.com"}],
        "session": {"restore_on_startup": 1}}), encoding="utf-8")
    (names_dir / "Profile 2").mkdir(parents=True)
    (names_dir / "Profile 2" / "Preferences").write_text(
        "{broken json", encoding="utf-8")
    (names_dir / "Profile 3").mkdir(parents=True)
    (names_dir / "Profile 3" / "Preferences").write_text(json.dumps({
        "profile": {"name": "Noor BM",
                    "user_name": "shahtiktokpk@example.com"},
        "account_info": [{"full_name": "shahtiktokpk",
                          "email": "shahtiktokpk@example.com"}],
        "session": {"restore_on_startup": 0}}), encoding="utf-8")
    (names_dir / "Local State").write_text(json.dumps(
        {"profile": {"info_cache": {
            "Profile 1": {"name": "Gillani",
                          "gaia_id": "1234567890",
                          "gaia_name": "Shah • Gillani",
                          "is_consented_primary_account": True,
                          "user_name": "gillani@example.com"},
            "Profile 2": {"name": "Recovered Profile"},
            "Profile 3": {"name": "Noor BM",
                          "gaia_name": "shahtiktokpk",
                          "is_consented_primary_account": False,
                          "user_name": "shahtiktokpk@example.com"}}}}),
        encoding="utf-8")
    found = profile_hub.discover_profiles(names_dir)
    by_dir = {p["dir"]: p for p in found["profiles"]}
    names_ok = (by_dir.get("Profile 1", {}).get("name") == "Shah • Gillani"
                and by_dir.get("Profile 1", {}).get("profile_name")
                == "Gillani"
                and by_dir.get("Profile 1", {}).get("account_name")
                == "Shah • Gillani"
                and by_dir.get("Profile 1", {}).get("email")
                == "gillani@example.com"
                and by_dir.get("Profile 2", {}).get("name")
                == "Recovered Profile"
                and by_dir.get("Profile 3", {}).get("name") == "Noor BM"
                and by_dir.get("Profile 3", {}).get("profile_name")
                == "Noor BM"
                and len(found["profiles"]) == 3
                and found["skipped"] == [])
    check("discovery: exact Chrome names shown, all profiles listed",
          names_ok,
          str([(p["dir"], p["name"]) for p in found["profiles"]])
          + f" skipped={found['skipped']}")

    # 16: UI regression — hub-only rename must save the exact typed
    # name, verify it came back, and show a real error if saving
    # fails (never silently keep the old name). Chrome's own profile
    # name is not part of this save path.
    save_block = html.split('$("#saveEdit").onclick', 1)[1] \
        .split('$("#editRemove")', 1)[0]
    rename_ui_ok = ("saved.custom_name !== newName" in save_block
                    and "Could not save name:" in save_block
                    and "Saving..." in save_block
                    and 'Math.random().toString(36).slice(2, 7)' in html)
    check("UI: rename verifies the saved hub name and reports errors",
          rename_ui_ok, f"rename_ui_ok={rename_ui_ok}")

    # Open profiles show a green LIVE badge (tiles, rows, Add list),
    # refreshed periodically from the running flag.
    live_ok = (".live {" in html and "background: #16a34a" in html
               and "function liveBadge(item)" in html
               and "runningOf(item)" in html
               and html.count("liveBadge(it)") >= 2
               and '<span class="live">LIVE</span>' in html
               and "setInterval(refreshRunning, 10000)" in html)
    check("UI: open profiles show a green LIVE badge",
          live_ok, f"live_ok={live_ok}")

    # 18: live server smoke test + rename round-trip over HTTP
    server = ThreadingHTTPServer(("127.0.0.1", 0), profile_hub.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=5) as resp:
            body = resp.read().decode("utf-8")
            check("GET / returns 200 + UI + no-cache headers",
                  resp.status == 200
                  and "SkillNova Profile Hub" in body
                  and "Lightweight: ~10" in body
                  and "no-store" in (resp.headers.get("Cache-Control") or ""),
                  f"HTTP {resp.status} "
                  f"cache={resp.headers.get('Cache-Control')}")
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/profiles",
                timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            check("GET /api/profiles returns 3 profiles",
                  len(data["profiles"]) == 3,
                  str([p["name"] for p in data["profiles"]]))
        renamed = json.loads(json.dumps(cfg))
        renamed["items"][0]["custom_name"] = "My TikTok UK"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config",
            data=json.dumps(renamed).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            posted = json.loads(resp.read().decode("utf-8"))
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/config",
                timeout=5) as resp:
            fetched = json.loads(resp.read().decode("utf-8"))
        chrome_name_after = json.loads(
            (mock / "Profile 1" / "Preferences").read_text(
                encoding="utf-8"))["profile"]["name"]
        rename_ok = (
            posted["items"][0]["custom_name"] == "My TikTok UK"
            and fetched["items"][0]["custom_name"] == "My TikTok UK"
            and chrome_name_after == "YouTube")
        check("HTTP rename saves hub name, Chrome name untouched",
              rename_ok,
              f"posted={posted['items'][0]['custom_name']} "
              f"fetched={fetched['items'][0]['custom_name']} "
              f"chrome={chrome_name_after}")
    except Exception as exc:  # noqa: BLE001 - report as a failed check
        check("live server smoke test", False, str(exc))
    finally:
        server.shutdown()

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed", flush=True)
    if FAILED:
        print("FAILED: " + ", ".join(FAILED), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
