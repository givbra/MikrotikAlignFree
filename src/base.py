#!/usr/bin/env python3
"""MikrotikAlignFree - безплатен инструмент за насочване на MikroTik антени.

Само стандартна библиотека на Python (3.8+, работи на Windows 7-11, Linux, macOS).
Стартира локален уеб сървър на 127.0.0.1:8080 и показва:
  * жив сигнал от антената (RouterOS API), пик, звукова индикация;
  * сигнал от двете страни - от втора антена през самата връзка или през другия
    компютър (синхронизация по MQTT);
  * тест на скоростта (/tool/bandwidth-test, изпълнява се от самата антена);
  * план на връзката: разстояние, азимут, наклон, зона на Френел, автоматично
    магнитно отклонение (модел WMM2025, изчислява се офлайн).
Не е официален продукт на MikroTik.
"""
import hashlib
import json
import math
import os
import random
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

APP_VERSION = "0.2.0"


def app_dir():
    """Папка за конфигурацията: до програмата, а ако е недостъпна за запис - в профила."""
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    if getattr(sys, "frozen", False) and os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA", base), "MikrotikAlignFree")
    os.makedirs(base, exist_ok=True)
    return base


BASE = app_dir()
CFG_PATH = os.path.join(BASE, "mikrotikalignfree_config.json")

DEFAULT_CFG = {
    "router_host": "192.168.88.1",
    "router_port": 8728,
    "router_ssl": False,
    "router_user": "admin",
    "router_password": "",
    "interface": "",
    "poll_ms": 500,
    "demo": False,
    "http_port": 8080,
    # втора антена (другата страна на връзката, достъпна през самата връзка)
    "router2_enabled": False,
    "router2_host": "",
    "router2_port": 8728,
    "router2_ssl": False,
    "router2_user": "admin",
    "router2_password": "",
    # тест на скоростта - данни за btest сървъра на отсрещната антена
    "btest_user": "",
    "btest_password": "",
    # синхронизация между два компютъра
    "mqtt_enabled": False,
    "mqtt_brokers": "mqtts://broker.emqx.io:8883, mqtts://broker.hivemq.com:8883",
    "room": "",
    "side": "a",
    "lang": "en",
}
SECRET_KEYS = [k for k in DEFAULT_CFG if k.endswith("password")]
CFG_VERSION = [0]


# --------------------------------------------------------------------------- конфигурация
def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg):
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_PATH)
    try:
        os.chmod(CFG_PATH, 0o600)
    except OSError:
        pass


def clean_room(value):
    value = str(value).strip()
    return value if re.fullmatch(r"[A-Za-z0-9_-]{8,40}", value) else ""


# --------------------------------------------------------------------------- RouterOS API
class RouterOSError(Exception):
    pass


def enc_len(n):
    if n < 0x80:
        return bytes([n])
    if n < 0x4000:
        return (n | 0x8000).to_bytes(2, "big")
    if n < 0x200000:
        return (n | 0xC00000).to_bytes(3, "big")
    if n < 0x10000000:
        return (n | 0xE0000000).to_bytes(4, "big")
    return b"\xf0" + n.to_bytes(4, "big")


class Api:
    def __init__(self, host, port=8728, use_ssl=False, timeout=4.0):
        sock = socket.create_connection((host, port), timeout)
        if use_ssl:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # RouterOS обикновено ползва самоподписан сертификат
            try:
                ctx.set_ciphers("ALL:@SECLEVEL=0")
            except ssl.SSLError:
                pass
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout)
        self.sock = sock

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _send_word(self, word):
        data = word.encode("utf-8")
        self.sock.sendall(enc_len(len(data)) + data)

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError(TR("връзката е прекъсната"))
            buf += chunk
        return buf

    def _read_len(self):
        b = self._recv(1)[0]
        if b < 0x80:
            return b
        if b < 0xC0:
            return ((b & 0x3F) << 8) | self._recv(1)[0]
        if b < 0xE0:
            r = self._recv(2)
            return ((b & 0x1F) << 16) | (r[0] << 8) | r[1]
        if b < 0xF0:
            r = self._recv(3)
            return ((b & 0x0F) << 24) | int.from_bytes(r, "big")
        return int.from_bytes(self._recv(4), "big")

    def _read_sentence(self):
        words = []
        while True:
            n = self._read_len()
            if n == 0:
                return words
            words.append(self._recv(n).decode("utf-8", "replace"))

    def _send_command(self, command, attrs):
        for w in (command,) + tuple(attrs):
            self._send_word(w)
        self._send_word("")

    @staticmethod
    def _attrs(sentence):
        data = {}
        for w in sentence[1:]:
            if w.startswith("="):
                k, _, v = w[1:].partition("=")
                data[k] = v
        return data

    def talk(self, command, *attrs):
        self._send_command(command, attrs)
        rows = []
        while True:
            sentence = self._read_sentence()
            if not sentence:
                continue
            kind, data = sentence[0], self._attrs(sentence)
            if kind == "!re":
                rows.append(data)
            elif kind == "!done":
                return rows, data
            elif kind == "!trap":
                raise RouterOSError(data.get("message", TR("грешка от RouterOS")))
            elif kind == "!fatal":
                raise RouterOSError(TR("фатална грешка от RouterOS"))

    def stream(self, command, attrs, on_row, stop, max_seconds):
        """Команда с продължителен изход (например bandwidth-test)."""
        self.sock.settimeout(12)
        self._send_command(command, attrs)
        end = time.time() + max_seconds
        while not stop.is_set() and time.time() < end:
            sentence = self._read_sentence()
            if not sentence:
                continue
            kind, data = sentence[0], self._attrs(sentence)
            if kind == "!re":
                on_row(data)
            elif kind == "!done":
                return
            elif kind == "!trap":
                raise RouterOSError(data.get("message", TR("грешка от RouterOS")))
            elif kind == "!fatal":
                raise RouterOSError(TR("фатална грешка от RouterOS"))

    def login(self, user, password):
        _, done = self.talk("/login", "=name=" + user, "=password=" + password)
        if "ret" in done:  # стар метод с предизвикателство (преди RouterOS 6.43)
            challenge = bytes.fromhex(done["ret"])
            digest = hashlib.md5(b"\x00" + password.encode("utf-8") + challenge).hexdigest()
            self.talk("/login", "=name=" + user, "=response=00" + digest)


# --------------------------------------------------------------------------- общо състояние
ST = {"local": {"ok": False, "error": TR("стартиране..."), "ts": 0, "peers": []}, "far": {"enabled": False},
      "mqtt": {"enabled": False}, "btest": {"state": "idle"}}
LOCK = threading.Lock()
NUM = re.compile(r"-?\d+(?:\.\d+)?")


def set_state(key, **kw):
    with LOCK:
        ST[key] = kw


def to_num(value):
    if value is None:
        return None
    m = NUM.search(str(value))
    return float(m.group(0)) if m else None


def parse_row(row):
    """Единен вид за двата пакета: wifi (RouterOS 7) и wireless (класически)."""
    return {
        "mac": row.get("mac-address", ""),
        "interface": row.get("interface", ""),
        "signal": to_num(row.get("signal", row.get("signal-strength"))),
        "tx_signal": to_num(row.get("tx-signal-strength")),
        "tx_rate": row.get("tx-rate", ""),
        "rx_rate": row.get("rx-rate", ""),
        "ccq": to_num(row.get("tx-ccq", row.get("ccq"))),
        "tx_ccq": to_num(row.get("tx-ccq", row.get("ccq"))),
        "rx_ccq": to_num(row.get("rx-ccq")),
        "ch0": to_num(row.get("signal-strength-ch0")),
        "ch1": to_num(row.get("signal-strength-ch1")),
        "snr": to_num(row.get("signal-to-noise")),
        "noise": to_num(row.get("noise-floor")),
        "distance": to_num(row.get("distance")),
        "uptime": row.get("uptime", ""),
    }


def poll_router(api, cfg, ctx):
    paths = ["/interface/wifi/registration-table/print", "/interface/wireless/registration-table/print"]
    if ctx.get("path"):
        paths = [ctx["path"]]
    rows, last_err = None, None
    for p in paths:
        try:
            rows, _ = api.talk(p)
            ctx["path"] = p
            break
        except RouterOSError as e:
            last_err = e
    if rows is None:
        raise last_err or RouterOSError(TR("няма регистрационна таблица"))
    want = cfg.get("interface", "").strip()
    if want:
        rows = [r for r in rows if r.get("interface") == want]
    return [parse_row(r) for r in rows]


def demo_values(t, shift=0.0, name="demo"):
    base = -68 + 9 * math.sin((t + shift) / 7.0)
    return [{
        "mac": "DE:MO:00:00:00:01", "interface": name, "signal": round(base + random.uniform(-1, 1)),
        "tx_signal": round(base - 1 + random.uniform(-1, 1)), "tx_rate": "866Mbps-80MHz/2S",
        "rx_rate": "780Mbps-80MHz/2S", "ccq": None, "tx_ccq": None, "rx_ccq": None,
        "ch0": round(base + random.uniform(-2, 1)), "ch1": round(base - 1 + random.uniform(-2, 1)),
        "snr": round(base + 92), "noise": -92, "distance": 4, "uptime": TR("демо"),
    }]


class RouterPoller(threading.Thread):
    """Опитва една антена. key='local' (router_*) или 'far' (router2_*)."""

    def __init__(self, key, prefix):
        super().__init__(daemon=True)
        self.key, self.prefix = key, prefix

    def run(self):
        api, ctx, identity, seen = None, {}, "", -1
        p = self.prefix
        while True:
            cfg = load_cfg()
            if seen != CFG_VERSION[0]:
                seen = CFG_VERSION[0]
                if api:
                    api.close()
                api, ctx, identity = None, {}, ""
            try:
                if self.key == "far" and not (cfg["router2_enabled"] and (cfg["router2_host"] or cfg["demo"])):
                    if api:
                        api.close()
                    api = None
                    set_state("far", enabled=False)
                    time.sleep(1)
                    continue
                if cfg["demo"]:
                    shift = 0.0 if self.key == "local" else -2.5
                    peers, identity = demo_values(time.time(), shift, self.key), TR("ДЕМО-") + self.key
                else:
                    if api is None:
                        api = Api(cfg[p + "_host"], int(cfg[p + "_port"]), bool(cfg[p + "_ssl"]))
                        api.login(cfg[p + "_user"], cfg[p + "_password"])
                        try:
                            rows, _ = api.talk("/system/identity/print")
                            identity = rows[0].get("name", "") if rows else ""
                        except RouterOSError:
                            identity = ""
                    peers = poll_router(api, cfg, ctx)
                    if time.time() - ctx.get("if_ts", 0) > 5:
                        ctx["if_ts"] = time.time()
                        try:
                            ctx["ifaces"] = read_ifaces(api)[1]
                        except (RouterOSError, ValueError):
                            pass
                if cfg["demo"]:
                    ctx["ifaces"] = [{"name": "wifi1", "mode": "ap" if self.key == "local" else "station-bridge",
                                      "frequency": "5500", "width": "20mhz", "running": "true", "disabled": "false"}]
                set_state(self.key, enabled=True, ok=True, error="", ts=time.time(), identity=identity,
                          peers=peers, path=ctx.get("path", ""), ifaces=ctx.get("ifaces", []))
            except (OSError, ConnectionError, RouterOSError, ValueError) as e:
                if api:
                    api.close()
                api, ctx = None, {}
                set_state(self.key, enabled=True, ok=False, error=str(e) or e.__class__.__name__, ts=time.time(),
                          peers=[])
                time.sleep(2)
            time.sleep(max(0.2, int(cfg.get("poll_ms", 500)) / 1000.0))


# --------------------------------------------------------------------------- тест на скоростта
BT_STOP = threading.Event()
BT_LOCK = threading.Lock()


def mbps(value):
    n = to_num(value)
    if n is None:
        return None
    s = str(value).lower()
    if "gbps" in s:
        return n * 1000
    if "mbps" in s:
        return n
    if "kbps" in s:
        return n / 1000.0
    return n / 1e6  # чисто число = bit/s


def btest_update(**kw):
    with LOCK:
        ST["btest"].update(kw)


def btest_worker(cfg, params):
    api = None
    keys = [("tx_now", "tx-current"), ("rx_now", "rx-current"), ("tx_avg", "tx-total-average"),
            ("rx_avg", "rx-total-average")]

    def on_row(row):
        upd = {name: mbps(row[k]) for name, k in keys if k in row}
        upd["status"] = row.get("status", "")
        if "lost-packets" in row:
            upd["lost"] = to_num(row["lost-packets"])
        btest_update(**upd)

    try:
        if cfg["demo"]:
            for i in range(params["duration"]):
                if BT_STOP.is_set():
                    break
                v = 480 + random.uniform(-40, 40)
                btest_update(tx_now=v, rx_now=v * 0.9, tx_avg=v, rx_avg=v * 0.9, status="running")
                time.sleep(1)
        else:
            api = Api(cfg["router_host"], int(cfg["router_port"]), bool(cfg["router_ssl"]))
            api.login(cfg["router_user"], cfg["router_password"])
            attrs = ["=address=" + params["address"], "=protocol=" + params["protocol"],
                     "=direction=" + params["direction"], "=duration=%ds" % params["duration"]]
            if params.get("password"):
                attrs += ["=user=" + (cfg["btest_user"] or "admin"), "=password=" + params["password"]]
            elif cfg["btest_user"]:
                attrs += ["=user=" + cfg["btest_user"], "=password=" + cfg["btest_password"]]
            api.stream("/tool/bandwidth-test", attrs, on_row, BT_STOP, params["duration"] + 20)
        btest_update(state="stopped" if BT_STOP.is_set() else "done")
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        btest_update(state="error", error=str(e) or e.__class__.__name__)
    finally:
        if api:
            api.close()


def btest_start(cfg, data):
    addr = str(data.get("address", "")).strip()
    proto = data.get("protocol", "tcp")
    direction = data.get("direction", "both")
    try:
        duration = int(data.get("duration", 10))
    except (TypeError, ValueError):
        duration = 10
    if not re.fullmatch(r"[A-Za-z0-9.:\-]{1,64}", addr):
        return TR("Невалиден адрес.")
    if proto not in ("tcp", "udp") or direction not in ("both", "send", "receive"):
        return TR("Невалидни параметри.")
    with BT_LOCK:
        if ST["btest"].get("state") == "running":
            return TR("Тестът вече върви.")
        BT_STOP.clear()
        with LOCK:
            ST["btest"] = {"state": "running", "address": addr, "protocol": proto, "direction": direction,
                           "duration": max(3, min(60, duration)), "started": time.time()}
        params = {"address": addr, "protocol": proto, "direction": direction, "duration": max(3, min(60, duration)),
                  "password": str(data.get("password", ""))[:64]}
        threading.Thread(target=btest_worker, args=(cfg, params), daemon=True).start()
    return ""


# --------------------------------------------------------------------------- магнитно отклонение (WMM2025)
# Коефициентите на модела WMM2025 (NOAA/BGS) са общодостъпни. Валиден от 2025.0 до 2030.0.
WMM_EPOCH = 2025.0
WMM_COF = """
1 0 -29351.8 0.0 12.0 0.0
1 1 -1410.8 4545.4 9.7 -21.5
2 0 -2556.6 0.0 -11.6 0.0
2 1 2951.1 -3133.6 -5.2 -27.7
2 2 1649.3 -815.1 -8.0 -12.1
3 0 1361.0 0.0 -1.3 0.0
3 1 -2404.1 -56.6 -4.2 4.0
3 2 1243.8 237.5 0.4 -0.3
3 3 453.6 -549.5 -15.6 -4.1
4 0 895.0 0.0 -1.6 0.0
4 1 799.5 278.6 -2.4 -1.1
4 2 55.7 -133.9 -6.0 4.1
4 3 -281.1 212.0 5.6 1.6
4 4 12.1 -375.6 -7.0 -4.4
5 0 -233.2 0.0 0.6 0.0
5 1 368.9 45.4 1.4 -0.5
5 2 187.2 220.2 0.0 2.2
5 3 -138.7 -122.9 0.6 0.4
5 4 -142.0 43.0 2.2 1.7
5 5 20.9 106.1 0.9 1.9
6 0 64.4 0.0 -0.2 0.0
6 1 63.8 -18.4 -0.4 0.3
6 2 76.9 16.8 0.9 -1.6
6 3 -115.7 48.8 1.2 -0.4
6 4 -40.9 -59.8 -0.9 0.9
6 5 14.9 10.9 0.3 0.7
6 6 -60.7 72.7 0.9 0.9
7 0 79.5 0.0 -0.0 0.0
7 1 -77.0 -48.9 -0.1 0.6
7 2 -8.8 -14.4 -0.1 0.5
7 3 59.3 -1.0 0.5 -0.8
7 4 15.8 23.4 -0.1 0.0
7 5 2.5 -7.4 -0.8 -1.0
7 6 -11.1 -25.1 -0.8 0.6
7 7 14.2 -2.3 0.8 -0.2
8 0 23.2 0.0 -0.1 0.0
8 1 10.8 7.1 0.2 -0.2
8 2 -17.5 -12.6 0.0 0.5
8 3 2.0 11.4 0.5 -0.4
8 4 -21.7 -9.7 -0.1 0.4
8 5 16.9 12.7 0.3 -0.5
8 6 15.0 0.7 0.2 -0.6
8 7 -16.8 -5.2 -0.0 0.3
8 8 0.9 3.9 0.2 0.2
9 0 4.6 0.0 -0.0 0.0
9 1 7.8 -24.8 -0.1 -0.3
9 2 3.0 12.2 0.1 0.3
9 3 -0.2 8.3 0.3 -0.3
9 4 -2.5 -3.3 -0.3 0.3
9 5 -13.1 -5.2 0.0 0.2
9 6 2.4 7.2 0.3 -0.1
9 7 8.6 -0.6 -0.1 -0.2
9 8 -8.7 0.8 0.1 0.4
9 9 -12.9 10.0 -0.1 0.1
10 0 -1.3 0.0 0.1 0.0
10 1 -6.4 3.3 0.0 0.0
10 2 0.2 0.0 0.1 -0.0
10 3 2.0 2.4 0.1 -0.2
10 4 -1.0 5.3 -0.0 0.1
10 5 -0.6 -9.1 -0.3 -0.1
10 6 -0.9 0.4 0.0 0.1
10 7 1.5 -4.2 -0.1 0.0
10 8 0.9 -3.8 -0.1 -0.1
10 9 -2.7 0.9 -0.0 0.2
10 10 -3.9 -9.1 -0.0 -0.0
11 0 2.9 0.0 0.0 0.0
11 1 -1.5 0.0 -0.0 -0.0
11 2 -2.5 2.9 0.0 0.1
11 3 2.4 -0.6 0.0 -0.0
11 4 -0.6 0.2 0.0 0.1
11 5 -0.1 0.5 -0.1 -0.0
11 6 -0.6 -0.3 0.0 -0.0
11 7 -0.1 -1.2 -0.0 0.1
11 8 1.1 -1.7 -0.1 -0.0
11 9 -1.0 -2.9 -0.1 0.0
11 10 -0.2 -1.8 -0.1 0.0
11 11 2.6 -2.3 -0.1 0.0
12 0 -2.0 0.0 0.0 0.0
12 1 -0.2 -1.3 0.0 -0.0
12 2 0.3 0.7 -0.0 0.0
12 3 1.2 1.0 -0.0 -0.1
12 4 -1.3 -1.4 -0.0 0.1
12 5 0.6 -0.0 -0.0 -0.0
12 6 0.6 0.6 0.1 -0.0
12 7 0.5 -0.1 -0.0 -0.0
12 8 -0.1 0.8 0.0 0.0
12 9 -0.4 0.1 0.0 -0.0
12 10 -0.2 -1.0 -0.1 -0.0
12 11 -1.3 0.1 -0.0 0.0
12 12 -0.7 0.2 -0.1 -0.1
"""
_WMM = {}


def _wmm_coeffs():
    if not _WMM:
        for line in WMM_COF.strip().splitlines():
            n, m, g, h, gd, hd = line.split()
            _WMM[(int(n), int(m))] = (float(g), float(h), float(gd), float(hd))
    return _WMM


def decimal_year(now=None):
    now = now or datetime.now(timezone.utc)
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    return now.year + (now - start).total_seconds() / (end - start).total_seconds()


def wmm_declination(lat, lon, alt_km=0.0, year=None):
    """Магнитно отклонение в градуси (изток = +)."""
    year = decimal_year() if year is None else year
    coef = _wmm_coeffs()
    lat = max(-89.99, min(89.99, lat))
    a, f, a_ref, nmax = 6378.137, 1 / 298.257223563, 6371.2, 12
    e2 = f * (2 - f)
    phi, lam = math.radians(lat), math.radians(lon)
    sp, cp = math.sin(phi), math.cos(phi)
    rc = a / math.sqrt(1 - e2 * sp * sp)
    p = (rc + alt_km) * cp
    z = (rc * (1 - e2) + alt_km) * sp
    r = math.hypot(p, z)
    phic = math.asin(z / r)
    x, s = math.sin(phic), math.cos(phic)  # cos и sin на допълнението до геоцентричната ширина
    P = [[0.0] * (nmax + 1) for _ in range(nmax + 1)]
    dP = [[0.0] * (nmax + 1) for _ in range(nmax + 1)]
    P[0][0] = 1.0
    for n in range(1, nmax + 1):
        for m in range(0, n + 1):
            if m == n:
                k = math.sqrt((2 * n - 1) / (2.0 * n)) if n > 1 else 1.0
                P[n][n] = k * s * P[n - 1][n - 1]
                dP[n][n] = k * (s * dP[n - 1][n - 1] + x * P[n - 1][n - 1])
            else:
                d = math.sqrt(n * n - m * m)
                c = math.sqrt((n - 1) ** 2 - m * m)
                p2 = P[n - 2][m] if n >= 2 else 0.0
                dp2 = dP[n - 2][m] if n >= 2 else 0.0
                P[n][m] = ((2 * n - 1) * x * P[n - 1][m] - c * p2) / d
                dP[n][m] = ((2 * n - 1) * (x * dP[n - 1][m] - s * P[n - 1][m]) - c * dp2) / d
    dt = year - WMM_EPOCH
    ar = a_ref / r
    br = bt = bp = 0.0
    for n in range(1, nmax + 1):
        arn = ar ** (n + 2)
        for m in range(0, n + 1):
            g, h, gd, hd = coef[(n, m)]
            g, h = g + dt * gd, h + dt * hd
            cm, sm = math.cos(m * lam), math.sin(m * lam)
            t1 = g * cm + h * sm
            br += (n + 1) * arn * t1 * P[n][m]
            bt -= arn * t1 * dP[n][m]
            bp += arn * m * (g * sm - h * cm) * P[n][m]
    bp /= s
    psi = phi - phic
    x_n = -bt * math.cos(psi) - br * math.sin(psi)
    return math.degrees(math.atan2(bp, x_n))


# --------------------------------------------------------------------------- MQTT синхронизация
def mq_str(text):
    b = text.encode("utf-8")
    return len(b).to_bytes(2, "big") + b


def mq_packet(first, payload):
    n, rem = len(payload), b""
    while True:
        d, n = n % 128, n // 128
        rem += bytes([d | (128 if n else 0)])
        if not n:
            break
    return bytes([first]) + rem + payload


def parse_brokers(text):
    out = []
    for item in re.split(r"[\s,;]+", text or ""):
        if not item:
            continue
        m = re.fullmatch(r"(?:(mqtts?)://)?([A-Za-z0-9.\-]+)(?::(\d{1,5}))?", item)
        if not m:
            continue
        scheme = m.group(1) or "mqtts"
        out.append((scheme == "mqtts", m.group(2), int(m.group(3) or (8883 if scheme == "mqtts" else 1883))))
    return out


class MqttSync(threading.Thread):
    """Минимален MQTT 3.1.1 клиент (QoS 0). Публикува моя сигнал и приема сигнала на другата страна."""

    def __init__(self):
        super().__init__(daemon=True)

    def run(self):
        idx = 0
        while True:
            cfg = load_cfg()
            room = clean_room(cfg["room"])
            brokers = parse_brokers(cfg["mqtt_brokers"])
            if not cfg["mqtt_enabled"] or not room or not brokers:
                set_state("mqtt", enabled=False)
                time.sleep(1)
                continue
            tls, host, port = brokers[idx % len(brokers)]
            try:
                self.session(cfg, tls, host, port, room)
            except (OSError, ConnectionError, ValueError, ssl.SSLError) as e:
                set_state("mqtt", enabled=True, ok=False, error=str(e) or e.__class__.__name__, broker=host)
                idx += 1
                time.sleep(3)

    @staticmethod
    def read_packet(sock):
        sock.settimeout(0.5)
        try:
            first = sock.recv(1)
        except socket.timeout:
            return None
        if not first:
            raise ConnectionError(TR("връзката с брокера е прекъсната"))
        sock.settimeout(8)

        def need(n):
            buf = b""
            while len(buf) < n:
                c = sock.recv(n - len(buf))
                if not c:
                    raise ConnectionError(TR("връзката с брокера е прекъсната"))
                buf += c
            return buf

        mult, rem = 1, 0
        while True:
            d = need(1)[0]
            rem += (d & 127) * mult
            if not d & 128:
                break
            mult *= 128
        return first[0], (need(rem) if rem else b"")

    def session(self, cfg, tls, host, port, room):
        side = cfg["side"] if cfg["side"] in ("a", "b") else "a"
        topic_base = "alignfree/v1/%s/" % room
        sig = (tls, host, port, room, side)
        sock = socket.create_connection((host, port), 8)
        if tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        try:
            cid = "af-" + secrets.token_hex(6)
            sock.sendall(mq_packet(0x10, mq_str("MQTT") + b"\x04\x02" + (30).to_bytes(2, "big") + mq_str(cid)))
            pkt = self.read_packet(sock)
            while pkt is None:
                pkt = self.read_packet(sock)
            if pkt[0] != 0x20 or len(pkt[1]) < 2 or pkt[1][1] != 0:
                raise ConnectionError(TR("брокерът отказа връзката"))
            sock.sendall(mq_packet(0x82, (1).to_bytes(2, "big") + mq_str(topic_base + "+") + b"\x00"))
            set_state("mqtt", enabled=True, ok=True, error="", broker=host, side=side, remote=None)
            last_ping = last_pub = last_chk = time.time()
            remote = None
            while True:
                now = time.time()
                if now - last_chk > 2:
                    last_chk = now
                    c2 = load_cfg()
                    s2 = c2["side"] if c2["side"] in ("a", "b") else "a"
                    b2 = parse_brokers(c2["mqtt_brokers"])
                    if not c2["mqtt_enabled"] or clean_room(c2["room"]) != room or s2 != side or \
                            (tls, host, port) not in b2:
                        return
                if now - last_pub >= 0.5:
                    last_pub = now
                    with LOCK:
                        loc = dict(ST["local"])
                    peer = (loc.get("peers") or [None])[0]
                    if loc.get("ok") and peer:
                        ifc = (loc.get("ifaces") or [{}])[0]
                        msg = json.dumps({"v": 1, "sig": peer["signal"], "tx": peer["tx_signal"],
                                          "txr": peer["tx_rate"], "rxr": peer["rx_rate"],
                                          "id": loc.get("identity", ""),
                                          "x": {"ch0": peer.get("ch0"), "ch1": peer.get("ch1"),
                                                "tc": peer.get("tx_ccq"), "rc": peer.get("rx_ccq"),
                                                "snr": peer.get("snr"), "nf": peer.get("noise"),
                                                "up": peer.get("uptime"), "mode": ifc.get("mode", ""),
                                                "fr": ifc.get("frequency", ""), "w": ifc.get("width", "")}})
                        sock.sendall(mq_packet(0x30, mq_str(topic_base + side) + msg.encode("utf-8")))
                if now - last_ping > 15:
                    last_ping = now
                    sock.sendall(b"\xc0\x00")
                pkt = self.read_packet(sock)
                if pkt and (pkt[0] >> 4) == 3:
                    n = int.from_bytes(pkt[1][:2], "big")
                    topic = pkt[1][2:2 + n].decode("utf-8", "replace")
                    body = pkt[1][2 + n:]
                    if (pkt[0] >> 1) & 3:
                        body = body[2:]
                    if topic.startswith(topic_base) and topic[len(topic_base):] != side:
                        try:
                            d = json.loads(body.decode("utf-8"))
                            remote = {"signal": to_num(d.get("sig")), "tx_signal": to_num(d.get("tx")),
                                      "tx_rate": str(d.get("txr", "")), "rx_rate": str(d.get("rxr", "")),
                                      "identity": str(d.get("id", ""))[:40], "ts": time.time(),
                                      "x": d.get("x") if isinstance(d.get("x"), dict) else {},
                                      "side": topic[len(topic_base):]}
                        except ValueError:
                            pass
                        set_state("mqtt", enabled=True, ok=True, error="", broker=host, side=side, remote=remote)
        finally:
            try:
                sock.sendall(b"\xe0\x00")
            except OSError:
                pass
            sock.close()


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "MikrotikAlignFree/" + APP_VERSION

    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(min(length, 65536)).decode("utf-8") or "{}")
        except (ValueError, OSError):
            return None

    def _local_origin_ok(self):
        # защита срещу заявки от чужди сайтове към локалния сървър
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if host.split(":")[0] not in ("127.0.0.1", "localhost"):
            return False
        return origin is None or origin.split("//", 1)[-1] == host

    def do_GET(self):
        if not self._local_origin_ok():
            return self._json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            body = PAGE.replace("__VERSION__", APP_VERSION).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/state":
            with LOCK:
                out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in ST.items()}
            out["now"] = time.time()
            self._json(out)
        elif url.path == "/api/config":
            cfg = load_cfg()
            for k in SECRET_KEYS:
                cfg["has_" + k] = bool(cfg.pop(k, ""))
            self._json(cfg)
        elif url.path == "/api/declination":
            q = parse_qs(url.query)
            try:
                lat, lon = float(q["lat"][0]), float(q["lon"][0])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError
            except (KeyError, ValueError, IndexError):
                return self._json({"error": "bad request"}, 400)
            year = decimal_year()
            self._json({"declination": round(wmm_declination(lat, lon, 0.3, year), 2), "model": "WMM2025",
                        "year": round(year, 2), "valid": WMM_EPOCH <= year < WMM_EPOCH + 5})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._local_origin_ok():
            return self._json({"error": "forbidden"}, 403)
        data = self._body()
        if data is None:
            return self._json({"error": "bad request"}, 400)
        if self.path == "/api/config":
            cfg = load_cfg()
            for key, default in DEFAULT_CFG.items():
                if key not in data:
                    continue
                if key in SECRET_KEYS:
                    if data[key]:
                        cfg[key] = str(data[key])
                elif key == "room":
                    cfg[key] = clean_room(data[key]) or cfg.get(key, "")
                elif key == "side":
                    cfg[key] = "b" if data[key] == "b" else "a"
                else:
                    try:
                        cfg[key] = type(default)(data[key])
                    except (TypeError, ValueError):
                        pass
            save_cfg(cfg)
            CFG_VERSION[0] += 1
            self._json({"ok": True})
        elif self.path == "/api/btest":
            err = btest_start(load_cfg(), data)
            self._json({"ok": not err, "error": err})
        elif self.path == "/api/btest/stop":
            BT_STOP.set()
            self._json({"ok": True})
        elif self.path == "/api/quit":
            self._json({"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()
        else:
            self._json({"error": "not found"}, 404)


def main():
    cfg = load_cfg()
    changed = False
    if not clean_room(cfg["room"]):
        cfg["room"] = secrets.token_urlsafe(9)
        changed = True
    if changed or not os.path.exists(CFG_PATH):
        save_cfg(cfg)
    port = int(cfg.get("http_port", 8080))
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    url = "http://127.0.0.1:%d" % port
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        # вече е стартирана - само отваряме страницата
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        return
    RouterPoller("local", "router").start()
    RouterPoller("far", "router2").start()
    MqttSync().start()
    print(TR("MikrotikAlignFree %s - отворете %s (Ctrl+C за изход)") % (APP_VERSION, url))
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


