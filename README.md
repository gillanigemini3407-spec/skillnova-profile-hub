# SkillNova Profile Hub v0.1

All your Chrome profiles — and your other apps — in one place.
Click a tile, that profile (or app) opens. Nothing else to manage.

English UI only. Standard library only — no installs, no accounts,
no keys, no background service. Close the window and nothing keeps
running. About 10–20 MB.

## Run it

Double-click `launcher.py` (or the packaged exe, when built), or:

```
python3 launcher.py
```

Then open the address it prints, e.g. `http://127.0.0.1:8000`.

For testing / advanced use, `python3 profile_hub.py [--port 8000]`
runs the same server without opening a browser.

## What it does

- **Discovers your Chrome profiles** from the Chrome User Data folder
  (Windows: `%LOCALAPPDATA%\Google\Chrome\User Data`,
  Linux: `~/.config/google-chrome`). Profile names come from each
  profile's `Preferences` file.
- **Pin chosen profiles** into the hub with + Add Profile. Only the
  ones you pick appear.
- **Groups**: make as many as you want, any name, your order.
  One profile can sit in more than one group.
- **Rename, colour, reorder**: every name you set lives only in the
  hub (`config.json`). Chrome's own profile names are never changed.
- **Group view / List view**: tiles side by side in rows, or a compact
  list. A toggle switches. Search filters by name.
- **Session restore**: before launching a profile the hub sets
  `session.restore_on_startup = 1` in that profile's Preferences, so
  Chrome reopens your tabs where you left them. That is the ONLY thing
  ever written into Chrome's files. If the profile is already running,
  the edit is skipped (Chrome owns the file while running) and a new
  window opens in the same profile.
- **Other apps**: + Add App pins any program (`.exe`, shortcut, script)
  as a tile too. Click opens it.

## Windows build (GitHub Actions)

The portable Windows ZIP is built on a free online Windows machine
(GitHub Actions), which also CHECKS the tool there — mock Chrome
profiles, launch dry-run, restore edit, and a smoke test of the
built exe. No Windows PC needed to build it.

1. Create a GitHub repo (e.g. `skillnova-profile-hub`, Private),
   upload this whole folder including `.github/workflows/`.
2. Actions tab → "Build Windows portable" → "Run workflow".
3. When the run is green, download the artifact
   `SkillNova-Profile-Hub-v0.1-portable-windows`, extract it on
   Windows, double-click `SkillNova-Profile-Hub.exe`.

`verify_windows.py` is the check script the workflow runs
(11 checks; it also runs on Linux).

## Files

- `profile_hub.py` — the app: discovery, config, launch, web server.
- `launcher.py` — double-click entry point.
- `index.html` — the UI (loads all data from the API, no samples).
- `config.json` — created on first save: your groups, items, view.
- `TEST-LOG.md` — what was tested and the evidence.

## Honest limits

- Chrome must be installed for profiles to exist; the hub cannot
  create a Chrome profile, only open the ones Chrome already has.
- "Tabs restore" works on a normal close. If the PC loses power or
  Chrome crashes, Chrome may ask you to press Restore once.
- Unsaved work typed into a web page (not saved by the site) does not
  come back — Chrome restores the tabs, not unsaved form text.
- On Windows the hub launches `chrome.exe` with
  `--profile-directory=<dir>`. If Chrome is installed in an unusual
  place and is not found, launching fails with a clear error.
- The hub never reads your browsing history, passwords or cookies —
  it reads only `Preferences` (profile name + restore flag) and writes
  only the single restore flag.
