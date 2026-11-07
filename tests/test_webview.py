"""Prova nella finestra vera (WebKit, come l'app Mac): apre la pagina, preme Live e controlla che il video scorra."""
import asyncio, json, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import fake_blink
sys.argv = ["app.py", "--app"]
import app as A
import uvicorn, webview

fake_blink.install(A)
PORT = 8812
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 12


def run_server():
    srv = uvicorn.Server(uvicorn.Config(A.app, host="127.0.0.1", port=PORT, log_level="warning"))
    A.state["server"] = srv
    srv.run()


threading.Thread(target=run_server, daemon=True).start()
for _ in range(50):
    if A.blinklive_on(PORT): break
    time.sleep(0.1)

result = {}


def drive(win):
    try:
        for _ in range(60):  # aspetta la lista telecamere (login ripreso da localhost)
            if win.evaluate_js('document.querySelectorAll("[data-act=live]").length') >= 1: break
            time.sleep(0.25)
        result["supporto"] = win.evaluate_js("!!(window.mpegts && mpegts.isSupported())")
        t0 = time.time()
        win.evaluate_js('document.querySelector("[data-act=live]").click()')
        started = None
        for _ in range(WAIT * 4):
            time.sleep(0.25)
            st = win.evaluate_js('(()=>{const v=document.querySelector("video"); return v? JSON.stringify({t:v.currentTime, rs:v.readyState, w:v.videoWidth, h:v.videoHeight, paused:v.paused}) : "null"})()')
            st = json.loads(st) if st and st != "null" else None
            if st and st["t"] > 0.5 and started is None:
                started = time.time() - t0
            result["stato"] = st
        result["partito_dopo_s"] = started
        result["overlay"] = win.evaluate_js('(()=>{const o=document.querySelector(".spinner"); return o? o.textContent : ""})()')
        # stop e chiusura pulita
        win.evaluate_js('document.querySelector("[data-act=live]").click()')
        time.sleep(1)
        result["live_dopo_stop"] = list(A.streams.keys())
    except Exception as e:
        result["errore"] = repr(e)
    finally:
        win.destroy()


win = webview.create_window("Prova MatteoEye", f"http://127.0.0.1:{PORT}/", width=900, height=700)
webview.start(drive, win, private_mode=True)
A.state["server"].should_exit = True
print(json.dumps(result, ensure_ascii=False, indent=1))
ok = result.get("supporto") and result.get("stato") and result["stato"]["t"] > 2 and result["stato"]["w"] == 320 and not result["stato"]["paused"]
print("WEBVIEW OK" if ok else "WEBVIEW FALLITO")
