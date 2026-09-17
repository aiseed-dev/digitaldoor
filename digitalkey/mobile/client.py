"""事業所サーバーの HTTP API を呼ぶ小さなクライアント。urllib だけで動く(端末でもサーバーでも)。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status, self.message = status, message


class Client:
    def __init__(self, base: str, token: str, *, opener: Callable | None = None, timeout: float = 8.0) -> None:
        self.base, self.token, self.opener, self.timeout = base.rstrip("/"), token, opener, timeout

    def _call(self, method: str, path: str, body: dict | None = None) -> dict | list:
        if self.opener is not None:  # 試験用: TestClient などに差し替える
            return self.opener(method, path, body, self.token)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail", "")
            except Exception:
                detail = ""
            raise ApiError(e.code, detail or e.reason) from None

    def health(self) -> dict:
        return self._call("GET", "/health")

    def doors(self) -> list:
        return self._call("GET", "/doors")

    def keys(self) -> list:
        return self._call("GET", "/keys")

    def unlock(self, door: str, key: str) -> dict:
        return self._call("POST", f"/doors/{door}/unlock", {"key": key})

    def lock(self, door: str) -> dict:
        return self._call("POST", f"/doors/{door}/lock", {})

    def issue(self, subject: str, door: str, start: str, end: str) -> dict:
        return self._call("POST", "/keys", {"subject": subject, "door": door, "start": start, "end": end})

    def revoke(self, key: str, reason: str) -> dict:
        return self._call("DELETE", f"/keys/{key}?reason={reason}")

    def operations(self, limit: int = 50) -> list:
        return self._call("GET", f"/operations?limit={limit}")

    def state(self) -> dict:
        return self._call("GET", "/state")
