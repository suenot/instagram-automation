"""Loopback-only diagnostics form using the CLI publication handler and journal."""

from argparse import Namespace
import asyncio
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

from .errors import PublishError
from .service import publish
from .state import account_name, private_dir, request_id, result

MAX_BODY = 64 * 1024 ** 2
HTML = r"""<!doctype html><meta charset="utf-8"><title>Instagram publisher</title>
<style>body{font:16px system-ui;max-width:760px;margin:32px auto}label{display:block;margin:16px 0}input,select,textarea{display:block}textarea{width:100%;height:120px}pre{white-space:pre-wrap}img{max-width:100%}</style>
<h1>Instagram publisher</h1><p>Uses your saved Camoufox account profile. Posting uses the account's current privacy.</p>
<form id=f><label>Account<select id=account required></select></label>
<label>Request ID<input id=request required></label><button type=button id=fresh>New request ID</button>
<label>Format<select id=kind><option value=reel>Reel</option><option value=photo>Photo</option><option value=carousel>Carousel</option></select></label>
<label>Local media (carousel uses selection order)<input id=media type=file multiple required></label>
<label>Caption<textarea id=caption></textarea></label>
<label>Or complete metadata JSON<input id=metadata type=file accept=application/json></label>
<label>Optional Reel cover<input id=cover type=file accept=image/jpeg,image/png></label>
<label><input id=ai type=checkbox>Add AI label (Reel)</label>
<button id=send>Publish once</button></form><pre id=result></pre><img id=diagnostic hidden>
<script>
const byId=id=>document.getElementById(id);
const newId=()=>byId('request').value=crypto.randomUUID();newId();byId('fresh').onclick=newId;
fetch('/accounts').then(r=>r.json()).then(data=>data.accounts.forEach(a=>{const o=document.createElement('option');o.value=o.textContent=a.account;byId('account').append(o)}));
const encode=file=>new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(file)});
byId('f').onsubmit=async event=>{event.preventDefault();byId('send').disabled=true;byId('diagnostic').hidden=true;
try{const metadata=byId('metadata').files[0];const cover=byId('cover').files[0];const media=await Promise.all(Array.from(byId('media').files).map(async file=>({bytes:await encode(file),name:file.name})));
const response=await fetch('/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:byId('account').value,request_id:byId('request').value,kind:byId('kind').value,media,caption:metadata?null:byId('caption').value,metadata:metadata?await metadata.text():null,cover:cover?{bytes:await encode(cover),name:cover.name}:null,ai_generated:byId('ai').checked})});
const data=await response.json();byId('result').textContent=JSON.stringify(data,null,2);const img=byId('diagnostic');img.src='/diagnostic?'+new URLSearchParams({account:byId('account').value,request_id:byId('request').value});img.onload=()=>img.hidden=false;
}catch(error){byId('result').textContent=String(error)}finally{byId('send').disabled=false}};
</script>"""


def serve(state, port):
    if not 0 <= port <= 65535:
        raise PublishError("INVALID_ARGUMENTS", "Port must be between 0 and 65535.")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, content, kind="application/json"):
            if kind == "application/json":
                content = json.dumps(content, ensure_ascii=False).encode()
            elif isinstance(content, str):
                content = content.encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def host_valid(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.host_valid():
                return self.send(403, {"error": "Loopback host required"})
            if self.path == "/":
                return self.send(200, HTML, "text/html; charset=utf-8")
            if self.path == "/accounts":
                return self.send(200, {"accounts": state.accounts()})
            if self.path.startswith("/diagnostic?"):
                from urllib.parse import parse_qs, urlsplit
                try:
                    query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
                    account = account_name(query["account"][0])
                    request = request_id(query["request_id"][0])
                    if state.get(account, request) is None:
                        raise ValueError()
                    path = state.request_dir(account, request) / "diagnostics" / "last-failure.png"
                    if path.is_symlink() or not path.is_file():
                        raise ValueError()
                    return self.send(200, path.read_bytes(), "image/png")
                except (KeyError, ValueError, OSError, PublishError):
                    pass
            return self.send(404, {"error": "Not found"})

        def do_POST(self):
            expected_origin = f"http://127.0.0.1:{self.server.server_port}"
            if (not self.host_valid() or self.headers.get("Origin") != expected_origin
                    or self.headers.get("Content-Type") != "application/json" or self.path != "/publish"):
                return self.send(403, {"error": "Loopback JSON form required"})
            account, request = None, None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise PublishError("INVALID_ARGUMENTS", "Debug form upload must be at most 64 MiB including JSON encoding.")
                data = json.loads(self.rfile.read(length))
                required = {"account", "request_id", "kind", "media", "caption", "metadata"}
                if not required <= set(data) or not set(data) <= required | {"cover", "ai_generated"}:
                    raise PublishError("INVALID_ARGUMENTS", "Unexpected debug form fields.")
                account, request = account_name(data["account"]), request_id(data["request_id"])
                if data["kind"] not in {"reel", "photo", "carousel"}:
                    raise PublishError("INVALID_ARGUMENTS", "Unknown media format.")
                if not isinstance(data.get("ai_generated", False), bool):
                    raise PublishError("INVALID_ARGUMENTS", "AI label must be a boolean.")
                if data["kind"] != "reel" and (data.get("cover") or data.get("ai_generated")):
                    raise PublishError("INVALID_ARGUMENTS", "Cover and AI label options apply only to Reels.")
                if not isinstance(data["media"], list) or not 0 < len(data["media"]) <= 20:
                    raise PublishError("INVALID_ARGUMENTS", "Select 1 to 20 media files.")
                if (data["metadata"] is None and not isinstance(data["caption"], str)) or (data["metadata"] is not None and data["caption"] is not None):
                    raise PublishError("INVALID_CAPTION", "Choose caption or complete metadata JSON.")
                # Include input bytes in the private source directory identity so a
                # changed form cannot reuse a terminal request's original arguments.
                key = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
                directory = private_dir(state.root / "ui-input" / key)
                media = []
                for index, item in enumerate(data["media"]):
                    content = base64.b64decode(item["bytes"], validate=True)
                    path = directory / f"{index + 1:03d}.media"
                    path.write_bytes(content)
                    path.chmod(0o600)
                    media.append(str(path))
                metadata = None
                if data["metadata"] is not None:
                    metadata = directory / "metadata.json"
                    metadata.write_text(data["metadata"], encoding="utf-8")
                    metadata.chmod(0o600)
                cover = None
                if data.get("cover") is not None:
                    cover = directory / "cover.media"
                    cover.write_bytes(base64.b64decode(data["cover"]["bytes"], validate=True))
                    cover.chmod(0o600)
                args = Namespace(account=account, request_id=request, kind=data["kind"], media=media, video=None,
                                 caption=data["caption"], caption_file=None, metadata_file=str(metadata) if metadata else None,
                                 headless=False, timeout_seconds=1800, cover=str(cover) if cover else None,
                                 ai_generated=data.get("ai_generated", False))
                payload, _ = asyncio.run(publish(state, args))
                self.send(200, payload)
            except PublishError as exc:
                self.send(400, {"platform": "instagram", "account": account, "request_id": request,
                                "status": "failed", "post_url": None, "error": exc.json()})
            except Exception:
                row = state.get(account, request) if account and request else None
                if row and row["status"] in {"submitting", "uncertain", "published"}:
                    return self.send(500, result(state.recover(row)))
                self.send(400, {"platform": "instagram", "account": account, "request_id": request,
                                "status": "failed", "post_url": None,
                                "error": {"code": "INVALID_DEBUG_REQUEST", "message": "Invalid form input or local operation failed."}})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Debug form: http://127.0.0.1:{server.server_port}/ (Ctrl+C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {"platform": "instagram", "status": "stopped", "error": None}, 0
