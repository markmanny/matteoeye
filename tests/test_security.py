"""Prove di sicurezza con il server vero (uvicorn) e Blink simulato.

Simula gli attacchi che MatteoEye deve respingere:
- "DNS rebinding": un sito che si fa risolvere in 127.0.0.1 e parla con MatteoEye
  attraverso il browser (arriva con il proprio nome nell'intestazione Host);
- tunnel pubblico (Tailscale Funnel, ngrok) con o senza intestazioni X-Forwarded-*;
- comandi e WebSocket mandati da un altro sito;
- tentativi di password a ripetizione;
e controlla che il programma resti comodo: dal computer stesso si entra senza password
e le sessioni sopravvivono a un riavvio.
"""
import asyncio, sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import fake_blink
sys.argv = ["app.py"]
import app as A
import uvicorn
from aiohttp import ClientSession, WSServerHandshakeError

fake_blink.install(A)
PORT = 8814
BASE = f"http://127.0.0.1:{PORT}"
LOCAL = {"Origin": BASE}
ok_count = 0


def check(cond, msg):
    global ok_count
    if not cond:
        raise AssertionError(msg)
    ok_count += 1
    print("  ok -", msg)


class FailingBlink:
    """Login Blink che rifiuta sempre email e password."""
    def __init__(self, session=None):
        self.auth = None
    async def start(self):
        return False


def run_server():
    srv = uvicorn.Server(uvicorn.Config(A.app, host="127.0.0.1", port=PORT, log_level="warning"))
    A.state["server"] = srv
    srv.run()


async def status(cs, **headers):
    async with cs.get(BASE + "/api/status", headers=headers) as r:
        return r.status, (await r.json())


async def post(cs, path, json=None, **headers):
    async with cs.post(BASE + path, json=json if json is not None else {}, headers=headers) as r:
        return r.status, (await r.json()), r.cookies.get("bl_session")


async def main():
    threading.Thread(target=run_server, daemon=True).start()
    for _ in range(50):
        if A.blinklive_on(PORT):
            break
        await asyncio.sleep(0.1)

    async with ClientSession() as cs:
        print("1) dal computer stesso")
        st, js = await status(cs)
        check(st == 200 and js["resumable"], "la pagina locale puo' entrare senza password")
        st, js, cookie = await post(cs, "/api/resume", **LOCAL)
        check(st == 200 and cookie is not None, "accesso senza password dalla pagina locale")
        token = cookie.value

        print("2) DNS rebinding (Host di un altro sito)")
        st, _ = await status(cs, Host=f"attacker.example:{PORT}")
        check(st == 403, "richiesta con Host estraneo rifiutata")
        st, _, _ = await post(cs, "/api/resume", Host=f"attacker.example:{PORT}", Origin=f"http://attacker.example:{PORT}")
        check(st == 403, "accesso senza password rifiutato al sito estraneo")

        print("3) comandi da un altro sito")
        st, _, _ = await post(cs, "/api/resume", Origin="http://attacker.example")
        check(st == 403, "POST con Origin di un altro sito rifiutato")
        st, _, _ = await post(cs, "/api/resume")
        check(st == 403, "accesso senza password rifiutato se manca l'Origin della pagina")

        print("4) tunnel pubblico")
        st, _, _ = await post(cs, "/api/resume", **LOCAL, **{"X-Forwarded-For": "203.0.113.9"})
        check(st == 403, "con X-Forwarded-For: password richiesta")
        A.state["lan"] = True
        try:
            st, js = await status(cs, Host="casa.tailnet.ts.net")
            check(st == 200 and not js["resumable"], "dal link pubblico la pagina chiede la password")
            st, _, _ = await post(cs, "/api/resume", Host="casa.tailnet.ts.net", Origin="https://casa.tailnet.ts.net")
            check(st == 403, "tunnel SENZA intestazioni (Host pubblico): password richiesta")
            st, _, _ = await post(cs, "/api/resume", Host=f"127.0.0.1:{PORT}", Origin="https://casa.tailnet.ts.net")
            check(st == 403, "tunnel che riscrive l'Host in localhost: password richiesta")
        finally:
            A.state["lan"] = False

        print("5) WebSocket da un altro sito")
        try:
            async with cs.ws_connect(BASE.replace("http", "ws") + "/ws/live/x", origin="http://attacker.example",
                                     headers={"Cookie": f"bl_session={token}"}) as ws:
                msg = await ws.receive(timeout=5)
                check(msg.type.name in ("CLOSE", "CLOSED"), "WebSocket da altro sito chiuso")
        except WSServerHandshakeError as e:
            check(e.status == 403, "WebSocket da altro sito rifiutato")

        print("6) tentativi di password")
        real_blink = A.Blink
        A.Blink = FailingBlink
        try:
            codes = []
            for _ in range(A.LOGIN_MAX_FAILS + 1):
                st, js, _ = await post(cs, "/api/login", {"email": "x@example.com", "password": "sbagliata"}, **LOCAL)
                codes.append(st)
            check(codes[:A.LOGIN_MAX_FAILS] == [401] * A.LOGIN_MAX_FAILS, "password sbagliate rifiutate")
            check(codes[-1] == 429 and "riprova" in js["detail"], "dopo 5 errori: pausa (" + js["detail"] + ")")
        finally:
            A.Blink = real_blink
            A._fails.clear()

        print("7) sessione dopo un riavvio")
        A.state["web_tokens"] = {}       # come un programma appena riavviato...
        A._load_sessions()               # ...che rilegge le sessioni salvate
        async with cs.get(BASE + "/api/cameras", headers={"Cookie": f"bl_session={token}"}) as r:
            check(r.status == 200, "il cookie resta valido dopo il riavvio, niente password")
        check(A.token_valid(token) and not A.token_valid("inventato"), "solo i cookie emessi sono validi")
        saved = A.SESSIONS_FILE.read_text()
        check(token not in saved, "su disco c'e' solo l'impronta del cookie, non il cookie")

        print("8) Esci")
        st, _, _ = await post(cs, "/api/logout", **LOCAL)
        check(st == 200 and not A.token_valid(token) and not A.SESSIONS_FILE.exists(), "Esci cancella tutte le sessioni")

    print(f"TUTTO OK ({ok_count} controlli)")


asyncio.run(main())
