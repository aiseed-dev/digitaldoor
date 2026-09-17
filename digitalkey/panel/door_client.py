"""door(door の中核、HTTP)への小さな口。"""
from __future__ import annotations

import json
import urllib.request


class Door:
    def __init__(self, base: str = "http://127.0.0.1:8797", timeout: float = 3.0) -> None:
        self.base, self.timeout = base.rstrip("/"), timeout

    def _call(self, method: str, path: str, body: dict | None = None) -> dict | list:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read() or b"{}")

    def state(self) -> dict:
        return self._call("GET", "/state")

    def lock(self, by: str = "Matter") -> str:
        return self._call("POST", "/lock", {"by": by})["result"]

    def unlock(self, by: str = "Matter") -> str:
        return self._call("POST", "/unlock", {"by": by})["result"]

    def event(self, **kw) -> dict:
        return self._call("POST", "/event", kw)

    def audit(self, since: int = 0) -> list:
        return self._call("GET", f"/audit?since={since}")

    def verify(self) -> dict:
        return self._call("GET", "/verify")
