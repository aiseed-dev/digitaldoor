"""Sesame のアプリの画面の試験で使う道具(画面の代わりのページ、ダイアログの文字集め、鍵の作り)。

疑似の Sesame・疑似の保存領域だけを使う。本物の鍵(~/.config/digitalkey/sesame-keys.json)にも、実機にも、Bluetooth にも触れない。
"""
import time
import uuid as uuidlib

from digitalkey_sesame import protocol as p
from digitalkey_sesame.keystore import ROLE_ADMIN, DeviceKey

UNREGISTERED = "11111111-2222-3333-4444-555555555501"   # 疑似の Sesame 6 Pro(未登録で始まる)
REGISTERED = "11111111-2222-3333-4444-555555555502"     # 疑似の Sesame 5(登録済み。鍵は 16 バイトの 0)
ZERO_KEY = "00" * 16
TEST_SECRET = "00112233445566778899aabbccddeeff"        # 試験用の決まった値(本物ではない)


class Page:
    """Flet の Page の代わり。出したダイアログを並べ、走らせた仕事を控えるだけ。画面の更新は何もしない。"""

    platform = None
    web = False

    def __init__(self):
        self.shown = []
        self.tasks = []

    def show_dialog(self, dlg):
        if hasattr(dlg, "open"):
            dlg.open = True         # 本物の Page.show_dialog も開いた状態にする
        self.shown.append(dlg)

    def run_task(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))

    def __getattr__(self, name):
        return lambda *a, **k: None


def texts(dlg):
    """ダイアログの中の文字を全部集める。"""
    out = []

    def walk(c):
        v = getattr(c, "value", None)
        if isinstance(v, str):
            out.append(v)
        for attr in ("content", "title"):
            sub = getattr(c, attr, None)
            if sub is not None and not isinstance(sub, str):
                walk(sub)
        for sub in getattr(c, "controls", None) or []:
            walk(sub)
    walk(dlg)
    return "\n".join(out)


def find_all(root, cls):
    """部品の木から、cls の部品を全部集める。"""
    out = []

    def walk(c):
        if isinstance(c, cls):
            out.append(c)
        for attr in ("content",):
            sub = getattr(c, attr, None)
            if sub is not None and not isinstance(sub, str):
                walk(sub)
        for sub in getattr(c, "controls", None) or []:
            walk(sub)
    walk(root)
    return out


def adv_for(uuid_s=UNREGISTERED, registered=True):
    return p.Advertisement(address="AA:BB:CC:DD:EE:01", name="Sesame6Pro", rssi=-40, product_type=21,
                           is_registered=registered, has_history=False, device_uuid=uuidlib.UUID(uuid_s))


def make_key(uuid_s=UNREGISTERED, secret_hex=TEST_SECRET, role=ROLE_ADMIN, name="玄関", registered_at=None):
    return DeviceKey(uuid_s, "sesame_6_pro", secret_hex, name=name, registered_at=registered_at or time.time(), role=role)
