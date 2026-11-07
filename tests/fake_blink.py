"""Blink simulato per le prove locali: telecamera finta che manda un video di prova (ffmpeg testsrc)."""
import asyncio, os, sys, time
from pathlib import Path

HERE = Path(__file__).parent
os.environ.setdefault("BLINKLIVE_DATA", str(HERE / "appdata_fake"))
sys.path.insert(0, str(HERE.parent))  # cartella del progetto (app.py)


def install(A, dead_first=False, gop=15, fps=15):
    """Sostituisce in `A` (il modulo app) la parte Blink con una simulazione."""
    calls = {"n": 0}

    class FakeLive:
        def __init__(self, cam, resp):
            self.camera = cam; self.server = None; self.procs = []; self.dead = resp.get("dead", False)
        async def start(self):
            self.server = await asyncio.start_server(self.join, "127.0.0.1", 0)
        @property
        def url(self):
            s = self.server.sockets[0].getsockname(); return f"tcp://{s[0]}:{s[1]}"
        async def join(self, r, w):
            if self.dead:
                w.close(); return
            self._bl_first_packet = True; self._bl_first_packet_time = time.time()
            p = await asyncio.create_subprocess_exec(
                A.FFMPEG, "-hide_banner", "-loglevel", "error", "-re",
                "-f", "lavfi", "-i", f"testsrc=size=320x240:rate={fps}", "-f", "lavfi", "-i", "sine=frequency=440",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", str(gop),
                "-c:a", "aac", "-f", "mpegts", "-", stdout=asyncio.subprocess.PIPE)
            self.procs.append(p)
            try:
                while True:
                    b = await p.stdout.read(1316)
                    if not b: break
                    w.write(b); await w.drain()
            except Exception: pass
            finally:
                w.close()
        async def feed(self): pass
        def stop(self):
            if self.server: self.server.close()
            for q in self.procs:
                if q.returncode is None: q.kill()

    async def fake_liveview(blink, net, cid, camera_type=""):
        calls["n"] += 1
        return {"server": "immis://x", "command_id": 1, "polling_interval": 5, "dead": dead_first and calls["n"] == 1}

    class Cam:
        def __init__(s, n):
            s.name = n; s.camera_id = 1; s.camera_type = ""; s.product_type = "catalina"
            s.sync = type("S", (), {"network_id": 1})()
            s.battery = "ok"; s.temperature_c = 21; s.arm = True; s.motion_detected = False; s.wifi_strength = -50
            s.image_from_cache = None
        async def get_media(s): return None
        async def snap_picture(s): return None

    class FakeBlink:
        cameras = {"Cam A": Cam("Cam A"), "Cam B": Cam("Cam B")}
        sync = {}
        async def refresh(self, force=False, force_cache=False): return True

    A.BlinkLiveStream = FakeLive
    A.blink_api.request_camera_liveview = fake_liveview
    A.state["blink"] = FakeBlink()
    return calls
