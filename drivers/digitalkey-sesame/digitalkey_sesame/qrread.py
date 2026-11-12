"""QR を読む部分 — 画像(JPEG や PNG のバイト列)から QR の文字を取り出す。差し替えのインターフェースです。

読み取りの方法の選び(Android と iOS の `flet build` で使える物を調べて選んだ):

  * pyzbar + flet-libzbar + Pillow を使う。Flet の mobile 用のパッケージ置き場(pypi.flet.dev)に、Android(arm64 など)と
    iOS(iphoneos、simulator)向けの pyzbar と、libzbar の本体を入れる flet-libzbar があり、どちらも Python 3.12 の車輪が
    ある。pyzbar 自身は pure Python で、libzbar を ctypes で呼ぶ(mobile 向けの pyzbar は flet-libzbar の libzbar を探す)。
    Pillow にも mobile 用の車輪がある。大きさは flet-libzbar が約 0.8 MB で、OpenCV(数十 MB)より軽い。
  * OpenCV(opencv-python。cv2.QRCodeDetector)も mobile の車輪はあるが、大きいので選ばない。
  * pure Python だけの解読器は、画面を撮った写真(ぼけ、斜め、反射)に強い物を見つけられなかったので選ばない。
  * Flet 本体に QR を読む部品は無い。flet-camera の撮影(`take_picture`)か画像の流し(`on_stream_image`。JPEG に符号化されて届く)から読む。
  * 読み取りの Flutter 拡張(mobile_scanner 等)を自前で書く道もあるが、Flutter の組み立てを手元で確かめられないので、今は選ばない。

実機(Android、iOS)で読めることは、まだ確かめていない。そのため読み取りは `QrReader` のインターフェースにして、アプリはインターフェースだけを知る。
PC と疑似(fake.py の FakeQrReader)で回る試験を置き、実機では「渡す側の QR を読めるか」を確かめる。
libzbar が無いコンピューター(読み込みに失敗する)では `available()` が偽になり、アプリは 32 桁を打ち込む道だけを出す。
"""
from __future__ import annotations

import asyncio
import io
from typing import Optional, Protocol


class QrReader(Protocol):
    """画像のバイト列から QR の文字を返す。読めなければ None。例外は出さない。"""

    def available(self) -> bool: ...
    async def read(self, image: bytes) -> Optional[str]: ...


class ZbarQrReader:
    """pyzbar(libzbar)で読む。重い処理なので別のスレッドで回す。import は使うときまで遅らせる(PC で libzbar が無くても落ちない)。"""

    MAX_SIDE = 1600     # これより大きい写真は縮めてから読む(速さのため)

    def __init__(self) -> None:
        self._decode = None
        self._ok: Optional[bool] = None

    def available(self) -> bool:
        if self._ok is None:
            try:
                from PIL import Image  # noqa: F401
                from pyzbar.pyzbar import decode
                self._decode = decode
                self._ok = True
            except Exception:
                self._ok = False
        return self._ok

    def read_sync(self, image: bytes) -> Optional[str]:
        if not self.available():
            return None
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(image))
            im = im.convert("L")
            if max(im.size) > self.MAX_SIDE:
                im.thumbnail((self.MAX_SIDE, self.MAX_SIDE))
            for found in self._decode((im.tobytes(), im.width, im.height)):
                if found.type == "QRCODE":
                    return found.data.decode("utf-8", errors="strict")
        except Exception:
            return None
        return None

    async def read(self, image: bytes) -> Optional[str]:
        return await asyncio.to_thread(self.read_sync, image)
