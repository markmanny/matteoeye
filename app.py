"""
MatteoEye - sito locale per vedere le telecamere Blink dal computer.

Avvio:  python app.py          (solo da questo computer)
        python app.py --lan    (anche dagli altri dispositivi di casa)
        python app.py --app    (modalita' programma: finestra propria, dati e log nella
                                cartella utente, si chiude da solo quando chiudi la pagina;
                                con --browser usa il browser al posto della finestra)
"""

import argparse
import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import socket
import ssl
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import ClientConnectionError, ClientSession
from blinkpy import api as blink_api
from blinkpy.auth import Auth, BlinkTwoFARequiredError
from blinkpy.blinkpy import Blink
from blinkpy.livestream import BlinkLiveStream
from fastapi import Cookie, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    import imageio_ffmpeg

    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover
    FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

# Windows: senza questo ogni ffmpeg avviato da pythonw (nessun terminale) apre una
# finestra di console vuota. Le finestre coprono il browser e Chromium, quando la sua
# finestra e' coperta, smette di disegnare il video (l'audio invece continua).
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

# Modalita' programma: eseguibile impacchettato (PyInstaller) oppure "python app.py --app"
APP_MODE = bool(getattr(sys, "frozen", False)) or "--app" in sys.argv
# Pagina e file statici (dentro al pacchetto quando e' un eseguibile)
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _migrate_dir(old, new):
    """Riusa la cartella dati della versione con il vecchio nome, se c'e'."""
    try:
        if old.is_dir() and not new.exists():
            old.rename(new)
    except OSError:
        pass


def _data_dir():
    """Cartella dei dati (token Blink e log)."""
    custom = os.environ.get("BLINKLIVE_DATA")
    if custom:
        d = Path(custom)
    elif not APP_MODE:
        d = Path(__file__).resolve().parent
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "MatteoEye"
        _migrate_dir(Path.home() / "Library" / "Application Support" / "Blink Live", d)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
        d = base / "MatteoEye"
        _migrate_dir(base / "Blink Live", d)
    else:
        d = Path.home() / ".blinklive"
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA = _data_dir()
CREDS = DATA / "blink_credenziali.json"  # contiene solo i token, mai la password
LOG_FILE = DATA / "blinklive.log"

# Pulisce le cartelle temporanee di esecuzioni precedenti chiuse male (piu' vecchie di un'ora)
for _old in Path(tempfile.gettempdir()).glob("blinklive_*"):
    try:
        if _old.is_dir() and time.time() - _old.stat().st_mtime > 3600:
            shutil.rmtree(_old, ignore_errors=True)
    except OSError:
        pass
HLS_ROOT = Path(tempfile.mkdtemp(prefix="blinklive_"))

_handlers = []
if sys.stderr is not None:
    _handlers.append(logging.StreamHandler())
if APP_MODE:
    _handlers.append(RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    if sys.stderr is None or sys.stdout is None:
        # eseguibile senza finestra: niente terminale, tutto nel file di log
        _logf = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or _logf
        sys.stderr = sys.stderr or _logf
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=_handlers)
log = logging.getLogger("blinklive")


# ---------------------------------------------------------------- correzione blinkpy
# blinkpy 0.25.9 legge i pacchetti con reader.read(n), che puo' restituire meno di n
# byte: in quel caso logga "Insufficient data for payload" e chiude la live dopo
# pochi secondi. Qui leggiamo sempre esattamente n byte (readexactly).
async def _recv_exact(self):
    """Copia i pacchetti video dal server Blink ai client locali (ffmpeg)."""
    try:
        while not self.target_reader.at_eof():
            try:
                header = await self.target_reader.readexactly(9)
            except asyncio.IncompleteReadError:
                break
            msgtype = header[0]
            payload_length = int.from_bytes(header[5:9], byteorder="big")
            if payload_length <= 0:
                continue
            try:
                data = await self.target_reader.readexactly(payload_length)
            except asyncio.IncompleteReadError:
                break
            # solo pacchetti video (0x00) che iniziano con il sync byte MPEG-TS
            if msgtype != 0x00 or data[0] != 0x47:
                continue
            if not getattr(self, "_bl_first_packet", False):
                self._bl_first_packet = True
                self._bl_first_packet_time = time.time()
                log.info("Live di %s: primo pacchetto video ricevuto da Blink", self.camera.name)
            for writer in self.clients:
                if not writer.is_closing():
                    writer.write(data)
                    await writer.drain()
            await asyncio.sleep(0)
    except ssl.SSLError as exc:
        if exc.reason != "APPLICATION_DATA_AFTER_CLOSE_NOTIFY":
            log.exception("Errore SSL nella ricezione della live")
    except Exception:
        log.exception("Errore nella ricezione della live")
    finally:
        self.target_writer.close()


BlinkLiveStream.recv = _recv_exact


@asynccontextmanager
async def lifespan(_app):
    """Avvio e chiusura del server (sostituisce i vecchi @app.on_event, deprecati)."""
    await resume()
    try:
        yield
    finally:
        await shutdown()


app = FastAPI(title="MatteoEye", lifespan=lifespan)

# ---------------------------------------------------------------- stato globale
state = {
    "session": None,      # aiohttp ClientSession
    "blink": None,        # istanza Blink attiva
    "pending": None,      # Blink in attesa del codice 2FA
    "web_tokens": set(),  # cookie di sessione validi del sito (uno per browser)
    "server": None,       # uvicorn.Server, per chiudere il programma
    "window": None,       # finestra pywebview (modalita' programma)
    "open_url": None,     # indirizzo da aprire nel browser all'avvio
    "pings": 0,           # battiti ricevuti dalla pagina (modalita' programma)
}
# nome_camera -> {"live": BlinkLiveStream, "proc": ffmpeg che produce il flusso TS per le finestre,
#                 "rec": ffmpeg sessione, "sess": file .ts, "dir": Path, "started": ts,
#                 "recording": None | {"id", "t0"}, "lock": asyncio.Lock,
#                 "clients": set di asyncio.Queue (una per finestra collegata), "prebuf": inizio del flusso,
#                 "had_client": bool, "ts_ready": asyncio.Event (primi byte video pronti), "bytes": int}
streams = {}
RECORDINGS = {}   # id -> info della registrazione salvata
LAST_REC = {}     # nome_camera -> info dell'ultima registrazione chiusa insieme alla live
FINALIZING = set()


def recordings_dir():
    """Cartella predefinita delle registrazioni (Filmati/Video dell'utente)."""
    if sys.platform == "darwin":
        d = Path.home() / "Movies" / "MatteoEye"
    elif sys.platform == "win32":
        d = Path(os.environ.get("USERPROFILE") or Path.home()) / "Videos" / "MatteoEye"
    else:
        d = Path.home() / "MatteoEye"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def get_session():
    if state["session"] is None or state["session"].closed:
        state["session"] = ClientSession()
    return state["session"]


def disable_local_storage_polling(blink):
    """Non interroghiamo l'archivio locale del Sync Module (clip su chiavetta): non ci
    serve e ogni richiesta del manifest tiene occupato il modulo, che intanto non
    riesce a servire la live ("System is busy")."""
    for sm in getattr(blink, "sync", {}).values():
        try:
            sm._local_storage["status"] = False
        except Exception:
            pass


async def save_creds(blink):
    data = dict(blink.auth.login_attributes)
    data.pop("password", None)
    CREDS.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(CREDS, 0o600)
    except OSError:
        pass


def new_web_token():
    token = secrets.token_urlsafe(32)
    state["web_tokens"].add(token)
    return token


def require_auth(token):
    if not state["blink"] or not token or token not in state["web_tokens"]:
        raise HTTPException(status_code=401, detail="Non autenticato")


def set_cookie(resp, token):
    resp.set_cookie("bl_session", token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 30)


# ---------------------------------------------------------------- avvio: riusa token salvati
async def resume():
    if state["open_url"]:
        asyncio.get_running_loop().call_later(0.5, _open_browser, state["open_url"])
    if APP_MODE:
        asyncio.create_task(watchdog())
    if not CREDS.exists():
        return
    try:
        data = json.loads(CREDS.read_text())
        session = await get_session()
        blink = Blink(session=session)
        blink.auth = Auth(data, no_prompt=True, session=session)
        if await blink.start():
            disable_local_storage_polling(blink)
            await blink.refresh(force=True)
            state["blink"] = blink
            await save_creds(blink)
            log.info("Accesso ripristinato dai token salvati (%d telecamere)", len(blink.cameras))
    except Exception as exc:
        log.warning("Impossibile riusare i token salvati: %s", exc)


async def shutdown():
    for name in list(streams):
        await stop_stream(name)
    if state["session"]:
        await state["session"].close()
    shutil.rmtree(HLS_ROOT, ignore_errors=True)


def _open_browser(url):
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception as exc:
        log.warning("Non riesco ad aprire il browser: %s", exc)


def quit_app():
    """Chiude il programma in modo pulito (ferma le live, spegne il server, chiude la finestra)."""
    if state["server"] is not None:
        state["server"].should_exit = True
    win = state["window"]
    if win is not None:
        try:
            win.destroy()
        except Exception:
            pass
    if state["server"] is None and win is None:
        os._exit(0)


def unblock_downloaded_dlls():
    """Windows: toglie il segno "scaricato da Internet" (flusso Zone.Identifier) dalle DLL.

    Estraendo lo zip con Esplora file ogni file eredita quel segno e .NET Framework si
    rifiuta di caricare Python.Runtime.dll (pythonnet), con l'errore "Failed to resolve
    Python.Runtime.Loader.Initialize": senza questo passaggio la finestra non si apre
    e si ripiega sul browser. Equivale a "Proprieta' > Annulla blocco" su ogni DLL.
    """
    if sys.platform != "win32":
        return
    count = 0
    prefix = Path(sys.prefix).resolve()
    roots = [BASE] if BASE in (prefix, *prefix.parents) else [BASE, prefix]  # Python dentro la cartella: una sola passata
    for root in roots:
        for dll in root.rglob("*.dll"):
            try:
                os.remove(f"{dll}:Zone.Identifier")
                count += 1
            except OSError:
                pass  # nessun segno (caso normale) o file non modificabile
    if count:
        log.info("Sbloccate %d DLL segnate come scaricate da Internet", count)


def run_window(url):
    """Finestra propria con la pagina dentro (pywebview). Blocca finche' viene chiusa."""
    unblock_downloaded_dlls()
    import webview

    win = webview.create_window("MatteoEye", url, width=1100, height=780, min_size=(640, 440))
    state["window"] = win
    kw = {"private_mode": False}  # conserva il cookie di sessione tra un avvio e l'altro
    if sys.platform == "win32":
        kw["gui"] = "edgechromium"  # WebView2 (Edge); se manca si passa al browser
    webview.start(**kw)
    state["window"] = None


def fatal_box(msg):
    """Mostra un errore in una finestra di sistema (il programma non ha un terminale)."""
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, msg, "MatteoEye", 0x10)
        elif sys.platform == "darwin":
            import subprocess

            subprocess.run(
                ["osascript", "-e", f'display dialog {json.dumps(msg)} with title "MatteoEye" buttons {{"OK"}} with icon stop'],
                timeout=120,
            )
    except Exception:
        pass


async def watchdog():
    """Modalita' programma: si spegne quando nessuna pagina e' piu' aperta.

    La pagina manda un battito ogni 15 s. Si contano i giri del controllo, non
    l'orologio, cosi' lo stop del computer non viene scambiato per una chiusura.
    """
    seen, misses = 0, 0
    while True:
        await asyncio.sleep(15)
        if state["pings"] != seen:
            seen, misses = state["pings"], 0
            continue
        misses += 1
        limit = 12 if seen else 20  # 3 min senza pagina (5 min se non si e' mai aperta)
        if misses >= limit:
            log.info("Nessuna pagina aperta: chiudo MatteoEye")
            quit_app()
            return


# ---------------------------------------------------------------- login
@app.get("/api/status")
async def status(bl_session: str = Cookie(None)):
    logged = bool(state["blink"]) and bool(bl_session) and bl_session in state["web_tokens"]
    # Se i token salvati sono validi ma il browser non ha ancora il cookie
    # (es. dopo un riavvio), lo consideriamo loggato solo da localhost.
    return {
        "logged_in": logged, "resumable": bool(state["blink"]) and not logged,
        "app_mode": APP_MODE, "window": state["window"] is not None,
    }


@app.post("/api/ping")
async def ping():
    if state["pings"] == 0:
        log.info("Pagina collegata")
    state["pings"] += 1
    return {"ok": True}


@app.post("/api/quit")
async def quit_endpoint(request: Request):
    if not APP_MODE:
        raise HTTPException(404)
    if request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403)
    for name in list(streams):
        await stop_stream(name)
    log.info("Chiusura richiesta dalla pagina")
    asyncio.get_running_loop().call_later(0.3, quit_app)
    return {"ok": True}


@app.post("/api/resume")
async def resume_web(request: Request):
    """Riprende la sessione salvata, consentito solo dal computer stesso."""
    if not state["blink"]:
        raise HTTPException(401, "Nessuna sessione salvata")
    via_proxy = "x-forwarded-for" in request.headers or "forwarded" in request.headers
    if request.client.host not in ("127.0.0.1", "::1") or via_proxy:
        raise HTTPException(403, "Accedi con email e password")
    resp = JSONResponse({"ok": True})
    set_cookie(resp, new_web_token())
    return resp


@app.post("/api/login")
async def login(payload: dict):
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""
    if not email or not password:
        raise HTTPException(400, "Inserisci email e password")
    session = await get_session()
    blink = Blink(session=session)
    blink.auth = Auth({"username": email, "password": password}, no_prompt=True, session=session)
    try:
        ok = await blink.start()
    except BlinkTwoFARequiredError:
        state["pending"] = blink
        return {"need_2fa": True}
    except (ClientConnectionError, asyncio.TimeoutError) as exc:
        # rete assente o DNS che non risolve i server Blink: non e' un errore del programma
        log.warning("Login: server Blink non raggiungibile (%s)", exc)
        raise HTTPException(503, "Non riesco a contattare Blink: controlla la connessione a Internet e riprova")
    except Exception as exc:
        log.exception("Errore login")
        raise HTTPException(401, f"Accesso non riuscito: {exc}")
    if not ok:
        raise HTTPException(401, "Email o password non corretti")
    return await finish_login(blink)


@app.post("/api/verify")
async def verify(payload: dict):
    blink = state["pending"]
    if not blink:
        raise HTTPException(400, "Nessun accesso in attesa di codice")
    code = (payload.get("code") or "").strip()
    try:
        ok = await blink.send_2fa_code(code)
    except Exception as exc:
        raise HTTPException(401, f"Codice non valido: {exc}")
    if not ok:
        raise HTTPException(401, "Codice non valido o scaduto")
    state["pending"] = None
    return await finish_login(blink)


async def finish_login(blink):
    disable_local_storage_polling(blink)
    await blink.refresh(force=True)
    state["blink"] = blink
    await save_creds(blink)
    resp = JSONResponse({"ok": True})
    set_cookie(resp, new_web_token())
    return resp


@app.post("/api/logout")
async def logout():
    for name in list(streams):
        await stop_stream(name)
    state["blink"] = None
    state["web_tokens"].clear()
    CREDS.unlink(missing_ok=True)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("bl_session")
    return resp


# ---------------------------------------------------------------- telecamere
def find_camera(name):
    cam = state["blink"].cameras.get(name)
    if not cam:
        raise HTTPException(404, "Telecamera non trovata")
    return cam


@app.get("/api/cameras")
async def cameras(bl_session: str = Cookie(None)):
    require_auth(bl_session)
    try:
        await state["blink"].refresh()
    except Exception as exc:
        log.warning("Refresh fallito: %s", exc)
    out = []
    for name, cam in state["blink"].cameras.items():
        out.append({
            "name": name,
            "type": cam.camera_type or cam.product_type or "",
            "battery": cam.battery,
            "temperature": cam.temperature_c,
            "armed": cam.arm,
            "motion": cam.motion_detected,
            "wifi": cam.wifi_strength,
            "live": name in streams,
            "updated": int(time.time()),
            "network": getattr(cam.sync, "name", None),
            "thumb_ts": thumbnail_time(cam),
        })
    return out


def thumbnail_time(cam):
    """Ora (epoch) della miniatura: Blink la scrive nell'indirizzo dell'immagine (ts=...)."""
    m = re.search(r"ts=(\d{9,})", str(getattr(cam, "thumbnail", "") or ""))
    return int(m.group(1)) if m else None


@app.get("/api/networks")
async def networks(bl_session: str = Cookie(None)):
    """Sistemi Blink (Sync Module) dell'account, anche senza telecamere, con lo stato di attivazione."""
    require_auth(bl_session)
    out = []
    for name, sm in getattr(state["blink"], "sync", {}).items():
        out.append({
            "name": name,
            "armed": getattr(sm, "arm", None),
            "online": getattr(sm, "online", None),
            "cameras": len(getattr(sm, "cameras", {}) or {}),
        })
    return out


@app.post("/api/networks/{name}/arm")
async def network_arm(name: str, payload: dict, bl_session: str = Cookie(None)):
    """Attiva o disattiva un sistema (come l'interruttore Armed/Disarmed dell'app Blink)."""
    require_auth(bl_session)
    sm = getattr(state["blink"], "sync", {}).get(name)
    if sm is None:
        raise HTTPException(404, "Sistema non trovato")
    armed = bool(payload.get("armed"))
    try:
        await sm.async_arm(armed)
    except Exception as exc:
        log.exception("Cambio stato del sistema %s non riuscito", name)
        raise HTTPException(502, f"Blink non ha cambiato lo stato: {exc}")
    log.info("Sistema %s %s", name, "attivato" if armed else "disattivato")
    return {"ok": True, "armed": armed}


@app.get("/api/cameras/{name}/thumbnail")
async def thumbnail(name: str, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    cam = find_camera(name)
    img = cam.image_from_cache
    if not img:
        r = await cam.get_media()
        if r is not None and r.status == 200:
            img = await r.read()
    if not img:
        raise HTTPException(404, "Nessuna immagine")
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/cameras/{name}/snap")
async def snap(name: str, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    cam = find_camera(name)
    await cam.snap_picture()
    await asyncio.sleep(4)
    await state["blink"].refresh(force_cache=True)
    return {"ok": True}


# ---------------------------------------------------------------- live
async def stop_stream(name):
    s = streams.pop(name, None)
    if not s:
        return
    if s.get("recording"):
        FINALIZING.add(name)
    try:
        async with s["lock"]:
            try:
                if s["proc"].returncode is None:
                    s["proc"].terminate()
                    await asyncio.wait_for(s["proc"].wait(), 3)
            except Exception:
                try:
                    s["proc"].kill()
                except Exception:
                    pass
            # registratore di sessione: chiusura pulita e, se stavamo registrando, taglio del file
            await stop_recorder(s)
            if s.get("recording"):
                await finalize_recording(name, s, locked=True)
            try:
                s["live"].stop()
            except Exception:
                pass
            shutil.rmtree(s["dir"], ignore_errors=True)
    finally:
        FINALIZING.discard(name)
    log.info("Live di %s fermata", name)


async def stop_recorder(s):
    r = s.get("rec")
    if r is None or r.returncode is not None:
        return
    try:
        if r.stdin is not None and not r.stdin.is_closing():
            r.stdin.write(b"q")  # chiusura pulita di ffmpeg
            await r.stdin.drain()
            r.stdin.close()
        await asyncio.wait_for(r.wait(), 5)
    except Exception:
        try:
            r.kill()
        except Exception:
            pass


async def finalize_recording(name, s, locked=False):
    """Chiude la registrazione in corso: taglia dal file di sessione il tratto registrato."""
    rec = s.get("recording")
    if not rec:
        return None
    s["recording"] = None
    if locked:
        return await _cut_recording(name, s, rec)
    async with s["lock"]:
        return await _cut_recording(name, s, rec)


async def _cut_recording(name, s, rec):
    live = s["live"]
    first = getattr(live, "_bl_first_packet_time", None) or s["started"]
    start = max(0.0, rec["t0"] - first - 1.0)          # un secondo di margine all'inizio
    length = max(1.0, time.time() - rec["t0"] + 1.0)
    label = re.sub(r'[\\/:*?"<>|]+', " ", name).strip() or "telecamera"
    stamp = time.strftime("%Y-%m-%d %H.%M.%S", time.localtime(rec["t0"]))
    out = recordings_dir() / f"{label} {stamp}.mp4"
    base = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    attempts = [
        base + ["-ss", f"{start:.2f}", "-i", str(s["sess"]), "-t", f"{length:.2f}", "-c", "copy", "-movflags", "+faststart", str(out)],
        base + ["-i", str(s["sess"]), "-c", "copy", "-movflags", "+faststart", str(out)],  # ripiego: tutta la sessione
    ]
    for cmd in attempts:
        if not s["sess"].exists():
            break
        proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL, **NO_WINDOW)
        try:
            await asyncio.wait_for(proc.wait(), 120)
        except asyncio.TimeoutError:
            proc.kill()
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            info = {
                "id": rec["id"], "camera": name, "filename": out.name, "path": str(out),
                "seconds": int(length - 1), "size": out.stat().st_size,
            }
            RECORDINGS[rec["id"]] = info
            LAST_REC[name] = info
            log.info("Registrazione di %s salvata in %s (%d byte)", name, out, info["size"])
            return info
        log.warning("Registrazione di %s: taglio non riuscito (codice %s), riprovo", name, proc.returncode)
    log.error("Registrazione di %s persa: file di sessione non utilizzabile", name)
    return None


async def watch_stream(name):
    s = streams.get(name)
    if not s:
        return
    await s["proc"].wait()
    if streams.get(name) is s:
        await stop_stream(name)


async def open_stream(name, cam):
    """Chiede la live a Blink e avvia ffmpeg; restituisce la voce di `streams`."""
    try:
        resp = await blink_api.request_camera_liveview(
            state["blink"], cam.sync.network_id, cam.camera_id, camera_type=cam.camera_type
        )
    except Exception as exc:
        log.exception("Richiesta live a Blink fallita per %s", name)
        raise HTTPException(502, f"Blink non ha avviato la live: {exc}")
    if not isinstance(resp, dict) or "server" not in resp:
        # Blink ha risposto con un errore (es. telecamera occupata o offline)
        log.error("Blink non ha avviato la live di %s, risposta: %s", name, resp)
        msg = resp.get("message") if isinstance(resp, dict) else None
        raise HTTPException(502, f"Blink non ha avviato la live: {msg or resp or 'nessuna risposta'}")
    if not str(resp["server"]).startswith("immis://"):
        raise HTTPException(501, f"Live non supportata da questa telecamera: {resp['server']}")
    live = BlinkLiveStream(cam, resp)

    await live.start()  # server TCP locale su 127.0.0.1:porta-casuale
    asyncio.create_task(live.feed())

    out_dir = HLS_ROOT / secrets.token_hex(8)
    out_dir.mkdir()
    # ffmpeg produce un flusso MPEG-TS continuo su stdout: niente pezzetti ne' liste da aspettare,
    # la finestra riceve il video via WebSocket e lo mostra dal primo fotogramma chiave.
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-fflags", "nobuffer", "-flags", "low_delay", "-probesize", "200000", "-analyzeduration", "500000",
        "-i", live.url,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy", "-bsf:v", "dump_extra",  # ripete SPS/PPS a ogni fotogramma chiave (chi si collega dopo decodifica subito)
        "-c:a", "aac", "-ar", "16000", "-b:a", "32k",
        "-muxdelay", "0", "-muxpreload", "0", "-flush_packets", "1",
        "-f", "mpegts", "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, **NO_WINDOW
    )
    # Secondo ffmpeg: registra tutta la sessione in un file .ts; "Registra" ne ritaglia un tratto.
    sess = out_dir / "sessione.ts"
    rec_cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-fflags", "nobuffer", "-probesize", "500000", "-analyzeduration", "1000000",
        "-i", live.url,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy", "-c:a", "aac", "-ar", "16000", "-b:a", "32k",
        "-flush_packets", "1",  # scrive subito su disco: il taglio deve trovare il video gia' registrato
        "-f", "mpegts", str(sess),
    ]
    try:
        rec = await asyncio.create_subprocess_exec(*rec_cmd, stdin=asyncio.subprocess.PIPE, **NO_WINDOW)
    except Exception as exc:
        log.warning("Registratore non avviato per %s: %s", name, exc)
        rec = None
    streams[name] = {
        "live": live, "proc": proc, "rec": rec, "sess": sess, "dir": out_dir,
        "started": time.time(), "recording": None, "lock": asyncio.Lock(),
        "clients": set(), "prebuf": bytearray(), "had_client": False,
        "ts_ready": asyncio.Event(), "bytes": 0,
    }
    asyncio.create_task(pump_stream(name, streams[name]))
    asyncio.create_task(watch_stream(name))
    return streams[name]


async def pump_stream(name, s):
    """Legge il flusso TS da ffmpeg e lo distribuisce alle finestre collegate."""
    proc = s["proc"]
    rest = b""
    try:
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            data = rest + chunk
            cut = len(data) - len(data) % 188  # solo pacchetti TS interi (188 byte)
            rest = data[cut:]
            out = data[:cut]
            if not out:
                continue
            s["bytes"] += len(out)
            if not s["ts_ready"].is_set():
                s["ts_ready"].set()
            if not s["had_client"] and len(s["prebuf"]) < 4_000_000:
                s["prebuf"] += out  # inizio del flusso, tenuto finche' la prima finestra non si collega
            for q in list(s["clients"]):
                if q.qsize() > 200:  # finestra troppo lenta: scarto il pezzo piu' vecchio
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(out)
    except Exception:
        log.exception("Errore nella distribuzione del video di %s", name)
    finally:
        for q in list(s["clients"]):
            q.put_nowait(None)  # fine del flusso


def stream_by_sid(sid):
    for name, s in streams.items():
        if s["dir"].name == sid:
            return name, s
    return None, None


@app.websocket("/ws/live/{sid}")
async def ws_live(websocket: WebSocket, sid: str):
    token = websocket.cookies.get("bl_session")
    if not state["blink"] or not token or token not in state["web_tokens"]:
        await websocket.close(code=4401)
        return
    name, s = stream_by_sid(sid)
    if s is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    q = asyncio.Queue()
    if not s["had_client"]:
        s["had_client"] = True
        if s["prebuf"]:
            q.put_nowait(bytes(s["prebuf"]))
        s["prebuf"] = bytearray()
    s["clients"].add(q)
    try:
        while True:
            data = await q.get()
            if data is None:
                break
            await websocket.send_bytes(data)
    except Exception:
        pass  # finestra chiusa o scheda cambiata
    finally:
        s["clients"].discard(q)
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/api/cameras/{name}/live")
async def start_live(name: str, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    cam = find_camera(name)
    for attempt in (1, 2):
        cur = streams.get(name)
        if cur and cur["proc"].returncode is not None:
            await stop_stream(name)
            cur = None
        if cur is None:
            cur = await open_stream(name, cam)
        else:
            log.info("Live di %s gia' in corso: la riuso", name)
        out_dir, proc, live = cur["dir"], cur["proc"], cur["live"]
        # aspetta i primi byte di video (max ~60 s: le telecamere a batteria impiegano
        # anche 20-30 s a svegliarsi)
        t0 = time.time()
        for _ in range(240):
            if cur["ts_ready"].is_set():
                log.info("Live di %s avviata in %.1f s", name, time.time() - t0)
                return {"ok": True, "sid": out_dir.name, "ws": f"/ws/live/{out_dir.name}"}
            if proc.returncode is not None:
                log.error("Live di %s: ffmpeg terminato (codice %s)", name, proc.returncode)
                break
            if streams.get(name) is not cur:
                raise HTTPException(409, "Live interrotta")
            await asyncio.sleep(0.25)
        got_video = getattr(live, "_bl_first_packet", False)
        log.warning("Live di %s: nessun video dopo %.0f s", name, time.time() - t0)
        if streams.get(name) is cur:
            await stop_stream(name)
        if attempt == 1 and not got_video:
            # Blink ha accettato la richiesta ma la telecamera non ha mandato nulla
            # (succede subito dopo uno stop): riproviamo una volta
            log.info("Live di %s: riprovo", name)
            await asyncio.sleep(2)
            continue
        break
    raise HTTPException(504, "La telecamera non ha risposto. Riprova tra qualche secondo.")


@app.delete("/api/cameras/{name}/live")
async def end_live(name: str, sid: str | None = None, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    s = streams.get(name)
    if s and sid and s["dir"].name != sid:
        # la richiesta riguarda una live precedente (es. scheda rimasta aperta): ignorata
        return {"ok": True, "ignored": True}
    await stop_stream(name)
    return {"ok": True}


# ---------------------------------------------------------------- registrazioni
@app.post("/api/cameras/{name}/record")
async def record_start(name: str, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    find_camera(name)
    s = streams.get(name)
    if not s or s["proc"].returncode is not None:
        raise HTTPException(409, "Avvia prima la live")
    if s.get("rec") is None or s["rec"].returncode is not None:
        raise HTTPException(500, "Registratore non attivo: ferma e riavvia la live")
    if s["recording"]:
        return {"ok": True, "id": s["recording"]["id"], "already": True}
    s["recording"] = {"id": secrets.token_hex(8), "t0": time.time()}
    log.info("Registrazione di %s avviata", name)
    return {"ok": True, "id": s["recording"]["id"]}


@app.post("/api/cameras/{name}/record/stop")
async def record_stop(name: str, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    s = streams.get(name)
    if s and s.get("recording"):
        info = await finalize_recording(name, s)
        if info:
            return info
        raise HTTPException(500, "Registrazione non riuscita")
    # la live e' finita mentre registravamo: la chiusura della live ha gia' salvato il file
    for _ in range(240):
        info = LAST_REC.pop(name, None)
        if info:
            return info
        if name not in FINALIZING:
            break
        await asyncio.sleep(0.25)
    raise HTTPException(404, "Nessuna registrazione in corso")


def _is_local(request):
    return request.client.host in ("127.0.0.1", "::1")


@app.post("/api/recordings/{rid}/save")
async def recording_save(rid: str, request: Request, bl_session: str = Cookie(None)):
    """Finestra "Salva con nome" (solo nella finestra del programma); altrimenti download."""
    require_auth(bl_session)
    info = RECORDINGS.get(rid)
    if not info:
        raise HTTPException(404, "Registrazione non trovata")
    win = state["window"]
    if win is None or not _is_local(request):
        return {"download": f"/api/recordings/{rid}/download", "path": info["path"]}
    import webview

    def ask():
        r = win.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(Path(info["path"]).parent), save_filename=info["filename"]
        )
        if isinstance(r, (list, tuple)):
            r = r[0] if r else None
        return str(r) if r else None

    try:
        dest = await asyncio.to_thread(ask)
    except Exception as exc:
        log.warning("Finestra di salvataggio non disponibile: %s", exc)
        dest = None
    if not dest:
        return {"saved": None, "path": info["path"]}
    dest = Path(dest)
    if dest.suffix.lower() != ".mp4":
        dest = dest.with_suffix(".mp4")
    src = Path(info["path"])
    if dest.resolve() != src.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        info["path"] = str(dest)
        info["filename"] = dest.name
    return {"saved": str(dest), "path": str(dest)}


@app.get("/api/recordings/{rid}/download")
async def recording_download(rid: str, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    info = RECORDINGS.get(rid)
    if not info or not Path(info["path"]).exists():
        raise HTTPException(404, "Registrazione non trovata")
    return FileResponse(info["path"], media_type="video/mp4", filename=info["filename"])


@app.post("/api/recordings/{rid}/reveal")
async def recording_reveal(rid: str, request: Request, bl_session: str = Cookie(None)):
    """Mostra il file nel Finder / Esplora file (solo dal computer stesso)."""
    require_auth(bl_session)
    info = RECORDINGS.get(rid)
    if not info or not _is_local(request):
        raise HTTPException(404, "Registrazione non trovata")
    path = info["path"]
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            subprocess.Popen(["xdg-open", str(Path(path).parent)])
    except Exception as exc:
        log.warning("Non riesco ad aprire la cartella: %s", exc)
    return {"ok": True}


# ---------------------------------------------------------------- clip registrate da Blink
# Due provenienze:
#  - cloud Blink (con abbonamento): elenco e video si scaricano direttamente;
#  - memoria locale del Sync Module (chiavetta USB): si chiede al modulo l'elenco
#    ("manifest") e, per ogni clip aperta, di caricarla sul server Blink da cui poi la si scarica.
# Il modulo fa una cosa alla volta: le richieste alla chiavetta sono in fila (un lucchetto
# per sistema) e si fanno solo quando si apre la sezione Clip, mai in sottofondo.
CLIPS = {"list": None, "at": 0.0, "notes": []}   # elenco in cache (5 min: leggere la chiavetta richiede ~25 s)
CLIP_LOCKS = {}                                    # network_id -> asyncio.Lock
CLIP_DIR = HLS_ROOT / "clips"


def _iso_to_epoch(value):
    from datetime import datetime, timezone

    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def _sync_by_network_id(net_id):
    for sm in getattr(state["blink"], "sync", {}).values():
        if str(getattr(sm, "network_id", "")) == str(net_id):
            return sm
    return None


def _live_on_network(sm):
    """True se c'e' una live in corso su una telecamera di questo sistema."""
    return any(getattr(s["live"].camera, "sync", None) is sm for s in streams.values())


def _clip_lock(net_id):
    return CLIP_LOCKS.setdefault(str(net_id), asyncio.Lock())


async def _cloud_clips(days):
    blink = state["blink"]
    since = time.time() - days * 86400
    out = []
    for page in range(1, 6):  # ~25 clip per pagina
        resp = await blink_api.request_videos(blink, time=since, page=page)
        media = (resp or {}).get("media") if isinstance(resp, dict) else None
        if not media:
            break
        for m in media:
            if m.get("deleted") or not m.get("media"):
                continue
            out.append({
                "id": f"c-{m['id']}", "source": "cloud",
                "camera": m.get("device_name") or "", "network": m.get("network_name") or "",
                "time": _iso_to_epoch(m.get("created_at")),
                "thumb": bool(m.get("thumbnail")), "_media": m.get("media"), "_thumb": m.get("thumbnail"),
            })
    return out


async def _local_clips(sm):
    """Elenco delle clip sulla chiavetta del Sync Module `sm`."""
    async with _clip_lock(sm.network_id):
        resp = await sm.poll_local_storage_manifest()
        req_id = (resp or {}).get("id") if isinstance(resp, dict) else None
        if not req_id:
            raise RuntimeError("il Sync Module non ha risposto (forse e' occupato)")
        resp = await sm.poll_local_storage_manifest(req_id)
        if not isinstance(resp, dict) or "clips" not in resp:
            raise RuntimeError("elenco delle clip non disponibile")
    manifest = resp.get("manifest_id")
    names = getattr(sm, "_names_table", {}) or {}
    out = []
    for c in resp.get("clips", []):
        out.append({
            "id": f"l-{sm.network_id}-{manifest}-{c['id']}", "source": "local",
            "camera": names.get(c.get("camera_name"), c.get("camera_name") or ""), "network": sm.name,
            "time": _iso_to_epoch(c.get("created_at")), "size": c.get("size"), "thumb": False,
        })
    return out


@app.get("/api/clips")
async def clips(days: int = 14, refresh: bool = False, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    if CLIPS["list"] is not None and not refresh and time.time() - CLIPS["at"] < 300:
        return {"clips": _public(CLIPS["list"]), "notes": CLIPS["notes"]}
    days = max(1, min(days, 60))
    found, notes = [], []
    try:
        found += await _cloud_clips(days)
    except Exception as exc:
        log.warning("Clip nel cloud non disponibili: %s", exc)
        notes.append("Clip nel cloud Blink non disponibili in questo momento.")
    for sm in getattr(state["blink"], "sync", {}).values():
        ls = getattr(sm, "_local_storage", {}) or {}
        if not (ls.get("enabled") or ls.get("compatible") and ls.get("status")):
            continue
        if _live_on_network(sm):
            notes.append(f"{sm.name}: chiavetta non letta perche' c'e' una live in corso.")
            continue
        try:
            found += await _local_clips(sm)
        except Exception as exc:
            log.warning("Clip sulla chiavetta di %s non disponibili: %s", sm.name, exc)
            notes.append(f"{sm.name}: la chiavetta del Sync Module non ha risposto, riprova tra poco.")
    found.sort(key=lambda c: c["time"] or 0, reverse=True)
    for c in found:  # clip della chiavetta gia' aperte: hanno la miniatura dal video
        if c["source"] == "local":
            c["thumb"] = (CLIP_DIR / (re.sub(r"[^A-Za-z0-9_-]", "_", c["id"]) + ".mp4")).exists()
    CLIPS.update(list=found, at=time.time(), notes=notes)
    log.info("Clip trovate: %d (%s)", len(found), ", ".join(notes) or "ok")
    return {"clips": _public(found), "notes": notes}


def _public(items):
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in items]


def _find_clip(cid):
    for c in CLIPS["list"] or []:
        if c["id"] == cid:
            return c
    raise HTTPException(404, "Clip non trovata: ricarica l'elenco")


async def _fetch_clip(c):
    """Scarica la clip `c` (una volta sola) e restituisce il percorso del file .mp4."""
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    path = CLIP_DIR / (re.sub(r"[^A-Za-z0-9_-]", "_", c["id"]) + ".mp4")
    if path.exists() and path.stat().st_size > 1000:
        return path
    blink = state["blink"]
    if c["source"] == "cloud":
        r = await blink_api.http_get(blink, url=blink.urls.base_url + c["_media"], stream=True, json=False, timeout=90)
        if r is None or r.status != 200:
            raise HTTPException(502, "Blink non ha restituito la clip")
        data = await r.read()
    else:
        _, net_id, manifest, clip_id = c["id"].split("-", 3)
        sm = _sync_by_network_id(net_id)
        if sm is None:
            raise HTTPException(404, "Sistema non trovato")
        if _live_on_network(sm):
            raise HTTPException(409, "C'e' una live in corso su questo sistema: fermala per aprire le clip della chiavetta")
        import string

        url = blink.urls.base_url + string.Template(blink_api.local_storage_clip_url_template()).substitute(
            account_id=blink.account_id, network_id=net_id, sync_id=sm.sync_id, manifest_id=manifest, clip_id=clip_id,
        )
        data = None
        async with _clip_lock(net_id):
            # 1) il modulo carica la clip sul server Blink  2) la si scarica
            await blink_api.request_local_storage_clip(blink, net_id, sm.sync_id, manifest, clip_id)
            for attempt in range(6):
                r = await blink_api.http_get(blink, url, stream=True, json=False, timeout=90)
                if r is not None and r.status == 200:
                    data = await r.read()
                    break
                await asyncio.sleep(2 + attempt * 2)
        if not data:
            raise HTTPException(504, "Il Sync Module non ha caricato la clip: riprova tra poco")
    if len(data) < 1000:
        raise HTTPException(502, "Clip vuota o non valida")
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    log.info("Clip %s di %s scaricata (%d byte)", c["id"], c["camera"], len(data))
    return path


@app.get("/api/clips/{cid}/video")
async def clip_video(cid: str, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    path = await _fetch_clip(_find_clip(cid))
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/clips/{cid}/thumbnail")
async def clip_thumbnail(cid: str, bl_session: str = Cookie(None)):
    require_auth(bl_session)
    c = _find_clip(cid)
    if not c.get("_thumb"):
        # clip della chiavetta: nessuna miniatura da Blink; se la clip e' gia' stata aperta
        # si usa un fotogramma del video scaricato
        video = CLIP_DIR / (re.sub(r"[^A-Za-z0-9_-]", "_", cid) + ".mp4")
        jpg = video.with_suffix(".jpg")
        if video.exists() and not jpg.exists():
            proc = await asyncio.create_subprocess_exec(
                FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", str(video),
                "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", "-y", str(jpg),
                stdin=asyncio.subprocess.DEVNULL, **NO_WINDOW,
            )
            await proc.wait()
        if jpg.exists():
            return FileResponse(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        raise HTTPException(404, "Nessuna miniatura")
    blink = state["blink"]
    thumb = c["_thumb"]
    url = thumb if thumb.startswith("http") else blink.urls.base_url + thumb
    if not re.search(r"\.jpe?g|[?&]ext=", url):
        url += ".jpg"
    r = await blink_api.http_get(blink, url, stream=True, json=False)
    if r is None or r.status != 200:
        raise HTTPException(404, "Nessuna miniatura")
    return Response(await r.read(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/clips/{cid}/keep")
async def clip_keep(cid: str, bl_session: str = Cookie(None)):
    """Copia la clip tra le registrazioni, cosi' si salva con lo stesso "Salva con nome"."""
    require_auth(bl_session)
    c = _find_clip(cid)
    src = await _fetch_clip(c)
    label = re.sub(r'[\\/:*?"<>|]+', " ", c["camera"]).strip() or "telecamera"
    stamp = time.strftime("%Y-%m-%d %H.%M.%S", time.localtime(c["time"] or time.time()))
    out = recordings_dir() / f"{label} {stamp} (clip Blink).mp4"
    shutil.copyfile(src, out)
    rid = secrets.token_hex(8)
    RECORDINGS[rid] = {"id": rid, "camera": c["camera"], "filename": out.name, "path": str(out),
                       "seconds": None, "size": out.stat().st_size}
    return RECORDINGS[rid]


# ---------------------------------------------------------------- pagina
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
async def index():
    # no-store: dopo un aggiornamento la finestra deve mostrare la pagina nuova, non quella in cache
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-store"})


def port_free(host, port):
    """True se nessuno risponde sulla porta (prova a collegarsi, non a fare bind:
    su macOS un bind su 127.0.0.1 riesce anche se un altro programma ascolta su 0.0.0.0)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return False
    except OSError:
        return True


def blinklive_on(port):
    """True se su quella porta risponde gia' un MatteoEye."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return "logged_in" in json.loads(r.read().decode())
    except Exception:
        return False


def main():
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--lan", action="store_true", help="rendi il sito visibile in rete locale")
    p.add_argument("--app", action="store_true", help="modalita' programma (vedi in alto)")
    p.add_argument("--browser", action="store_true", help="in modalita' programma usa il browser invece della finestra")
    p.add_argument("--port", type=int, default=8787)
    a = p.parse_args()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    port = a.port
    use_window = APP_MODE and not a.browser and not a.lan
    if not port_free(host, port):
        if blinklive_on(port):
            # c'e' gia' un MatteoEye acceso su questo computer: mostriamo solo la pagina
            url = f"http://127.0.0.1:{port}"
            print(f"MatteoEye e' gia' in esecuzione su {url}")
            if use_window:
                try:
                    run_window(url)
                    return
                except Exception as exc:
                    log.warning("Finestra non disponibile (%s): apro il browser", exc)
            _open_browser(url)
            return
        # porta occupata da un altro programma: ne scegliamo una libera
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    state["server"] = server
    print(f"\n  MatteoEye in esecuzione su {url}\n  (chiudi questa finestra per spegnerlo)\n")
    log.info("MatteoEye avviato su %s (dati in %s)", url, DATA)
    if use_window:
        # server in un thread, finestra nel thread principale (obbligatorio su macOS)
        import threading

        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        for _ in range(150):
            if blinklive_on(port) or not t.is_alive():
                break
            time.sleep(0.1)
        if not t.is_alive():
            raise RuntimeError("il server interno non e' partito")
        try:
            run_window(url)
        except Exception as exc:
            log.warning("Finestra non disponibile (%s): apro il browser", exc)
            _open_browser(url)
            t.join()
        else:
            log.info("Finestra chiusa: chiudo MatteoEye")
            quit_app()
            t.join(timeout=10)
        return
    state["open_url"] = url
    server.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.exception("Errore fatale")
        fatal_box(f"MatteoEye non e' riuscito a partire:\n{exc}\n\nDettagli nel file di log:\n{LOG_FILE}")
        sys.exit(1)
