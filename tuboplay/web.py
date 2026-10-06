"""
Servidor web local: controlar TuboPlay desde el móvil.

El PC sigue siendo quien reproduce.  Este módulo abre un servidor HTTP en la
red local y sirve una sola página (HTML + JS, sin dependencias) que en el
teléfono se ve así: barra de reproducción, pestañas, mezclas y cola.

Seguridad:

* escucha en ``0.0.0.0`` para que llegue el teléfono, pero **toda** ruta lleva
  un token aleatorio en la dirección (``/t/<token>/…``); sin él la respuesta es
  404, como si el servidor no existiera;
* el servidor **nunca** toca Tk: deja las órdenes en una cola que la interfaz
  vacía en su propio hilo, y lee un diccionario de estado que la interfaz
  reemplaza entero (referencia atómica).  Así no hay que bloquear nada.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

HOST = "0.0.0.0"
PORTS = (8765, 8766, 8767, 8768)


def lan_ip() -> str:
    """IP local con la que otros equipos de la red te ven (no es 127.0.0.1)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # «conectar» un UDP no manda paquetes: solo fija la ruta local
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class WebServer:
    def __init__(self, token: str, port: int = 0) -> None:
        self.token = token
        self.state: Dict[str, Any] = {}     # la interfaz la sustituye entera
        self.actions: deque = deque()       # órdenes del móvil -> interfaz
        self.last_hit = 0.0                 # última petición recibida
        self.port = port
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- dirección ---------------------------------------------------------
    @property
    def base(self) -> str:
        return "http://%s:%d/t/%s" % (lan_ip(), self.port, self.token)

    # -- ciclo de vida -----------------------------------------------------
    def start(self) -> str:
        """Arranca el servidor; devuelve la dirección o '' si no pudo."""
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):  # no llenamos la consola
                pass

            # -- utilidades --
            def _send(self, code: int, payload: bytes, ctype: str) -> None:
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _json(self, code: int, obj: Any) -> None:
                self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")

            def _route(self):
                """``(resto del camino, ¿token correcto?)``."""
                path = urlsplit(self.path).path
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 2 and parts[0] == "t" and parts[1] == server.token:
                    server.last_hit = time.time()
                    return parts[2:], True
                return parts, False

            # -- GET --
            def do_GET(self):  # noqa: N802
                parts, ok = self._route()
                if not ok:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                if not parts or parts[0] == "index.html":
                    self._send(200, PAGE.encode("utf-8"),
                               "text/html; charset=utf-8")
                    return
                if parts[0] == "api" and len(parts) >= 2 and parts[1] == "state":
                    self._json(200, server.state)
                    return
                self._send(404, b"not found", "text/plain; charset=utf-8")

            # -- POST --
            def do_POST(self):  # noqa: N802
                parts, ok = self._route()
                if not ok:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                if parts[:1] != ["api"] or parts[1:2] != ["action"]:
                    self._json(404, {"ok": False})
                    return
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(min(size, 65536))
                    action = json.loads(body.decode("utf-8") or "{}")
                except Exception:  # noqa: BLE001
                    action = {}
                name = str(action.get("name") or "")
                if not name:
                    self._json(400, {"ok": False})
                    return
                server.actions.append(
                    {"name": name, "arg": action.get("arg"),
                     "text": str(action.get("text") or "")[:500]})
                self._json(200, {"ok": True})

        try:
            self._httpd = ThreadingHTTPServer((HOST, self.port or PORTS[0]), Handler)
        except OSError:
            for port in PORTS[1:]:
                try:
                    self._httpd = ThreadingHTTPServer((HOST, port), Handler)
                    break
                except OSError:
                    self._httpd = None
        if self._httpd is None:
            return ""
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="tuboplay-web", daemon=True)
        self._thread.start()
        return self.base

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._httpd = None


# ---------------------------------------------------------------------------
# La página
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#101219">
<title>TuboPlay</title>
<style>
:root{--bg:#101219;--card:#14171e;--acc:#e11d48;--dim:#8b93a7;--bad:#f87171}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:#fff;font:15px/1.4 "Segoe UI",Roboto,
system-ui,sans-serif;padding-bottom:26px}
header{position:sticky;top:0;background:rgba(16,18,25,.96);backdrop-filter:blur(8px);
z-index:5;padding:14px 16px 10px;border-bottom:1px solid #1c2029}
h1{margin:0;font-size:17px;display:flex;align-items:center;gap:8px}
h1 b{color:var(--acc)}
#now{color:var(--dim);font-size:13px;margin-top:6px;min-height:18px;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{height:5px;background:#22262f;border-radius:4px;overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;width:0;background:var(--acc);border-radius:4px;
transition:width .5s linear}
.times{display:flex;justify-content:space-between;color:var(--dim);font-size:11px;
margin-top:4px}
.ctl{display:flex;justify-content:center;align-items:center;gap:16px;margin-top:10px}
button{font:inherit;border:0;cursor:pointer}
.big{width:64px;height:64px;border-radius:50%;background:var(--acc);color:#fff;
font-size:26px;display:grid;place-items:center}
.big:active{background:#be123c}
.sm{width:46px;height:46px;border-radius:50%;background:var(--card);color:#fff;
font-size:19px;display:grid;place-items:center}
.sm:active{background:#1d2230}
.vol{display:flex;align-items:center;justify-content:center;gap:12px;
color:var(--dim);font-size:13px;margin-top:10px}
.vol button{width:38px;height:38px;border-radius:11px;background:var(--card);
color:#fff;font-size:19px}
.seg{display:flex;gap:6px;padding:12px 16px 4px}
.seg button{flex:1;padding:9px 0;border-radius:10px;background:var(--card);
color:var(--dim);font-size:14px}
.seg button.on{background:var(--acc);color:#fff}
ul{list-style:none;margin:0;padding:6px 10px}
li{background:var(--card);border-radius:11px;margin:6px 0;padding:11px 13px;
display:flex;gap:11px;align-items:center}
li:active{background:#1a1e28}
li .t{flex:1;min-width:0}
li .t b{display:block;font-weight:600;font-size:14.5px;white-space:nowrap;
overflow:hidden;text-overflow:ellipsis}
li .t span{display:block;color:var(--dim);font-size:12.5px;margin-top:2px;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
li.cur{outline:1px solid var(--acc)}
li.cur .t b{color:#fff}
.ico{width:34px;height:34px;border-radius:9px;background:#0e1117;display:grid;
place-items:center;font-size:15px;color:var(--dim);flex:none}
.x{width:32px;height:32px;border-radius:9px;background:transparent;color:var(--dim);
font-size:17px;flex:none}
.x:active{color:var(--bad)}
.empty,.warn{color:var(--dim);text-align:center;padding:28px 22px;font-size:14px}
.add{display:flex;gap:8px;padding:8px 12px 4px}
.add input{flex:1;background:var(--card);border:1px solid #262b38;border-radius:10px;
color:#fff;padding:11px 12px;font:inherit;outline:none;min-width:0}
.add button{background:var(--acc);color:#fff;border-radius:10px;padding:0 16px}
.foot{color:#4c5466;text-align:center;font-size:11.5px;padding:16px}
</style>
</head>
<body>
<header>
  <h1>🎧 <b>Tubo</b>Play <span style="font-weight:400;color:var(--dim)">· móvil</span></h1>
  <div id="now">conectando…</div>
  <div class="bar"><i id="prog"></i></div>
  <div class="times"><span id="t0">0:00</span><span id="t1">0:00</span></div>
  <div class="ctl">
    <button class="sm" onclick="a('prev')">⏮</button>
    <button class="big" id="pp" onclick="a('toggle')">▶</button>
    <button class="sm" onclick="a('next')">⏭</button>
  </div>
  <div class="vol">
    <button onclick="a('vol_down')">−</button>
    <span id="vol">vol</span>
    <button onclick="a('vol_up')">+</button>
  </div>
</header>

<div class="seg" id="seg">
  <button data-v="tabs" class="on">Pestañas</button>
  <button data-v="mix">Mix</button>
  <button data-v="queue">Cola</button>
</div>
<div class="add" id="add" style="display:none">
  <input id="url" placeholder="pegar URL o buscar en YouTube" enterkeyhint="go">
  <button onclick="doAdd()">Añadir</button>
</div>
<ul id="list"><li><div class="t"><span>Cargando…</span></div></li></ul>
<div class="foot">TuboPlay reproduce en el ordenador. Esta página sólo lo dirige.</div>

<script>
var view='tabs', sig='';
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,
  function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function mmss(ms){ms=Math.max(0,ms|0);var s=(ms/1000)|0;var m=(s/60)|0;s=s%60;
  return m+':'+(s<10?'0':'')+s}
function a(name,arg){
  fetch('api/action',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name,arg:arg})})
    .then(function(){return pull(true)})
    .catch(function(){});
}
function doAdd(){
  var el=document.getElementById('url'), v=el.value.trim();
  if(!v) return; el.value='';
  fetch('api/action',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:'add',text:v})})
    .then(function(){setTimeout(function(){view='queue';setSeg();pull(true)},600)});
}
function setSeg(){
  var bs=document.querySelectorAll('#seg button');
  for(var i=0;i<bs.length;i++) bs[i].className=(bs[i].dataset.v===view?'on':'');
  document.getElementById('add').style.display=(view==='queue'?'flex':'none');
}
document.getElementById('seg').addEventListener('click',function(e){
  if(e.target.dataset.v){view=e.target.dataset.v;setSeg();a('set_view',view)}
});
document.getElementById('url').addEventListener('keydown',function(e){
  if(e.key==='Enter') doAdd()
});

function render(s){
  document.getElementById('pp').textContent=(s.playing?'❚❚':'▶');
  document.getElementById('vol').textContent='vol '+(s.volume|0)+'%';
  document.getElementById('prog').style.width=
    (s.dur>0?(100*s.pos/s.dur).toFixed(1):0)+'%';
  document.getElementById('t0').textContent=mmss(s.pos);
  document.getElementById('t1').textContent=mmss(s.dur);
  document.getElementById('now').textContent=(s.title||'—')+
    (s.uploader?' · '+s.uploader:'');

  var items=s[view]||[];
  var ns=String(items.length)+':'+(s.sig||'');
  if(ns===sig) return; sig=ns;
  var ul=document.getElementById('list');
  if(!items.length){
    ul.innerHTML='<li><div class="t"><span class="empty">'+
      esc(s.empty&&s.empty[view]?s.empty[view]:'Nada por aquí todavía.')+
      '</span></div></li>'; return;
  }
  var h='';
  for(var i=0;i<items.length;i++){
    var it=items[i];
    h+='<li'+(it.cur?' class="cur"':'')+' data-i="'+i+'">'+
       '<div class="ico">'+(it.cur&&s.playing?'▶':esc(it.icon||'♪'))+'</div>'+
       '<div class="t"><b>'+esc(it.label)+'</b><span>'+esc(it.sub||'')+'</span></div>'+
       (it.rm?'<button class="x" data-rm="'+i+'">✕</button>':'')+
       '</li>';
  }
  ul.innerHTML=h;
  var lis=ul.querySelectorAll('li');
  for(var j=0;j<lis.length;j++){
    (function(li){
      li.addEventListener('click',function(ev){
        var rm=ev.target.getAttribute&&ev.target.getAttribute('data-rm');
        if(rm!=null){ a('remove',+rm); ev.stopPropagation(); return; }
        var i=+li.dataset.i, it=items[i];
        if(!it) return;
        a(it.rm?'play_index':'play_url', it.rm?i:it.url);
      });
    })(lis[j]);
  }
}

function pull(force){
  fetch('api/state',{cache:'no-store'}).then(function(r){return r.json()})
    .then(function(s){
      if(force) sig='';
      render(s);
    }).catch(function(){
      document.getElementById('now').textContent='sin conexión con el PC…';
    });
}
setSeg(); pull(true);
setInterval(function(){pull(false)},1200);
</script>
</body>
</html>
"""
