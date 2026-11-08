# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Redesigned interface: dark theme with a light option, one tab per network (Sync Module) so you only see the cameras of that place, camera cards with a play button, a controls pill (audio, stop, fullscreen, snapshot, record), a red `REC mm:ss` badge while recording, the age of the last picture, temperature, Wi-Fi and motion status under each card.
- Arm / disarm the selected network from the bar at the bottom (`Disattivato` / `Attivo`), Sync Module online status, and per-camera motion detection toggle.
- New endpoints: `GET /api/networks`, `POST /api/networks/{name}/arm`, `POST /api/cameras/{name}/motion`; `GET /api/cameras` now also returns `network`, `last_record` and `thumbnail_ts`.

## [1.0.0] - 2026-11-07

### Added
- Live view of Blink cameras in a native window on Windows and macOS, with a browser fallback.
- One-click recording to MP4 with a *Save as* dialog.
- Snapshots, fullscreen, battery, temperature and arm status.
- Ready-to-run packages for Windows (embeddable Python) and macOS (Apple silicon and Intel).
- Optional remote access from other devices (LAN, Tailscale, ngrok).
- English README with screenshots, contributing guide, security policy, issue and pull request templates.
- GitHub Actions workflow running the end-to-end test with a simulated camera.

### Fixed
- Windows: the native window now opens when the app is extracted from the downloaded zip. Windows marks the extracted DLLs as "downloaded from the Internet", and .NET refused to load pythonnet (`Failed to resolve Python.Runtime.Loader.Initialize`).
- No more FastAPI `on_event` deprecation warnings at startup (moved to a lifespan handler).
- Clear "cannot reach Blink" message when signing in without a working connection, instead of a traceback in the log.

### Changed
- Repository layout: build scripts in `packaging/`, macOS launchers in `scripts/macos/`, guides and images in `docs/`.
- Packages are published on the Releases page instead of being committed to the repository.

[Unreleased]: https://github.com/markmanny/matteoeye/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/markmanny/matteoeye/releases/tag/v1.0.0
