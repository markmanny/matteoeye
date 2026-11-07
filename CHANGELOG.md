# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- English README with screenshots, logo and FAQ; the Italian README moved to `README.it.md`.
- Repository layout: build scripts in `packaging/`, macOS launchers in `scripts/macos/`, guides and images in `docs/`.
- Packages are published on the Releases page instead of being committed to the repository; the build scripts now write to `dist/`.

### Added
- GitHub Actions workflow running the end-to-end test with a simulated camera.
- Contributing guide, security policy, issue and pull request templates.

## [1.0.0] - 2026-11-07

### Added
- First public version: live view of Blink cameras in a native window on Windows and macOS.
- One-click recording to MP4 with a *Save as* dialog.
- Snapshots, fullscreen, battery and temperature status.
- Ready-to-run packages for Windows (embeddable Python) and macOS (Apple silicon and Intel).
- Optional remote access from other devices (LAN, Tailscale, ngrok).

[Unreleased]: https://github.com/markmanny/matteoeye/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/markmanny/matteoeye/releases/tag/v1.0.0
