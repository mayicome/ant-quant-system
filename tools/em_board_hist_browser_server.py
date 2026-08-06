# -*- coding: utf-8 -*-
"""本地 HTTP 辅助：浏览器 JSONP 拉东财板块日 K，POST 回本机写入缓存。

用法::
  python tools/em_board_hist_browser_server.py
  # 浏览器打开 http://127.0.0.1:8765/ 按页面按钮开始
  # 或加 --auto 自动用系统默认浏览器打开
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.backfill_em_board_rank_from_hist import (  # noqa: E402
    CACHE_DIR,
    _klines_to_df,
    _save_hist_df,
)

HOST = "127.0.0.1"
PORT = 8765

_STATE: Dict[str, Any] = {
    "lock": threading.Lock(),
    "saved": 0,
    "fail": 0,
    "kind_saved": {"industry": 0, "concept": 0},
}


def _load_codes(kind: str) -> List[str]:
    path = os.path.join(CACHE_DIR, f"_codes_{kind}.json")
    with open(path, "r", encoding="utf-8") as f:
        return list(json.load(f))


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<title>EM Board Hist JSONP Fetcher</title>
<style>
body{font-family:sans-serif;max-width:900px;margin:24px auto;padding:0 16px}
button{font-size:16px;padding:8px 16px;margin-right:8px}
pre{background:#111;color:#0f0;padding:12px;height:320px;overflow:auto}
.row{margin:8px 0}
</style>
</head>
<body>
<h1>东财板块日 K 拉取（JSONP）</h1>
<p>beg=<b id="beg"></b> end=<b id="end"></b> · industry=<b id="ni"></b> · concept=<b id="nc"></b></p>
<div class="row">
  <button id="btnAll">开始全部</button>
  <button id="btnInd">仅行业</button>
  <button id="btnCon">仅概念</button>
  <button id="btnStop">停止</button>
</div>
<pre id="log"></pre>
<script>
const qs0 = new URLSearchParams(location.search);
const BEG = qs0.get("beg") || "20260520";
const END = qs0.get("end") || "20260605";
const PAUSE_MS = Number(qs0.get("pause") || 90);
let STOP = false;
const logEl = document.getElementById("log");
function log(msg){
  logEl.textContent += msg + "\n";
  logEl.scrollTop = logEl.scrollHeight;
}
document.getElementById("beg").textContent = BEG;
document.getElementById("end").textContent = END;

async function loadCodes(kind){
  const r = await fetch("/codes/" + kind);
  return await r.json();
}
function loadOne(code){
  const cb = "emHistCb_" + code + "_" + Date.now();
  const url = "https://91.push2his.eastmoney.com/api/qt/stock/kline/get?secid=90." + code
    + "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    + "&klt=101&fqt=0&beg=" + BEG + "&end=" + END
    + "&smplmt=10000&lmt=1000000&cb=" + cb;
  return new Promise((resolve) => {
    const t = setTimeout(() => {
      cleanup();
      resolve({code, ok:false, err:"timeout", klines:[]});
    }, 20000);
    function cleanup(){
      clearTimeout(t);
      try { delete window[cb]; } catch(e){}
      if (s && s.parentNode) s.parentNode.removeChild(s);
    }
    window[cb] = (data) => {
      const kl = ((data && data.data && data.data.klines) || []);
      cleanup();
      resolve({code, ok:true, n:kl.length, klines:kl});
    };
    const s = document.createElement("script");
    s.src = url;
    s.onerror = () => { cleanup(); resolve({code, ok:false, err:"script error", klines:[]}); };
    document.head.appendChild(s);
  });
}
async function postItem(kind, item){
  const r = await fetch("/ingest", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({kind, item})
  });
  return await r.json();
}
async function runKind(kind){
  const codes = await loadCodes(kind);
  log(`[${kind}] start total=${codes.length}`);
  let ok=0, fail=0;
  for (let i=0; i<codes.length; i++){
    if (STOP) { log(`[${kind}] stopped at ${i}`); break; }
    const code = codes[i];
    const item = await loadOne(code);
    if (item.ok && item.klines && item.klines.length){
      const resp = await postItem(kind, item);
      if (resp.ok) ok++; else fail++;
    } else {
      fail++;
      await postItem(kind, item);
    }
    if ((i+1) % 20 === 0 || i+1 === codes.length){
      log(`[${kind}] ${i+1}/${codes.length} ok=${ok} fail=${fail}`);
    }
    await new Promise(r => setTimeout(r, PAUSE_MS));
  }
  log(`[${kind}] done ok=${ok} fail=${fail}`);
  return {ok, fail};
}
async function main(which){
  STOP = false;
  if (which === "industry" || which === "all") await runKind("industry");
  if (STOP) return;
  if (which === "concept" || which === "all") await runKind("concept");
  log("ALL FINISHED");
}
document.getElementById("btnAll").onclick = () => main("all");
document.getElementById("btnInd").onclick = () => main("industry");
document.getElementById("btnCon").onclick = () => main("concept");
document.getElementById("btnStop").onclick = () => { STOP = true; log("stop requested"); };

(async () => {
  const ni = (await loadCodes("industry")).length;
  const nc = (await loadCodes("concept")).length;
  document.getElementById("ni").textContent = ni;
  document.getElementById("nc").textContent = nc;
  const qs = new URLSearchParams(location.search);
  if (qs.get("auto") === "1") {
    const which = qs.get("kind") || "all";
    log("auto start kind=" + which);
    main(which);
  }
})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[http] " + (fmt % args) + "\n")
        sys.stdout.flush()

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            body = PAGE_HTML.encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return
        if path.startswith("/codes/"):
            kind = path.split("/codes/", 1)[1].strip()
            if kind not in ("industry", "concept"):
                self._send(404, b"not found", "text/plain")
                return
            raw = json.dumps(_load_codes(kind), ensure_ascii=False).encode("utf-8")
            self._send(200, raw, "application/json; charset=utf-8")
            return
        if path == "/status":
            with _STATE["lock"]:
                payload = {
                    "saved": _STATE["saved"],
                    "fail": _STATE["fail"],
                    "kind_saved": dict(_STATE["kind_saved"]),
                }
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/ingest":
            self._send(404, b"not found", "text/plain")
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, b'{"ok":false}', "application/json")
            return
        kind = str(payload.get("kind") or "").strip()
        item = payload.get("item") or {}
        code = str(item.get("code") or "").strip().upper()
        ok_item = bool(item.get("ok"))
        klines = item.get("klines") or []
        saved = False
        if kind in ("industry", "concept") and code.startswith("BK") and ok_item and klines:
            df = _klines_to_df(klines)
            if not df.empty:
                _save_hist_df(df, kind, code)
                jpath = os.path.join(CACHE_DIR, f"{kind}_{code}.json")
                with open(jpath, "w", encoding="utf-8") as f:
                    json.dump({"code": code, "klines": list(klines)}, f, ensure_ascii=False)
                saved = True
                with _STATE["lock"]:
                    _STATE["saved"] += 1
                    _STATE["kind_saved"][kind] = int(_STATE["kind_saved"].get(kind) or 0) + 1
        else:
            with _STATE["lock"]:
                _STATE["fail"] += 1
        body = json.dumps({"ok": saved, "code": code}).encode("utf-8")
        self._send(200, body, "application/json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--auto", action="store_true", help="自动打开浏览器并开始")
    parser.add_argument(
        "--kind",
        default="all",
        choices=("all", "industry", "concept"),
        help="--auto 时拉取的类型",
    )
    args = parser.parse_args()
    os.makedirs(CACHE_DIR, exist_ok=True)
    server = ThreadingHTTPServer((HOST, int(args.port)), Handler)
    url = f"http://{HOST}:{int(args.port)}/"
    if args.auto:
        url = f"{url}?auto=1&kind={args.kind}"
    print(f"[info] serving on {url}")
    print(f"[info] cache_dir={CACHE_DIR}")
    if args.auto:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[info] stopped")


if __name__ == "__main__":
    main()
