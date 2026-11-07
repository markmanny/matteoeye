# Security policy

MatteoEye handles access to home security cameras, so security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through GitHub: **Security → Report a vulnerability** on this repository ([direct link](https://github.com/markmanny/matteoeye/security/advisories/new)).

Include what you found, how to reproduce it and the version you tested. You will get an answer as soon as possible.

## How MatteoEye protects your account

- Your Blink password is used only to sign in and is **never stored**. Only Blink's access tokens are saved, in the user's data folder, with owner-only permissions where the system supports them.
- By default the server listens on `127.0.0.1` only and **answers only requests addressed to `localhost`/`127.0.0.1`**, which blocks DNS-rebinding attacks (a web page that resolves its own domain to 127.0.0.1 to talk to MatteoEye through your browser).
- Requests that change something, and WebSockets, must come from a MatteoEye page (their `Origin` must match the address), so other websites can't send commands.
- **Signing in without a password** (reusing the saved Blink session) is allowed only from this computer: loopback connection, `localhost` address, a MatteoEye page as `Origin`, and no proxy or tunnel headers. This does not rely on a tunnel adding `X-Forwarded-For`: a remote browser behind Tailscale Funnel or ngrok always shows its public address as `Origin`.
- With `--lan`, Tailscale Funnel or ngrok, every new device must sign in with the Blink email, password and two-factor code. After **5 wrong attempts in 15 minutes** sign-in is paused, and 5 wrong codes cancel the pending sign-in.
- Each browser gets a random 256-bit session cookie (`HttpOnly`, `SameSite=Strict`, 30 days). Only its SHA-256 fingerprint is saved on disk, so devices stay signed in across restarts; **Esci** (log out) revokes all of them.
- Video goes straight from Blink to your computer: no third-party server is involved.

### Known limitations

- With `--lan` the page is served over plain HTTP: on your home network the password travels unencrypted. For access from outside, prefer Tailscale (encrypted) over exposing the port.
- The Blink tokens file is not encrypted: anyone who can read your user folder (including administrators) can use your Blink account until you log out.

## Scope

The Blink API is unofficial. Issues in Blink's own service or in [blinkpy](https://github.com/fronzbot/blinkpy) should be reported to those projects.
