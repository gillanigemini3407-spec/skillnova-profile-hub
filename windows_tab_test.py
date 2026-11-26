"""Practical Windows tab-restore test for SkillNova Profile Hub.

Runs on a GitHub Actions Windows runner with real Chrome and
replicates the user's exact failure scenario:
1. Opens 3 tabs in a fresh profile, then CLOSES one of them.
2. Closes Chrome with the other 2 tabs still open.
3. Reopens the profile through the hub's launch_profile().
4. Verifies EXACTLY the 2 open tabs come back — the closed tab
   must not return and nothing may duplicate.
5. Repeats the close/reopen cycle and verifies the count is
   still exactly 2 (no multiplication across sessions).
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import profile_hub

URLS = [
    "https://example.com/",
    "https://example.org/",
    "https://example.net/",
]
EXPECTED = ["https://example.com/", "https://example.org/"]
CLOSED = "https://example.net/"
PROFILE = "Profile 99"
PORT = 9222


def devtools_pages():
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/json/list", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return []


def chrome_cmdlines():
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
          "| ForEach-Object { $_.CommandLine }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=60)
    return out.stdout or ""


def kill_chrome():
    subprocess.run(["taskkill", "/IM", "chrome.exe", "/T", "/F"],
                   capture_output=True, timeout=60)
    time.sleep(3)


def main():
    chrome = profile_hub.find_chrome()
    print("chrome:", chrome)
    if not chrome:
        print("FAIL: Chrome not found")
        return 1
    udd = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
    os.environ["PROFILE_HUB_CHROME_DIR"] = str(udd)
    prof = udd / PROFILE
    # Remote debugging refuses Chrome's default data directory, so the
    # session is created in a temp data dir, then the profile folder
    # is copied into the default one for the hub to launch.
    tudd = Path(os.environ["TEMP"]) / "hub-tab-test-udd"
    tprof = tudd / "Default"
    tprof.mkdir(parents=True, exist_ok=True)
    tprefs = tprof / "Preferences"
    if not tprefs.exists():
        tprefs.write_text(json.dumps(
            {"profile": {"name": "TabTest"}}), encoding="utf-8")

    kill_chrome()
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--disable-dev-shm-usage",
           f"--remote-debugging-port={PORT}",
           f"--user-data-dir={tudd}", "--profile-directory=Default",
           "--no-first-run", "--no-default-browser-check", "about:blank"]
    print("launch1:", cmd, flush=True)
    errlog = open(Path(__file__).parent / "chrome-stderr.log", "w",
                  encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, stderr=errlog, stdout=errlog)
    # Headless Chrome rejects multiple URL targets, so open tabs via
    # the DevTools HTTP API once the debugging port is up.
    for _ in range(30):
        time.sleep(2)
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/json/version",
                    timeout=5) as r:
                print("devtools version:",
                      r.read().decode("utf-8")[:200], flush=True)
            break
        except Exception as exc:
            print("waiting for devtools:", exc, flush=True)
    for url in URLS:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/json/new?{url}", method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                print("opened tab:", url, r.status, flush=True)
        except Exception as exc:
            print("open tab failed:", url, exc, flush=True)
    opened = []
    for i in range(30):
        time.sleep(2)
        if i in (0, 5, 15):
            print("chrome poll:", proc.poll(), flush=True)
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{PORT}/json/version",
                        timeout=5) as r:
                    print("devtools version:",
                          r.read().decode("utf-8")[:300], flush=True)
            except Exception as exc:
                print("devtools version error:", exc, flush=True)
        opened = [p.get("url", "") for p in devtools_pages()
                  if p.get("type") == "page"]
        if all(any(u.rstrip("/") in o for o in opened) for u in URLS):
            break
    print("tabs open in Chrome:", opened, flush=True)
    if not all(any(u.rstrip("/") in o for o in opened) for u in URLS):
        errlog.flush()
        try:
            tail = (Path(__file__).parent / "chrome-stderr.log").read_text(
                encoding="utf-8", errors="replace")[-3000:]
            print("chrome stderr tail:", tail, flush=True)
        except Exception as exc:
            print("stderr read error:", exc, flush=True)
        print("FAIL: could not open 3 tabs", flush=True)
        kill_chrome()
        return 1

    # Close ONE tab (example.net). It must never come back. The
    # other two stay open; Chrome is then killed WITHOUT closing
    # them, exactly like a user closing the Chrome window.
    for p in devtools_pages():
        if p.get("type") == "page" and CLOSED.rstrip("/") in p.get("url", ""):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/json/close/{p['id']}",
                    timeout=5)
                print("closed tab:", p.get("url"), flush=True)
            except Exception as exc:
                print("close tab failed:", exc, flush=True)
    time.sleep(6)
    kill_chrome()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass

    # Move the created session profile into Chrome's default data dir
    # so the hub launches it exactly like a real user profile.
    import shutil
    if prof.exists():
        shutil.rmtree(prof, ignore_errors=True)
    shutil.copytree(tprof, prof)
    print("profile copied to default data dir:", prof, flush=True)

    saved = profile_hub._session_urls(prof)
    print("hub read saved session URLs:", saved, flush=True)
    if sorted(saved) != sorted(EXPECTED):
        print("FAIL: saved session is not exactly the 2 open tabs",
              flush=True)
        return 1

    for cycle in (1, 2):
        res = profile_hub.launch_profile(PROFILE)
        print(f"cycle {cycle} hub launch result:",
              json.dumps(res, default=str), flush=True)
        url_args = [a for a in (res.get("command") or [])
                    if a.startswith("http")]
        print(f"cycle {cycle} URLs in hub command:", url_args, flush=True)
        if sorted(url_args) != sorted(EXPECTED):
            print(f"FAIL: cycle {cycle} command is not exactly the "
                  "2 open tabs", flush=True)
            kill_chrome()
            return 1
        time.sleep(10)
        live = chrome_cmdlines()
        print(f"cycle {cycle} live cmdlines contain closed tab:",
              CLOSED.rstrip("/") in live, flush=True)
        kill_chrome()
        time.sleep(2)
        saved = profile_hub._session_urls(prof)
        print(f"cycle {cycle} saved session after close:", saved,
              flush=True)
        if sorted(saved) != sorted(EXPECTED):
            print(f"FAIL: cycle {cycle} session multiplied or lost "
                  "tabs", flush=True)
            return 1

    print("PASS: exactly the 2 open tabs restored across two cycles; "
          "closed tab never returned, nothing duplicated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
