"""Prova del flusso via WebSocket con il server vero (uvicorn) e un client aiohttp, Blink simulato."""
import asyncio, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import fake_blink
sys.argv = ["app.py"]
import app as A
import uvicorn
from aiohttp import ClientSession, WSMsgType

fake_blink.install(A, dead_first=True)
PORT = 8811
tok = A.new_web_token()


def run_server():
    srv = uvicorn.Server(uvicorn.Config(A.app, host="127.0.0.1", port=PORT, log_level="warning"))
    A.state["server"] = srv
    srv.run()


async def main():
    threading.Thread(target=run_server, daemon=True).start()
    for _ in range(50):
        if A.blinklive_on(PORT): break
        await asyncio.sleep(0.1)
    async with ClientSession(cookies={"bl_session": tok}) as cs:
        t0 = time.time()
        async with cs.post(f"http://127.0.0.1:{PORT}/api/cameras/Cam%20A/live") as r:
            live = await r.json()
        print(f"1) live avviata in {time.time()-t0:.1f}s (1° tentativo muto, poi riprova):", live)
        assert live.get("ws") and live.get("sid")
        # senza cookie valido il WebSocket viene rifiutato
        async with ClientSession(cookies={"bl_session": "x"}) as bad:
            try:
                async with bad.ws_connect(f"ws://127.0.0.1:{PORT}{live['ws']}") as ws:
                    m = await ws.receive(timeout=5); print("2) senza sessione ->", m.type.name, getattr(m, "data", None))
                    assert m.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
            except Exception as e:
                print("2) senza sessione -> rifiutato:", type(e).__name__)
        # client 1: riceve l'inizio del flusso (prebuffer) e poi il resto
        total = 0; first = None; n = 0; t1 = time.time()
        async with cs.ws_connect(f"ws://127.0.0.1:{PORT}{live['ws']}") as ws:
            while time.time() - t1 < 4:
                m = await ws.receive(timeout=5)
                if m.type != WSMsgType.BINARY: print("   msg:", m.type.name); break
                if first is None: first = m.data
                total += len(m.data); n += 1
            print(f"3) client 1: {n} messaggi, {total} byte in 4 s | primo byte 0x{first[0]:02x} | lunghezze multiple di 188: {total % 188 == 0}")
            assert first[0] == 0x47 and total % 188 == 0 and total > 50_000
            # client 2 si collega a flusso gia' avviato
            total2 = 0; t2 = time.time()
            async with cs.ws_connect(f"ws://127.0.0.1:{PORT}{live['ws']}") as ws2:
                while time.time() - t2 < 3:
                    m = await ws2.receive(timeout=5)
                    if m.type != WSMsgType.BINARY: break
                    if total2 == 0: assert m.data[0] == 0x47
                    total2 += len(m.data)
            print(f"4) client 2 (collegato dopo): {total2} byte in 3 s, allineato ai pacchetti: {total2 % 188 == 0}")
            assert total2 > 30_000
            # stop della live: il server chiude il WebSocket
            async with cs.delete(f"http://127.0.0.1:{PORT}/api/cameras/Cam%20A/live?sid={live['sid']}") as r:
                print("5) DELETE live ->", await r.json())
            closed = False; t3 = time.time()
            while time.time() - t3 < 5:
                m = await ws.receive(timeout=5)
                if m.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING): closed = True; break
            print("6) WebSocket chiuso dal server dopo lo stop:", closed)
            assert closed
        # Registra funziona ancora col nuovo flusso
        async with cs.post(f"http://127.0.0.1:{PORT}/api/cameras/Cam%20B/live") as r:
            live_b = await r.json()
        async with cs.post(f"http://127.0.0.1:{PORT}/api/cameras/Cam%20B/record") as r:
            rec = await r.json()
        await asyncio.sleep(3)
        async with cs.post(f"http://127.0.0.1:{PORT}/api/cameras/Cam%20B/record/stop") as r:
            info = await r.json()
        print(f"7) registrazione con il nuovo flusso: {info.get('filename')} | {info.get('size')} byte")
        assert info.get("size", 0) > 1000
        Path(info["path"]).unlink(missing_ok=True)
        async with cs.delete(f"http://127.0.0.1:{PORT}/api/cameras/Cam%20B/live?sid={live_b['sid']}") as r:
            await r.json()
    A.state["server"].should_exit = True
    print("TUTTO OK")

asyncio.run(main())
