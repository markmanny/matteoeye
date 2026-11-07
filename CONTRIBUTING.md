# Contributing to MatteoEye

Thanks for helping! Contributions in English or Italian are both welcome.

## Reporting a bug

Open an [issue](https://github.com/markmanny/matteoeye/issues/new/choose) using the bug template and include:

- your system (Windows 10/11, macOS version, Apple silicon or Intel);
- how you started MatteoEye (package from Releases or from source);
- the relevant part of `blinklive.log` (`%APPDATA%\MatteoEye` on Windows, `~/Library/Application Support/MatteoEye` on macOS).

The log never contains your password, but it can contain your email address and camera names: remove anything you don't want to publish.

## Development setup

```bash
git clone https://github.com/markmanny/matteoeye.git
cd matteoeye
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt pywebview
./.venv/bin/python app.py --app
```

## Before opening a pull request

1. Keep changes focused: one fix or feature per pull request.
2. Run the tests. They use a simulated camera, so no Blink account is needed:
   ```bash
   ./.venv/bin/python tests/test_ws.py
   ```
   On macOS, also run `tests/test_webview.py` and `tests/test_chrome.py` when you touch the player or `static/`.
3. If you change behaviour users can see, update `README.md`, `README.it.md` and `CHANGELOG.md`.
4. Never commit credentials, `blink_credenziali.json`, logs or built zips: they are already in `.gitignore`.

## Code style

- Python 3.11+, standard library first; new dependencies only when they clearly pay off (they end up in both packages).
- The code and its comments are in Italian: keep that style in `app.py` so the file stays consistent.
- The interface is a single file (`static/index.html`) with no build step: keep it that way.

## Releases

Packages are built on a Mac with `packaging/build-macos.sh` and `packaging/build-windows.sh` (output in `dist/`), then attached to a GitHub Release tagged `vX.Y.Z`. Add the release notes to `CHANGELOG.md`.
