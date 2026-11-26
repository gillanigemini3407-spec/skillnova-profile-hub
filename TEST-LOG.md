# SkillNova Profile Hub v0.1 — TEST LOG

Tested 25 Nov 2026 on this Linux VM (Python 3.12.3, no Chrome installed).
All tests ran against a MOCK Chrome User Data folder at
`/tmp/mock-chrome-user-data` — the real Chrome config on this machine
was never touched (there is none). No real browser was ever launched:
launch tests used `PROFILE_HUB_DRY_RUN=1` plus a fake `google-chrome`
in `/tmp/fakebin` so the exact command could be verified.

## Mock data

| Dir | Preferences | Expected |
|---|---|---|
| `Default` | `{"profile": {"name": "Facebook"}, "session": {"restore_on_startup": 1}}` | found, restore on |
| `Profile 1` | `{"profile": {"name": "YouTube"}, "session": {"restore_on_startup": 0}}` | found, restore off |
| `Profile 2` | `{"profile": {"name": "TikTok"}}` (no `session` key) | found, restore missing |
| `Profile 7` | `{not valid json!!!` | skipped + logged |
| `Crashpad` | (no Preferences, not a profile dir) | ignored |

## Results

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Discovery finds 3 good profiles, names read correctly | **PASS** | `discover_profiles()` returned Default/Facebook, Profile 1/YouTube, Profile 2/TikTok |
| 2 | Corrupt Preferences skipped, no crash, logged | **PASS** | Profile 7 in `skipped` with reason `unreadable Preferences: Expecting property name...`; console line `[profile-hub] skip Profile 7: ...` |
| 3 | Non-profile folder ignored | **PASS** | `Crashpad` appears nowhere in output |
| 4 | Restore flags read correctly (1 / 0 / missing) | **PASS** | `restore_on_startup`: 1, 0, null; `restore_on`: true, false, false |
| 5 | `GET /` returns 200 + UI | **PASS** | `curl` → HTTP 200; page contains "SkillNova Profile Hub" and footer `Lightweight: ~10–20 MB · Nothing runs in background` |
| 6 | `GET /api/profiles` returns the 3 profiles | **PASS** | JSON output verified (see #1) |
| 7 | `GET /api/restore-status` returns per-profile flags | **PASS** | Same 3 profiles with flags; after edits all three read `restore_on_startup: 1` |
| 8 | `POST /api/config` saves rename / reorder / groups / app item | **PASS** | Saved 2 groups + 3 items; item i1 renamed "My YouTube", in TWO groups (`g1`, `g2`); app item i3 (`/usr/bin/gedit`) saved |
| 9 | `GET /api/config` round-trips the saved config | **PASS** | GET returned byte-identical structure; `config.json` also written to disk |
| 10 | Launch dry-run returns the exact command (profile, restore was 0) | **PASS** | `["/tmp/fakebin/google-chrome", "--profile-directory=Profile 1"]`, `dry_run: true` |
| 11 | Restore edit on launch: 0 → 1, other keys untouched | **PASS** | Profile 1 Preferences after launch: `{"profile": {"name": "YouTube"}, "session": {"restore_on_startup": 1}}` — name key unchanged |
| 12 | Launch when restore already 1: no rewrite | **PASS** | `changed: false, reason: restore_on_startup already 1`; command still returned |
| 13 | Launch of unknown item → 404 | **PASS** | `{"error": "Item not found in config."}` HTTP 404 |
| 14 | App launch dry-run returns the app path as command | **PASS** | `["/usr/bin/gedit"]`, `dry_run: true` |
| 15 | Running profile: edit SKIPPED, Preferences untouched, command still returned | **PASS** | With `SingletonLock` symlink present: `changed: false, reason: profile running — edit skipped, Chrome owns the file`; Profile 2 Preferences byte-identical afterwards. **Bug found & fixed during this test:** the first version used `Path.exists()` on the lock, which is False for Chrome's broken-symlink lock — switched to `os.path.lexists()`, re-tested, now correct. |
| 16 | Missing `session` key is created on launch (profile not running) | **PASS** | After lock removed, Profile 2 Preferences became `{"profile": {"name": "TikTok"}, "session": {"restore_on_startup": 1}}` |
| 17 | Fake Chrome was never invoked; no Chrome process ran | **PASS** | Server log: 0 × `FAKE CHROME CALLED`; `pgrep google-chrome`: none |
| 18 | `launcher.py` smoke test | **PASS** | `GET /` 200 on port 8000, `/api/profiles` returned 3 profiles, clean startup log |
| 19 | Default config when no file exists | **PASS** | `{"version": "0.1", "view": "group", "groups": [], "items": []}` |
| 20 | Empty / missing User Data dir handled | **PASS** | Empty dir → `profiles: []`; missing dir → `skipped` with reason `Chrome User Data folder not found`, no crash |
| 21 | Bad JSON to `POST /api/config` → 400 | **PASS** | `{"error": "Bad JSON."}` HTTP 400 |
| 22 | UI loads real data, no hardcoded sample profiles | **PASS** | `index.html` renders only from `/api/profiles` + `/api/config`; grep for `Facebook One|Pakistan One|World One|Hussain One` → 0 matches |
| 23 | Python compiles clean, stdlib only | **PASS** | `python3 -m py_compile profile_hub.py launcher.py` OK; imports are stdlib only (`argparse, json, os, shutil, subprocess, sys, uuid, http.server, pathlib`) |

## Not tested (honest gaps)

- **Live browser clicks**: the UI's buttons (Add Profile, Edit, Group/List
  toggle, search) were verified only at the API level, not by clicking in
  a real browser — this VM has no desktop browser to drive. The JS is
  plain fetch calls to the same endpoints tested above.
- **Real Chrome launch**: no Chrome exists on this VM, so the actual
  `chrome --profile-directory=...` process spawn was verified only as a
  dry-run command. On the user's Windows PC the first real launch is the
  remaining check (chrome.exe path lookup covers Program Files, Program
  Files (x86), LocalAppData, and PATH).
- **Windows paths**: `%LOCALAPPDATA%\Google\Chrome\User Data` and
  `chrome.exe` discovery are coded but could not run on this Linux VM.

## How to re-run the tests

```bash
# 1. mock data (already at /tmp/mock-chrome-user-data)
# 2. start server in dry-run mode
PATH=/tmp/fakebin:$PATH PROFILE_HUB_CHROME_DIR=/tmp/mock-chrome-user-data \
  PROFILE_HUB_DRY_RUN=1 PROFILE_HUB_CONFIG=/tmp/test-config.json \
  python3 profile_hub.py --port 8011
# 3. curl the endpoints as in rows 5–17 above
```

## Windows build (GitHub Actions)

- **Build #1 — RED (25 Nov 2026 ~12:51 PKT, run 45571461669):** repo
  gillanigemini3407-spec/skillnova-profile-hub created PRIVATE, all 7
  files in place. Checks (verify_windows.py), PyInstaller freeze all
  PASSED. Failed at "Smoke test the built exe": FileNotFoundError —
  the exe serves index.html from its own folder, but PyInstaller does
  not bundle index.html, and the Assemble step (which copies it) runs
  AFTER the smoke test. Root cause: smoke test ran before index.html
  was beside the exe.
- **Fix:** new workflow step "Copy index.html beside the exe" right
  after the freeze, before the smoke test. Workflow re-run pending.

- **Build #2 — GREEN (25 Nov 2026 ~13:03 PKT, run 45573012421):** workflow
  fix (copy index.html beside the exe) worked. ALL steps passed:
  Windows checks (verify_windows.py 11/11), PyInstaller freeze,
  exe smoke test (UI served on port 8000), assemble, zip, upload.
  Artifact: SkillNova-Profile-Hub-v0.1-portable-windows, 8.5 MB.
  Download: https://github.com/gillanigemini3407-spec/skillnova-profile-hub/actions/runs/45573012421/artifacts/15003287206 (owner sign-in required).

- **Rename bug fix (26 Nov 2026, user report):** in Group view the tile was one big launch `<button>` containing the Rename span, and the click handler checked launch before edit — clicking Rename opened the browser. Fixed: tile is now a plain container with separate Open and Rename buttons, and the click handler checks edit before launch. Added regression check 12 to verify_windows.py ("UI: Rename button edits, Open button launches"); verify_windows.py now 12/12 PASS locally.

- **Add-profile modal fix (26 Nov 2026, user screenshot):** long profile list pushed Close below the fold; no X/backdrop/Esc close; Added rows did not refresh until page reload. Fixed: top-right X always visible, backdrop click and Esc close, profile list scrolls inside the modal while header/Done stay visible, and Add re-renders the discovered list immediately (row becomes Added without refresh). Added regression check 13 to verify_windows.py; local verify now 13/13 PASS (rename + modal both checked).

- **Group visibility fix (26 Nov 2026, user report):** creating a group saved but the group never appeared — render() skipped groups with no profiles, so a new empty group was invisible. Fixed: empty groups now show in Group view with a "No profiles yet" hint (hidden only when a search excludes them), and creating a group switches to Group view with a "Group created." message. Added regression check 14; local verify now 14/14 PASS.

- **Add App hardening (26 Nov 2026, user asked to check it too):** Add App now strips surrounding quotes from pasted Windows paths ("Copy as path" includes quotes, which would have broken launch), refuses duplicate app paths, derives a clean display name without the .exe extension, reports save errors instead of failing silently, and every modal (Add App, Edit, Group) now has a top-right X like Add Profile. Backend launch already used os.startfile on Windows (.exe/.lnk). Added regression check 15; local verify now 15/15 PASS.

- **Stale-page fix (26 Nov 2026, user: groups still not creating):** live DOM test proved current code creates/shows/persists groups correctly — the user's browser was still running the old page after index.html replacement (no cache headers were sent). Fixed: server now sends Cache-Control: no-store on the UI and API, group creation clears any active search filter, and saves wait for the initial load so a fast click can never overwrite the saved config. Verified: DOM group test PASS, no-cache headers present, verify 15/15 PASS. User must close the old hub tab and relaunch the exe once so the new page loads.

- **Profile names + completeness fix (26 Nov 2026, user report):** profiles showed generic Chrome names ("Person 4", "Your Chrome") and not all profiles appeared. Fixed: discovery now prefers the signed-in account's full name over generic Chrome names, shows the account email in profile sublines to tell duplicates apart, lists every profile Chrome knows from Local State (even when Preferences cannot be read), and sorts Default, then Profile 1, 2, 3... numerically. Added regression check 16; local verify 16/16 PASS.

- **Full Chrome name correction (26 Nov 2026, user screenshot):** the hub still showed only "Gillani" while Chrome showed the full signed-in name "Shah • Gillani". Fixed: the full account name now wins over a shorter local profile name too (not only over generic names), Local State `gaia_name` is used as a fallback, and the Add list subline keeps the local Chrome profile name for clarity. Regression check updated with the Shah • Gillani fixture.

- **Exact Chrome profile-name correction (26 Nov 2026, user screenshot):** the hub showed "shahtiktokpk" while Chrome showed the profile name "Noor BM". Fixed: signed-in profiles still show the full account name, but profiles not signed in to Chrome keep their exact local Chrome profile name; username-like account labels matching the email name no longer replace it. Regression fixture now covers both Shah • Gillani and Noor BM.

- **Hub-only rename save fix (26 Nov 2026, user screenshot):** renaming a profile inside the tool did not reliably save the typed name. Fixed: Edit Save now verifies the saved hub name came back from the server before closing, shows a real error instead of failing silently, new item ids include a random suffix, and config loading/saving repairs any missing or duplicate item id so a rename always hits the exact profile opened. The rename stays hub-only — Chrome's own profile name is never changed. Added regression checks for the rename UI and an HTTP rename round-trip (hub name saved, Chrome name untouched); local verify now 18/18 PASS.

- **Green LIVE badge for open profiles (26 Nov 2026, user request):** profiles that are open in Chrome now show a green LIVE badge on tiles, list rows, and the Add-profile list; the running state refreshes every 10 seconds. Added regression check; local verify 19/19 PASS (includes the hub-rename fix checks).

- **Auto tab-restore fix (26 Nov 2026, user report):** reopened profiles were not restoring their open tabs. Fixed: every hub launch now passes Chrome's `--restore-last-session` switch (which overrides the startup preference for that launch) in addition to the Preferences edit, a failed Preferences edit no longer blocks the launch, and the restore badge now reflects the hub's forced restore. Added a regression check that the launch command forces last-session restore; local verify now 20/20 PASS.

- **Restore fix for Windows running detection (26 Nov 2026, user report: tabs still not restoring):** _profile_running only checked SingletonLock (Linux/Mac); Chrome on Windows marks a live profile with "lockfile". Missing it let the restore edit run while Chrome owned Preferences, and Chrome overwrote restore_on_startup on exit. Now both markers are checked. Added regression check; local verify 21/21 PASS.

- **Persistent config fix (26 Nov 2026):** saved profiles/groups lived only in a config.json beside the exe, so every portable update needed a manual copy. Fixed: the config now lives in a stable per-user home (Windows: %LOCALAPPDATA%\SkillNovaProfileHub\config.json), and a legacy beside-the-exe config is migrated automatically on first load. Future updates keep profiles/groups with no copying. Added a migration regression check; local verify now 22/22 PASS.

- **Restore fix v2 (26 Nov 2026, user: tabs still not restoring):** the Windows lockfile can remain after Chrome exits, so existence alone made closed profiles look "running" and the restore edit was skipped forever. _profile_running now tests the actual Windows file lock (msvcrt LK_NBLCK); a stale lockfile no longer blocks the restore edit. Added regression check; local verify 23/23 PASS.

- **Restore fix v3 (26 Nov 2026, user: old tabs still not restoring on reopen):** when a profile stays loaded in a running Chrome (window closed, Chrome in background), neither the Preferences edit nor the restore flag reliably brings tabs back. Hub now falls back to opening the profile's saved session URLs directly (read from Sessions/Session_* UTF-16LE text, deduped, capped at 25) in that case; closed profiles keep Chrome-native restore. Added regression check; local verify 24/24 PASS.

- **Restore fix v4 (26 Nov 2026, user: tabs still not restoring):** hub-managed session-URL restore is now primary for closed profiles (saved tab URLs opened directly on launch; command-line URLs take precedence over Chrome startup restore, so no duplicates). Running/loaded profiles keep the restore flag to avoid duplicating tabs in an open window. Local verify 24/24 PASS.

- 2026-11-26 duplicate-tabs fix: hub URL restore + Chrome startup restore ran together, doubling tabs each reopen (1->2->4->8). Fix: when hub opens saved URLs, restore_on_startup=5 so only one restore path runs. Local verify 25/25. Practical GitHub test must run TWO close/reopen cycles and confirm tab count stays identical.
