# Security policy

MatteoEye handles access to home security cameras, so security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through GitHub: **Security → Report a vulnerability** on this repository ([direct link](https://github.com/markmanny/matteoeye/security/advisories/new)).

Include what you found, how to reproduce it and the version you tested. You will get an answer as soon as possible.

## How MatteoEye protects your account

- Your Blink password is used only to sign in and is **never stored**. Only Blink's access tokens are saved, in the user's data folder, with owner-only permissions where the system supports them.
- By default the server listens on `127.0.0.1` only. With `--lan`, or when exposed through Tailscale Funnel or ngrok, every new device must sign in with the Blink email, password and two-factor code.
- Resuming a saved session without signing in is allowed only from the computer itself, never through a proxy.
- Video goes straight from Blink to your computer: no third-party server is involved.

## Scope

The Blink API is unofficial. Issues in Blink's own service or in [blinkpy](https://github.com/fronzbot/blinkpy) should be reported to those projects.
