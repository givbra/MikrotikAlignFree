# --------------------------------------------------------------------------- разширения (v0.3)
import base64
import concurrent.futures
import errno as _errno
import gzip
import subprocess

EXTRA_GET = {}
EXTRA_POST = {}
CHAT_OUT = []
CHAT_LOCK = threading.Lock()


def _guard(fn):
    def wrapper(handler, arg):
        try:
            return fn(handler, arg)
        except Exception as e:  # непредвидена грешка - връщаме разбираем отговор, а не 500
            return {"ok": False, "error": TR("Вътрешна грешка: %s") % (e,)}
    return wrapper


def route_get(path):
    def deco(fn):
        EXTRA_GET[path] = _guard(fn)
        return fn
    return deco


def route_post(path):
    def deco(fn):
        EXTRA_POST[path] = _guard(fn)
        return fn
    return deco


def classify_error(exc):
    """Кратък вид на грешката, за да покаже интерфейсът разбираемо обяснение."""
    if isinstance(exc, RouterOSError):
        text = str(exc).lower()
        return "auth" if ("invalid user" in text or "cannot log in" in text or "password" in text) else "ros"
    if isinstance(exc, socket.timeout):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, ssl.SSLError):
        return "ssl"
    if isinstance(exc, OSError):
        code = getattr(exc, "errno", None)
        winerr = getattr(exc, "winerror", None)
        if winerr == 10061 or code in (_errno.ECONNREFUSED,):
            return "refused"
        if winerr == 10060 or code in (_errno.ETIMEDOUT,):
            return "timeout"
        if winerr in (10051, 10065) or code in (_errno.ENETUNREACH, _errno.EHOSTUNREACH):
            return "unreachable"
        if isinstance(exc, socket.gaierror):
            return "dns"
    if isinstance(exc, ConnectionError):
        return "closed"
    return "other"


# --------------------------------------------------------------------------- синхронизация между двата компютъра
# CHAT_OUT е опашка от (подтема, речник) - изпраща се от MQTT нишката като <стая>/<подтема>/<страна>.
SYNC_IN = {"plan": None, "voice": {}}


def sync_queue(sub, payload):
    with CHAT_LOCK:
        CHAT_OUT.append((sub, payload))


def chat_add(side, text, mine=False, mid=""):
    text = str(text).strip()[:200]
    if not text:
        return
    with LOCK:
        chat = ST.setdefault("chat", {"seq": 0, "log": []})
        if mid and any(m.get("mid") == mid and m.get("side") == side for m in chat["log"]):
            return
        chat["seq"] += 1
        chat["log"] = (chat["log"] + [{"id": chat["seq"], "mid": mid, "side": side, "text": text, "ts": time.time(),
                                       "mine": mine, "delivered": False}])[-40:]


def sync_in(rest, body):
    kind, _, side = rest.partition("/")
    try:
        d = json.loads(bytes(body).decode("utf-8"))
        if not isinstance(d, dict):
            return
    except ValueError:
        return
    if kind == "chat":
        mid = str(d.get("id", ""))[:16]
        chat_add(side, d.get("text", ""), mid=mid)
        if mid:
            sync_queue("ack", {"id": mid})
    elif kind == "ack":
        mid = str(d.get("id", ""))[:16]
        with LOCK:
            for m in ST.get("chat", {}).get("log", []):
                if m.get("mine") and m.get("mid") == mid:
                    m["delivered"] = True
    elif kind == "aim":
        with LOCK:
            ST["aim_remote"] = {"on": bool(d.get("on")), "side": side, "ts": time.time()}
    elif kind == "plan":
        inputs = d.get("inputs")
        if isinstance(inputs, list) and len(inputs) < 60 and len(json.dumps(inputs)) < 20000:
            SYNC_IN["plan"] = inputs
            with LOCK:
                ST["plan_in"] = {"side": side, "ts": time.time()}
    elif kind == "voice":
        data = str(d.get("d", ""))
        if 0 < len(data) < 160000:
            vid = secrets.token_hex(4)
            SYNC_IN["voice"] = {"id": vid, "m": str(d.get("m", "audio/webm"))[:40], "d": data}
            chat_add(side, TR("🎤 гласово съобщение"), mid="v" + vid)
            with LOCK:
                ST["voice_in"] = {"id": vid, "side": side, "ts": time.time()}


def need_sync(cfg):
    with LOCK:
        mq = dict(ST.get("mqtt", {}))
    if not (cfg["mqtt_enabled"] and mq.get("ok")):
        return TR("Включете синхронизацията (Настройки) и изчакайте връзка с брокера.")
    return ""


@route_post("/api/chat")
def api_chat(handler, data):
    text = str(data.get("text", "")).strip()[:200]
    cfg = load_cfg()
    if not text:
        return {"ok": False, "error": TR("Празно съобщение.")}
    err = need_sync(cfg)
    if err:
        return {"ok": False, "error": err}
    mid = secrets.token_hex(4)
    sync_queue("chat", {"v": 1, "id": mid, "text": text})
    chat_add(cfg["side"], text, mine=True, mid=mid)
    return {"ok": True}


@route_post("/api/aim")
def api_aim(handler, data):
    on = bool(data.get("on"))
    cfg = load_cfg()
    if cfg["mqtt_enabled"] and not need_sync(cfg):
        sync_queue("aim", {"on": on})
    return {"ok": True}


@route_post("/api/voice")
def api_voice_send(handler, data):
    cfg = load_cfg()
    err = need_sync(cfg)
    if err:
        return {"ok": False, "error": err}
    d = str(data.get("d", ""))
    if not (0 < len(d) < 160000):
        return {"ok": False, "error": TR("Записът е празен или твърде дълъг.")}
    sync_queue("voice", {"m": str(data.get("m", "audio/webm"))[:40], "d": d})
    return {"ok": True}


@route_get("/api/voice")
def api_voice_get(handler, query):
    v = SYNC_IN.get("voice") or {}
    if query.get("id", [""])[0] != v.get("id"):
        return {"ok": False, "error": TR("няма")}
    return {"ok": True, "m": v["m"], "d": v["d"]}


@route_post("/api/plan/send")
def api_plan_send(handler, data):
    cfg = load_cfg()
    err = need_sync(cfg)
    if err:
        return {"ok": False, "error": err}
    inputs = data.get("inputs")
    if not isinstance(inputs, list) or len(json.dumps(inputs)) > 20000:
        return {"ok": False, "error": TR("Невалиден план.")}
    sync_queue("plan", {"v": 1, "inputs": inputs})
    return {"ok": True}


@route_get("/api/plan_in")
def api_plan_in(handler, query):
    return {"ok": SYNC_IN["plan"] is not None, "inputs": SYNC_IN["plan"]}


# --------------------------------------------------------------------------- откриване на антени (MNDP, UDP 5678)
NEIGH = {}


def mndp_parse(data):
    if len(data) < 8:
        return None
    pos, d = 4, {}
    while pos + 4 <= len(data):
        t = int.from_bytes(data[pos:pos + 2], "big")
        n = int.from_bytes(data[pos + 2:pos + 4], "big")
        v = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        if t == 1 and len(v) == 6:
            d["mac"] = ":".join("%02X" % x for x in v)
        elif t == 5:
            d["identity"] = v.decode("utf-8", "replace")
        elif t == 7:
            d["version"] = v.decode("utf-8", "replace")
        elif t == 8:
            d["platform"] = v.decode("utf-8", "replace")
        elif t == 12:
            d["board"] = v.decode("utf-8", "replace")
        elif t == 16:
            d["iface"] = v.decode("utf-8", "replace")
        elif t == 17 and len(v) == 4:
            d["ip"] = socket.inet_ntoa(v)
    return d if d.get("mac") else None


def publish_neighbors():
    mine = local_ipv4s()
    nets = {ip.rsplit(".", 1)[0] for ip in mine}
    now = time.time()
    out = []
    for mac, d in sorted(NEIGH.items()):
        if now - d["seen"] > 180:
            continue
        ip = d.get("ip", "")
        out.append({"mac": mac, "identity": d.get("identity", ""), "board": d.get("board", ""),
                    "version": d.get("version", ""), "ip": ip, "age": int(now - d["seen"]),
                    "same_net": bool(ip) and ip.rsplit(".", 1)[0] in nets})
    with LOCK:
        ST["neighbors"] = out


def mndp_worker(port=5678):
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", port))
        sock.settimeout(1.0)
    except OSError:
        return
    last_req = 0.0
    while True:
        if time.time() - last_req > 30:
            last_req = time.time()
            try:
                sock.sendto(b"\x00\x00\x00\x00", ("255.255.255.255", port))
            except OSError:
                pass
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            publish_neighbors()
            continue
        except OSError:
            time.sleep(1)
            continue
        d = mndp_parse(data)
        if d:
            d.setdefault("ip", addr[0])
            d["seen"] = time.time()
            NEIGH[d["mac"]] = d
            publish_neighbors()


# --------------------------------------------------------------------------- свързване и четене от антена
def open_api(cfg, prefix="router", override=None):
    p = dict(host=cfg[prefix + "_host"], port=cfg[prefix + "_port"], ssl=cfg[prefix + "_ssl"],
             user=cfg[prefix + "_user"], password=cfg[prefix + "_password"])
    if override:
        p.update({k: v for k, v in override.items() if v not in (None, "")})
    if not p["host"]:
        raise RouterOSError(TR("Няма зададен адрес на антената."))
    api = Api(str(p["host"]), int(p["port"]), bool(p["ssl"]), timeout=5.0)
    try:
        api.login(str(p["user"]), str(p["password"]))
    except Exception:
        api.close()
        raise
    return api


def safe_talk(api, path, *attrs):
    try:
        return api.talk(path, *attrs)[0]
    except RouterOSError:
        return []


def read_ifaces(api):
    wifi = safe_talk(api, "/interface/wifi/print")
    if wifi:
        return "wifi", [{
            "name": r.get("name", ""), "mode": r.get("configuration.mode", ""), "ssid": r.get("configuration.ssid", ""),
            "frequency": r.get("channel.frequency", r.get("configuration.channel.frequency", "")),
            "width": r.get("channel.width", ""), "band": r.get("channel.band", ""),
            "disabled": r.get("disabled", ""), "running": r.get("running", "")} for r in wifi]
    wl = safe_talk(api, "/interface/wireless/print")
    return "wireless", [{
        "name": r.get("name", ""), "mode": r.get("mode", ""), "ssid": r.get("ssid", ""),
        "frequency": r.get("frequency", ""), "width": r.get("channel-width", ""), "band": r.get("band", ""),
        "disabled": r.get("disabled", ""), "running": r.get("running", "")} for r in wl]


def read_info(api):
    info = {"identity": "", "board": "", "version": "", "uptime": "", "cpu": "", "model": "", "firmware": "",
            "pkg": "", "interfaces": [], "addresses": []}
    rows = safe_talk(api, "/system/identity/print")
    info["identity"] = rows[0].get("name", "") if rows else ""
    rows = safe_talk(api, "/system/resource/print")
    if rows:
        r = rows[0]
        info.update(board=r.get("board-name", ""), version=r.get("version", ""), uptime=r.get("uptime", ""),
                    cpu=r.get("cpu-load", ""))
    rows = safe_talk(api, "/system/routerboard/print")
    if rows:
        info["model"] = rows[0].get("model", "")
        info["firmware"] = rows[0].get("current-firmware", "")
    wifi = safe_talk(api, "/interface/wifi/print")
    if wifi:
        info["pkg"] = "wifi"
        for r in wifi:
            info["interfaces"].append({
                "name": r.get("name", ""), "mode": r.get("configuration.mode", ""),
                "ssid": r.get("configuration.ssid", ""), "frequency": r.get("channel.frequency", ""),
                "width": r.get("channel.width", ""), "band": r.get("channel.band", ""),
                "country": r.get("configuration.country", ""), "disabled": r.get("disabled", ""),
                "running": r.get("running", ""), "configuration": r.get("configuration", "")})
    else:
        wl = safe_talk(api, "/interface/wireless/print")
        if wl:
            info["pkg"] = "wireless"
            for r in wl:
                info["interfaces"].append({
                    "name": r.get("name", ""), "mode": r.get("mode", ""), "ssid": r.get("ssid", ""),
                    "frequency": r.get("frequency", ""), "width": r.get("channel-width", ""),
                    "band": r.get("band", ""), "country": r.get("country", ""), "disabled": r.get("disabled", ""),
                    "running": r.get("running", ""), "configuration": r.get("security-profile", "")})
    for r in safe_talk(api, "/ip/address/print"):
        info["addresses"].append({"address": r.get("address", ""), "interface": r.get("interface", ""),
                                  "disabled": r.get("disabled", "")})
    return info


@route_get("/api/router_info")
def api_router_info(handler, query):
    which = (query.get("target", ["local"])[0])
    prefix = "router2" if which == "far" else "router"
    cfg = load_cfg()
    if cfg["demo"]:
        return {"ok": True, "info": {
            "identity": TR("ДЕМО"), "board": TR("SXTsq 5 ax (демо)"), "version": TR("7.16 (демо)"), "uptime": "3d4h", "cpu": "4",
            "model": "RBSXTsq5Pax", "firmware": "7.16", "pkg": "wifi",
            "interfaces": [{"name": "wifi1", "mode": "ap", "ssid": TR("ДЕМО-ЛИНК"), "frequency": "5500", "width": "20mhz",
                            "band": "5ghz-ax", "country": "Bulgaria", "disabled": "false", "running": "true",
                            "configuration": "cfg1"}],
            "addresses": [{"address": "192.168.88.1/24", "interface": "bridge", "disabled": "false"}]}}
    api = None
    try:
        api = open_api(cfg, prefix)
        return {"ok": True, "info": read_info(api)}
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__, "kind": classify_error(e)}
    finally:
        if api:
            api.close()


@route_post("/api/test_router")
def api_test_router(handler, data):
    which = "router2" if data.get("which") == "router2" else "router"
    cfg = load_cfg()
    try:
        override = {"host": str(data.get("host", "")).strip(), "port": int(data.get("port") or 0) or None,
                    "user": str(data.get("user", "")), "password": str(data.get("password", "")),
                    "ssl": bool(data.get("ssl"))}
    except (TypeError, ValueError):
        return {"ok": False, "error": TR("Невалиден порт."), "kind": "other"}
    api = None
    try:
        api = open_api(cfg, which, override)
        rows = safe_talk(api, "/system/identity/print")
        res = safe_talk(api, "/system/resource/print")
        return {"ok": True, "identity": rows[0].get("name", "") if rows else "",
                "board": res[0].get("board-name", "") if res else "",
                "version": res[0].get("version", "") if res else ""}
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__, "kind": classify_error(e)}
    finally:
        if api:
            api.close()


# --------------------------------------------------------------------------- търсене на антени в мрежата
def local_ipv4s():
    ips = set()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("203.0.113.1", 9))  # без изпращане на данни - само определя изходния адрес
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if ip.startswith(("10.", "192.168.", "172.")) and not ip.startswith("127."))


def probe_port(ip, port, timeout=0.5):
    try:
        with socket.create_connection((ip, port), timeout):
            return True
    except OSError:
        return False


def scan_worker(port):
    mine = local_ipv4s()
    nets = sorted({ip.rsplit(".", 1)[0] for ip in mine})[:3]
    extra = ["192.168.88.1", "192.168.1.1", "192.168.0.1", "10.0.0.1", "192.168.88.2", "10.5.50.1"]
    targets = []
    for n in nets:
        targets += ["%s.%d" % (n, i) for i in range(1, 255)]
    for e in extra:
        if e not in targets:
            targets.append(e)
    targets = [t for t in targets if t not in mine]
    found, done = [], [0]
    with LOCK:
        ST["scan"] = {"state": "running", "found": [], "done": 0, "total": len(targets), "nets": nets, "mine": mine}

    def check(ip):
        ok = probe_port(ip, port)
        done[0] += 1
        if ok:
            found.append(ip)
        if done[0] % 10 == 0 or ok:
            with LOCK:
                ST["scan"].update(done=done[0], found=sorted(found))

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        list(ex.map(check, targets))
    with LOCK:
        ST["scan"].update(state="done", done=len(targets), found=sorted(found))


@route_post("/api/scan")
def api_scan(handler, data):
    with LOCK:
        if ST.get("scan", {}).get("state") == "running":
            return {"ok": False, "error": TR("Търсенето вече върви.")}
    try:
        port = int(data.get("port") or 8728)
    except (TypeError, ValueError):
        port = 8728
    threading.Thread(target=scan_worker, args=(port,), daemon=True).start()
    return {"ok": True}


# --------------------------------------------------------------------------- проверка за интернет
def internet_worker():
    hosts = [("api.open-meteo.com", 443), ("tile.openstreetmap.org", 443)]
    while True:
        ok = 0
        for h, p in hosts:
            try:
                socket.create_connection((h, p), 3).close()
                ok += 1
            except OSError:
                pass
        with LOCK:
            ST["internet"] = {"ok": ok, "total": len(hosts), "ts": time.time()}
        time.sleep(25)


# --------------------------------------------------------------------------- генератор на настройки
def cli_val(v):
    v = str(v)
    if v == "" or re.search(r'[\s"=;\\]', v):
        return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
    return v


def cli_line(path, op, attrs, numbers=None):
    parts = [path + "/" + op]
    if numbers:
        parts.append("numbers=" + cli_val(numbers))
    parts += ["%s=%s" % (k, cli_val(v)) for k, v in attrs.items()]
    return " ".join(parts)


def upsert(path, key, attrs):
    return {"op": "upsert", "path": path, "key": key, "attrs": attrs,
            "cli": TR("# създава или обновява: ") + cli_line(path, "add", attrs)}


def setter(path, attrs, numbers=None):
    return {"op": "set", "path": path, "numbers": numbers, "attrs": attrs, "cli": cli_line(path, "set", attrs, numbers)}


class BadParam(ValueError):
    pass


def need(cond, msg):
    if not cond:
        raise BadParam(msg)


WIDTHS = {
    "wifi": {"20": "20mhz", "40": "20/40mhz", "80": "20/40/80mhz"},
    "wireless": {"20": "20mhz", "40": "20/40mhz-Ce", "80": "20/40/80mhz-Ceee"},
}
BANDS = {
    "wifi": {"5ghz-ax", "5ghz-ac", "5ghz-n", "2ghz-ax", "2ghz-n"},
    "wireless": {"5ghz-a/n/ac", "5ghz-onlyn", "5ghz-n/ac", "2ghz-b/g/n", "2ghz-onlyn"},
}


def build_link(p):
    pkg = p.get("pkg")
    need(pkg in ("wifi", "wireless"), TR("Изберете вид на пакета (wifi или wireless)."))
    role = p.get("role")
    need(role in ("ap", "client"), TR("Изберете роля."))
    iface = str(p.get("iface", "")).strip()
    need(re.fullmatch(r"[A-Za-z0-9_.\-]{1,32}", iface), TR("Невалидно име на интерфейс."))
    ssid = str(p.get("ssid", ""))
    need(1 <= len(ssid.encode("utf-8")) <= 32 and not re.search(r"[\x00-\x1f]", ssid), TR("SSID: 1-32 знака."))
    pw = str(p.get("passphrase", ""))
    need(8 <= len(pw) <= 63 and re.fullmatch(r"[\x20-\x7e]+", pw), TR("Паролата: 8-63 знака, само латински букви, цифри и символи."))
    try:
        freq = int(p.get("frequency"))
    except (TypeError, ValueError):
        raise BadParam(TR("Невалидна честота."))
    need(2400 <= freq <= 2500 or 4900 <= freq <= 6100, TR("Честотата е извън обхватите 2.4 и 5 GHz."))
    width = WIDTHS[pkg].get(str(p.get("width", "20")))
    need(width, TR("Невалидна ширина на канала."))
    band = str(p.get("band", ""))
    need(band in BANDS[pkg], TR("Невалиден обхват."))
    country = str(p.get("country", "Bulgaria")).strip()
    need(re.fullmatch(r"[A-Za-z ]{2,30}", country), TR("Държавата се въвежда на латиница (напр. Bulgaria)."))
    ident = str(p.get("identity", "")).strip()
    need(ident == "" or re.fullmatch(r"[A-Za-z0-9_.\-]{1,32}", ident), TR("Име на антената: латиница, цифри, - _ . (до 32 знака)."))
    ip = str(p.get("ip", "")).strip()
    gw = str(p.get("gateway", "")).strip()
    if ip:
        m = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})", ip)
        need(m and all(int(x) <= 255 for x in m.groups()[:4]) and 8 <= int(m.group(5)) <= 30, TR("IP адрес: напр. 192.168.88.2/24."))
    if gw:
        need(re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", gw) and all(int(x) <= 255 for x in gw.split(".")), TR("Невалиден шлюз."))
    eth = str(p.get("eth", "ether1")).strip() or "ether1"
    need(re.fullmatch(r"[A-Za-z0-9_.\-]{1,32}", eth), TR("Невалидно име на кабелния порт."))
    acts = []
    if ident:
        acts.append(setter("/system/identity", {"name": ident}))
    if pkg == "wifi":
        acts.append(upsert("/interface/wifi/security", "name",
                           {"name": "af-sec", "authentication-types": "wpa2-psk", "passphrase": pw}))
        acts.append(upsert("/interface/wifi/channel", "name",
                           {"name": "af-ch", "frequency": str(freq), "width": width, "band": band}))
        acts.append(upsert("/interface/wifi/configuration", "name",
                           {"name": "af-cfg", "mode": "ap" if role == "ap" else "station-bridge", "ssid": ssid,
                            "country": country, "installation": "outdoor", "security": "af-sec",
                            "channel": "af-ch"}))
        acts.append(setter("/interface/wifi", {"configuration": "af-cfg", "disabled": "no"}, iface))
    else:
        acts.append(upsert("/interface/wireless/security-profiles", "name",
                           {"name": "af-sec", "mode": "dynamic-keys", "authentication-types": "wpa2-psk",
                            "wpa2-pre-shared-key": pw}))
        attrs = {"mode": "bridge" if role == "ap" else "station-bridge", "ssid": ssid, "band": band,
                 "frequency": str(freq), "channel-width": width, "country": country.lower(),
                 "security-profile": "af-sec", "wireless-protocol": "nv2" if p.get("nv2") else "802.11",
                 "installation": "outdoor", "distance": "dynamic", "disabled": "no"}
        if role == "client":
            attrs["scan-list"] = str(freq)
        acts.append(setter("/interface/wireless", attrs, iface))
    if ip:
        acts.append({"op": "bridge", "eth": eth, "iface": iface,
                     "cli": TR("# мост: ако %s вече е в мост - ползва се той, иначе: /interface/bridge/add name=bridge1\n"
                            "/interface/bridge/port/add bridge=<мост> interface=%s   # и %s") % (eth, eth, iface)})
        acts.append({"op": "ip", "address": ip, "clean": bool(p.get("clean")), "drop": True,
                     "cli": (TR("# изчиства DHCP сървър/клиент и излишни адреси\n") if p.get("clean") else "") +
                            TR("/ip/address/set address=%s interface=<мост>   # връзката може да прекъсне") % ip})
    if gw:
        acts.append(upsert("/ip/route", "dst-address", {"dst-address": "0.0.0.0/0", "gateway": gw}))
    return acts


def build_prepare(p):
    user = str(p.get("user", "alignfree")).strip()
    need(re.fullmatch(r"[A-Za-z0-9_.\-]{3,32}", user), TR("Потребител: 3-32 знака (латински букви, цифри, _ . -)."))
    pw = str(p.get("password", ""))
    need(len(pw) >= 8 and re.fullmatch(r"[\x20-\x7e]+", pw), TR("Паролата: поне 8 знака, само латински букви, цифри и символи."))
    allow = str(p.get("allow", "")).strip()
    need(allow == "" or re.fullmatch(r"[0-9./,]{7,64}", allow), TR("Разрешен адрес: напр. 192.168.88.10 или 192.168.88.0/24."))
    acts = [upsert("/user/group", "name", {"name": "alignfree", "policy": "read,api,test"}),
            upsert("/user", "name", {"name": user, "group": "alignfree", "password": pw})]
    svc = {"disabled": "no"}
    if allow:
        svc["address"] = allow
    acts.append(setter("/ip/service", svc, "api"))
    if p.get("bwserver", True):
        acts.append(setter("/tool/bandwidth-server", {"enabled": "yes", "authenticate": "yes"}))
    return acts


BUILDERS = {"link": build_link, "prepare": build_prepare}


def run_actions(api, actions):
    results = []
    ctx = {"bridge": "bridge1"}
    for a in actions:
        try:
            if a["op"] == "bridge":
                eth = a["eth"]
                rows, _ = api.talk("/interface/bridge/port/print", "?interface=" + eth)
                if rows:
                    ctx["bridge"] = rows[0].get("bridge", "bridge1")
                else:
                    br, _ = api.talk("/interface/bridge/print", "?name=bridge1")
                    if not br:
                        api.talk("/interface/bridge/add", "=name=bridge1")
                    api.talk("/interface/bridge/port/add", "=bridge=bridge1", "=interface=" + eth)
                rows, _ = api.talk("/interface/bridge/port/print", "?interface=" + a["iface"])
                if rows:
                    if rows[0].get("bridge") != ctx["bridge"]:
                        api.talk("/interface/bridge/port/set", "=.id=" + rows[0][".id"], "=bridge=" + ctx["bridge"])
                else:
                    api.talk("/interface/bridge/port/add", "=bridge=" + ctx["bridge"], "=interface=" + a["iface"])
            elif a["op"] == "ip":
                br = ctx["bridge"]
                if a["clean"]:
                    for path in ("/ip/dhcp-server", "/ip/dhcp-client"):
                        try:
                            old, _ = api.talk(path + "/print")
                            for r in old:
                                api.talk(path + "/remove", "=.id=" + r[".id"])
                        except RouterOSError:
                            pass
                rows, _ = api.talk("/ip/address/print")
                if any(r.get("address") == a["address"] and r.get("interface") == br for r in rows):
                    pass
                else:
                    target = next((r for r in rows if r.get("interface") == br), rows[0] if (rows and a["clean"]) else None)
                    if a["clean"]:
                        for r in rows:
                            if target is None or r[".id"] != target[".id"]:
                                api.talk("/ip/address/remove", "=.id=" + r[".id"])
                    if target is not None:
                        api.talk("/ip/address/set", "=.id=" + target[".id"], "=address=" + a["address"], "=interface=" + br)
                    else:
                        api.talk("/ip/address/add", "=address=" + a["address"], "=interface=" + br)
            elif a["op"] == "upsert":
                rows, _ = api.talk(a["path"] + "/print", "?%s=%s" % (a["key"], a["attrs"][a["key"]]))
                if rows:
                    attrs = ["=%s=%s" % (k, v) for k, v in a["attrs"].items() if k != a["key"]]
                    api.talk(a["path"] + "/set", "=.id=" + rows[0][".id"], *attrs)
                else:
                    api.talk(a["path"] + "/add", *["=%s=%s" % (k, v) for k, v in a["attrs"].items()])
            else:
                attrs = ["=%s=%s" % (k, v) for k, v in a["attrs"].items()]
                if a.get("numbers"):
                    attrs.insert(0, "=numbers=" + a["numbers"])
                api.talk(a["path"] + "/set", *attrs)
            results.append({"cli": a["cli"], "ok": True})
        except (RouterOSError, OSError, ConnectionError, KeyError) as e:
            if a.get("drop") and isinstance(e, (OSError, ConnectionError)):
                results.append({"cli": a["cli"], "ok": True, "note": TR("връзката прекъсна - това е нормално след смяна на адреса")})
                break
            results.append({"cli": a["cli"], "ok": False, "error": str(e) or e.__class__.__name__})
            break
    return results


def mask_cli(text, secrets_):
    for s in secrets_:
        if s:
            text = text.replace(cli_val(s), "********")
    return text


@route_post("/api/setup/preview")
def api_setup_preview(handler, data):
    builder = BUILDERS.get(data.get("kind"))
    if not builder:
        return {"ok": False, "error": TR("Непознат вид настройка.")}
    try:
        acts = builder(data.get("params") or {})
    except BadParam as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "commands": [a["cli"] for a in acts]}


@route_post("/api/setup/apply")
def api_setup_apply(handler, data):
    if data.get("confirm") is not True:
        return {"ok": False, "error": TR("Липсва потвърждение.")}
    builder = BUILDERS.get(data.get("kind"))
    if not builder:
        return {"ok": False, "error": TR("Непознат вид настройка.")}
    cfg = load_cfg()
    if cfg["demo"]:
        return {"ok": False, "error": TR("В демо режим не се променя нищо.")}
    try:
        acts = builder(data.get("params") or {})
    except BadParam as e:
        return {"ok": False, "error": str(e)}
    prefix = "router2" if data.get("target") == "far" else "router"
    api = None
    try:
        api = open_api(cfg, prefix, data.get("login") or None)
        pre = []
        if data.get("kind") == "link" and data.get("backup", True):
            name = "mikrotikalignfree-" + time.strftime("%Y%m%d-%H%M%S")
            try:
                api.talk("/system/backup/save", "=name=" + name)
                pre = [{"cli": TR("# резервно копие: %s.backup") % name, "ok": True}]
            except RouterOSError as e:
                return {"ok": False, "results": [{"cli": TR("# резервно копие"), "ok": False,
                                                   "error": TR("Копието не е направено, нищо не е променено: ") + str(e)}]}
        results = pre + run_actions(api, acts)
        return {"ok": all(r["ok"] for r in results), "results": results}
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__, "kind": classify_error(e)}
    finally:
        if api:
            api.close()


@route_post("/api/setup/backup")
def api_setup_backup(handler, data):
    cfg = load_cfg()
    if cfg["demo"]:
        return {"ok": False, "error": TR("В демо режим не се прави копие.")}
    prefix = "router2" if data.get("target") == "far" else "router"
    name = "mikrotikalignfree-" + time.strftime("%Y%m%d-%H%M%S")
    api = None
    try:
        api = open_api(cfg, prefix)
        api.talk("/system/backup/save", "=name=" + name)
        exported = True
        try:
            api.talk("/export", "=file=" + name)
        except RouterOSError:
            exported = False
        return {"ok": True, "name": name, "export": exported}
    except (OSError, ConnectionError, RouterOSError, ValueError) as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__, "kind": classify_error(e)}
    finally:
        if api:
            api.close()


# --------------------------------------------------------------------------- мрежови настройки на компютъра (Windows)
def oem_decode(raw):
    try:
        import ctypes
        enc = "cp%d" % ctypes.windll.kernel32.GetOEMCP()
    except (ImportError, AttributeError, OSError):
        enc = "utf-8"
    for e in (enc, "utf-8", "cp1251"):
        try:
            return raw.decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


@route_get("/api/netsh/adapters")
def api_netsh_adapters(handler, query):
    if os.name != "nt":
        return {"ok": False, "error": TR("Тази функция е само за Windows.")}
    try:
        out = subprocess.run(["netsh", "interface", "show", "interface"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    lines = oem_decode(out.stdout).splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().startswith("---")), -1)
    names = []
    for l in lines[start + 1:]:
        parts = l.split()
        if len(parts) >= 4:
            names.append(" ".join(parts[3:]))
    return {"ok": True, "adapters": names}


def run_netsh_elevated(args):
    quoted = args.replace("'", "''")
    cmd = ("$p = Start-Process -FilePath 'netsh.exe' -ArgumentList '%s' -Verb RunAs -Wait -PassThru "
           "-WindowStyle Hidden; exit $p.ExitCode" % quoted)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                       capture_output=True, timeout=120)
    return r.returncode


ADAPTER_RE = re.compile(r"[\w .()\-]{1,64}")
IP_RE = re.compile(r"(\d{1,3}\.){3}\d{1,3}")


@route_post("/api/netsh/set")
def api_netsh_set(handler, data):
    if os.name != "nt":
        return {"ok": False, "error": TR("Тази функция е само за Windows.")}
    name = str(data.get("name", ""))
    if not ADAPTER_RE.fullmatch(name):
        return {"ok": False, "error": TR("Невалидно име на адаптер.")}
    if data.get("dhcp"):
        args = 'interface ipv4 set address name="%s" source=dhcp' % name
    else:
        ip, mask, gw = str(data.get("ip", "")), str(data.get("mask", "255.255.255.0")), str(data.get("gateway", ""))
        if not (IP_RE.fullmatch(ip) and IP_RE.fullmatch(mask) and (not gw or IP_RE.fullmatch(gw))):
            return {"ok": False, "error": TR("Невалиден IP адрес, маска или шлюз.")}
        args = 'interface ipv4 set address name="%s" source=static address=%s mask=%s' % (name, ip, mask)
        if gw:
            args += " gateway=" + gw
    try:
        code = run_netsh_elevated(args)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": code == 0, "error": "" if code == 0 else TR("netsh върна код %s (отказан UAC или грешка).") % code,
            "command": "netsh " + args}


# --------------------------------------------------------------------------- вградена карта (Leaflet, BSD-2)
LEAFLET_JS_B64 = "@@LEAFLET_JS@@"
LEAFLET_CSS = r'''@@LEAFLET_CSS@@'''
_STATIC = {}


def static_asset(name):
    if name not in _STATIC:
        if name == "leaflet.js":
            _STATIC[name] = (gzip.decompress(base64.b64decode(LEAFLET_JS_B64)), "application/javascript; charset=utf-8")
        elif name == "leaflet.css":
            _STATIC[name] = (LEAFLET_CSS.encode("utf-8"), "text/css; charset=utf-8")
        else:
            return None
    return _STATIC[name]


def start_background():
    threading.Thread(target=internet_worker, daemon=True).start()
    threading.Thread(target=mndp_worker, daemon=True).start()
