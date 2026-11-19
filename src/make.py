#!/usr/bin/env python3
"""Сглобява mikrotikalignfree.py от base.py + ext.py + page.html + Leaflet + WMM."""
import base64, gzip, re, sys
R = lambda p: open(p, encoding="utf-8").read()
base, ext, page = R("base.py"), R("ext.py"), R("page.html")

def sub(text, old, new, count=1):
    assert text.count(old) == count, (old[:60], text.count(old))
    return text.replace(old, new)

base = sub(base, 'APP_VERSION = "0.2.0"', 'APP_VERSION = "0.5.0"')
base = sub(base, "peers=[])\n                time.sleep(2)", "peers=[], kind=classify_error(e))\n                time.sleep(2)")
base = sub(base, '            sock.sendall(mq_packet(0x82, (1).to_bytes(2, "big") + mq_str(topic_base + "+") + b"\\x00"))\n',
    '            sock.sendall(mq_packet(0x82, (1).to_bytes(2, "big") + mq_str(topic_base + "+") + b"\\x00"))\n'
    '            sock.sendall(mq_packet(0x82, (2).to_bytes(2, "big") + mq_str(topic_base + "+/+") + b"\\x00"))\n')
base = sub(base, "                if now - last_ping > 15:\n",
    "                with CHAT_LOCK:\n                    outgoing, CHAT_OUT[:] = list(CHAT_OUT), []\n"
    "                for sub, payload in outgoing:\n"
    "                    sock.sendall(mq_packet(0x30, mq_str(topic_base + sub + \"/\" + side) +\n"
    "                                           json.dumps(payload).encode(\"utf-8\")))\n"
    "                if now - last_ping > 15:\n")
base = sub(base, "                    if topic.startswith(topic_base) and topic[len(topic_base):] != side:\n",
    "                    rest = topic[len(topic_base):] if topic.startswith(topic_base) else \"\"\n"
    "                    if \"/\" in rest:\n"
    "                        if rest.rpartition(\"/\")[2] != side:\n"
    "                            sync_in(rest, body)\n"
    "                    elif topic.startswith(topic_base) and rest != side:\n")
base = sub(base, '        if url.path in ("/", "/index.html"):\n',
    '        if url.path.startswith("/static/"):\n'
    '            asset = static_asset(url.path[len("/static/"):])\n'
    '            if not asset:\n'
    '                return self._json({"error": "not found"}, 404)\n'
    '            body, ctype = asset\n'
    '            self.send_response(200)\n'
    '            self.send_header("Content-Type", ctype)\n'
    '            self.send_header("Cache-Control", "max-age=86400")\n'
    '            self.send_header("Content-Length", str(len(body)))\n'
    '            self.end_headers()\n'
    '            self.wfile.write(body)\n'
    '        elif url.path in EXTRA_GET:\n'
    '            self._json(EXTRA_GET[url.path](self, parse_qs(url.query)))\n'
    '        elif url.path in ("/", "/index.html"):\n')
base = sub(base, '        elif self.path == "/api/quit":\n',
    '        elif self.path in EXTRA_POST:\n            self._json(EXTRA_POST[self.path](self, data))\n'
    '        elif self.path == "/api/quit":\n')
base = sub(base, '    MqttSync().start()\n', '    MqttSync().start()\n    start_background()\n')
base = sub(base, "WMM_COF = \"\"\"\n@@WMM@@\n\"\"\"", "WMM_COF = \"\"\"\n%s\n\"\"\"" % open("/tmp/wmm_block.txt").read().strip()) if "@@WMM@@" in base else base


import json as _json
I18N_TXT = _json.dumps(_json.load(open("i18n/en.json", encoding="utf-8")), ensure_ascii=False, separators=(",", ":"))
assert "'''" not in I18N_TXT
I18N_BLOCK = (
    "# --------------------------------------------------------------------------- език (en по подразбиране / bg)\n"
    "LANG = [load_cfg().get(\"lang\", \"en\")]\n"
    "I18N = json.loads(r'''" + I18N_TXT + "''')\n"
    "PAGE_RE = re.compile(\"[^<>\\\"'`{}\\\\$\\n]+\")\n\n\n"
    "def TR(s):\n"
    "    \"\"\"Превод на съобщение от сървъра (само когато езикът е английски).\"\"\"\n"
    "    return I18N[\"srv\"].get(s, s) if LANG[0] == \"en\" else s\n\n\n"
    "def render_page():\n"
    "    lang = LANG[0]\n"
    "    s = PAGE\n"
    "    if lang == \"en\":\n"
    "        table = I18N[\"page\"]\n\n"
    "        def one(mt):\n"
    "            r = mt.group(0)\n"
    "            k = r.strip()\n"
    "            v = table.get(k)\n"
    "            if v is None:\n"
    "                return r\n"
    "            i = r.index(k)\n"
    "            return r[:i] + v.replace(\"\\u201c\", \"\\ue000\").replace(\"\\u201d\", \"\\ue001\") + r[i + len(k):]\n\n"
    "        s = PAGE_RE.sub(one, s)\n"
    "        s = s.replace(\"\\u201c\", \"\\u201d\").replace(\"\\u201e\", \"\\u201c\")\n"
    "        s = s.replace(\"\\ue000\", \"\\u201c\").replace(\"\\ue001\", \"\\u201d\")\n"
    "    return s.replace(\"__VERSION__\", APP_VERSION).replace(\"__LANG__\", lang)\n\n\n"
)
I18N_BLOCK = re.sub(r"PAGE_RE = re\.compile\(.*\)\n", lambda m: r'''PAGE_RE = re.compile(r"[^<>\"'`{}\\$\n]+")''' + "\n", I18N_BLOCK)
MARK = "# --------------------------------------------------------------------------- RouterOS API\n"
base = sub(base, MARK, I18N_BLOCK + MARK)
base = sub(base, 'PAGE.replace("__VERSION__", APP_VERSION).encode("utf-8")', "render_page().encode(\"utf-8\")")
base = sub(base, '                elif key == "side":\n', '                elif key == "lang":\n                    cfg[key] = "bg" if data[key] == "bg" else "en"\n                elif key == "side":\n')
base = sub(base, "            save_cfg(cfg)\n            CFG_VERSION[0] += 1\n", "            save_cfg(cfg)\n            LANG[0] = cfg.get(\"lang\", \"en\")\n            CFG_VERSION[0] += 1\n")

js = base64.b64encode(gzip.compress(open("leaflet.js", "rb").read(), 9)).decode()
ext = sub(ext, '"@@LEAFLET_JS@@"', '"%s"' % js)
ext = sub(ext, "@@LEAFLET_CSS@@", open("leaflet.css", encoding="utf-8").read())
i = base.index("def main():")
out = base[:i] + ext + "\n\n" + base[i:]
out = out.rstrip() + "\n\n\n# --------------------------------------------------------------------------- уеб интерфейс\n"
out += "# Leaflet 1.9.4 (c) Volodymyr Agafonkin, BSD-2-Clause - вграден за карта без интернет зависимост за самата библиотека.\n"
assert "'''" not in page
out += "PAGE = r'''" + page + "'''\n\nif __name__ == \"__main__\":\n    main()\n"
open("mikrotikalignfree.py", "w", encoding="utf-8").write(out)
print("alignfree.py:", len(out.splitlines()), "реда,", len(out) // 1024, "KB")
