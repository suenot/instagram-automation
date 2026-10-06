from io import BytesIO
import base64
import json
from types import SimpleNamespace

from instagram_automation import debug_ui
from instagram_automation.state import State


def test_debug_uses_publication_handler_and_rejects_cross_origin(tmp_path, monkeypatch):
    state = State(tmp_path / "state")
    state.bind("caribbean", "caribbeanawesome", "Caribbean Awesome")
    calls, responses = [], []
    payload = {"account": "caribbean", "request_id": "form-one", "kind": "photo",
               "media": [{"name": "photo.jpg", "bytes": base64.b64encode(b"exact uploaded bytes").decode()}],
               "caption": "Exact caption", "metadata": None}
    async def handler(received_state, args):
        calls.append((received_state, args))
        assert __import__("pathlib").Path(args.media[0]).read_bytes() == b"exact uploaded bytes"
        return {"status": "uncertain", "request_id": args.request_id}, 4
    monkeypatch.setattr(debug_ui, "publish", handler)
    class Server:
        server_port = 8765
        def __init__(self, address, handler_class):
            assert address == ("127.0.0.1", 8765)
            self.handler_class = handler_class
        def serve_forever(self):
            for origin in ("https://attacker.example", "http://127.0.0.1:8765"):
                request = object.__new__(self.handler_class)
                body = json.dumps(payload).encode()
                request.server = self
                request.path = "/publish"
                request.headers = {"Host": "127.0.0.1:8765", "Origin": origin,
                                   "Content-Type": "application/json", "Content-Length": str(len(body))}
                request.rfile = BytesIO(body)
                request.send = lambda status, content: responses.append((status, content))
                request.do_POST()
        def server_close(self):
            pass
    monkeypatch.setattr(debug_ui, "ThreadingHTTPServer", Server)
    output, code = debug_ui.serve(state, 8765)
    assert output["status"] == "stopped" and code == 0
    assert len(calls) == 1 and calls[0][0] is state
    assert calls[0][1].request_id == "form-one" and calls[0][1].caption == "Exact caption"
    assert responses[0][0] == 403 and responses[1] == (200, {"status": "uncertain", "request_id": "form-one"})
