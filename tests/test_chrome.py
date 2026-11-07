"""Prova con il motore Chromium (lo stesso di WebView2 su Windows): Chrome headless guidato via DevTools."""
import asyncio, json, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import fake_blink
sys.argv = ["app.py"]
import app as A
import uvicorn
from aiohttp import ClientSession

fake_blink.install(A)
PORT, DBG = 8813, 9333
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def run_server():
    srv = uvicorn.Server(uvicorn.Config(A.app, host="127.0.0.1", port=PORT, log_level="warning"))
    A.state["server"] = srv
    srv.run()


async def main():
    threading.Thread(target=run_server, daemon=True).start()
    for _ in range(50):
        if A.blinklive_on(PORT): break
        await asyncio.sleep(0.1)
    prof = tempfile.mkdtemp(prefix="chromeprof_")
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={DBG}", f"--user-data-dir={prof}",
                               "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--mute-audio",
                               "--window-size=1100,800", f"http://127.0.0.1:{PORT}/"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = {}
    try:
        async with ClientSession() as cs:
            targets = None
            for _ in range(100):
                try:
                    async with cs.get(f"http://127.0.0.1:{DBG}/json") as r:
                        targets = await r.json()
                    if any(t["type"] == "page" for t in targets): break
                except Exception:
                    pass
                await asyncio.sleep(0.2)
            page = next(t for t in targets if t["type"] == "page")
            async with cs.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                mid = 0
                async def ev(expr):
                    nonlocal mid
                    mid += 1
                    await ws.send_json({"id": mid, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}})
                    while True:
                        m = await ws.receive_json()
                        if m.get("id") == mid:
                            return m.get("result", {}).get("result", {}).get("value")
                for _ in range(80):
                    if (await ev('document.querySelectorAll("[data-act=live]").length') or 0) >= 1: break
                    await asyncio.sleep(0.25)
                result["supporto"] = await ev("!!(window.mpegts && mpegts.isSupported())")
                t0 = time.time()
                await ev('document.querySelector("[data-act=live]").click()')
                started = None; st = None
                for _ in range(14 * 4):
                    await asyncio.sleep(0.25)
                    raw = await ev('(()=>{const v=document.querySelector("video"); return v? JSON.stringify({t:v.currentTime, rs:v.readyState, w:v.videoWidth, h:v.videoHeight, paused:v.paused}) : "null"})()')
                    st = json.loads(raw) if raw and raw != "null" else None
                    if st and st["t"] > 0.5 and started is None:
                        started = time.time() - t0
                result["stato"] = st; result["partito_dopo_s"] = started
                result["overlay"] = await ev('(()=>{const o=document.querySelector(".spinner"); return o? o.textContent : ""})()')
                await ev('document.querySelector("[data-act=live]").click()')
                await asyncio.sleep(1)
                result["live_dopo_stop"] = list(A.streams.keys())
    except Exception as e:
        result["errore"] = repr(e)
    finally:
        chrome.terminate()
        try: chrome.wait(5)
        except Exception: chrome.kill()
        shutil.rmtree(prof, ignore_errors=True)
    A.state["server"].should_exit = True
    print(json.dumps(result, ensure_ascii=False, indent=1))
    st = result.get("stato")
    ok = result.get("supporto") and st and st["t"] > 2 and st["w"] == 320 and not st["paused"]
    print("CHROME OK" if ok else "CHROME FALLITO")

asyncio.run(main())
