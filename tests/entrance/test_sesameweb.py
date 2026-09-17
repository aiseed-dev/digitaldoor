import base64
import json

from cryptography.hazmat.primitives import cmac
from cryptography.hazmat.primitives.ciphers import algorithms

from digitalkey.entrance.keyring import Keyring, SesameWebLock

SECRET = "00112233445566778899aabbccddeeff"


def test_sign_is_cmac_of_middle_three_bytes():
    lock = SesameWebLock("玄関", "uuid-1", "api", SECRET)
    now = 1_800_000_000
    c = cmac.CMAC(algorithms.AES(bytes.fromhex(SECRET)))
    c.update(now.to_bytes(4, "little")[1:4])
    assert lock.sign(now) == c.finalize().hex()
    assert len(lock.sign(now)) == 32


async def test_unlock_posts_cmd_83_with_key_and_history(ledger):
    calls = []

    def opener(method, url, headers, data):
        calls.append((method, url, headers, json.loads(data) if data else None))
        return {"statusCode": 200}

    lock = SesameWebLock("玄関", "uuid-1", "api-key", SECRET, opener=opener)
    k = Keyring(ledger, {"玄関": lock})
    key = k.issue(subject="Anna", lock_id="玄関", start="2020-01-01T00:00", end="2099-01-01T00:00")
    op = await k.open(key.id)
    assert op["結果"] == "成功"
    method, url, headers, body = calls[0]
    assert method == "POST" and url.endswith("/uuid-1/cmd") and headers["x-api-key"] == "api-key"
    assert body["cmd"] == 83 and base64.b64decode(body["history"]) == b"entrance" and len(body["sign"]) == 32


async def test_api_failure_is_recorded(ledger):
    def opener(method, url, headers, data):
        raise RuntimeError("HTTP 401 Unauthorized")

    k = Keyring(ledger, {"玄関": SesameWebLock("玄関", "uuid-1", "bad", SECRET, opener=opener)})
    key = k.issue(subject="Anna", lock_id="玄関", start="2020-01-01T00:00", end="2099-01-01T00:00")
    op = await k.open(key.id)
    assert op["結果"] == "失敗" and "401" in op["理由"]
