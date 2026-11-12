"""アプリ自身のカメラ(flet-camera)で QR を読む。端末のふつうのカメラアプリは通さない。

`CameraQrScanner(reader)` は、プレビューの部品を返し(`build()`)、開始すると(`start()`)カメラの画像の流れ(`on_stream_image`。
Flet が JPEG に符号化して渡す)を `QrReader` に渡して、QR の文字が取れたら `on_text` を呼ぶ。
画像は 1 枚ずつ処理し、処理中に届いた分は捨てる。読んだ画像は保存しない。

カメラの使用許可は、カメラを初期化するときに端末が尋ねる。許可されないと初期化が例外になり、`start()` は False を返す
(アプリは 32 桁を打ち込む道を案内する)。flet_camera は使うときまで import しない(PC では要らない)。
Android と iOS の実機で読めることは、まだ確かめていない(差し替えのインターフェースのため、PC と疑似の試験は fake.py の FakeCameraScanner で回る)。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional, Protocol

from .qrread import QrReader

log = logging.getLogger(__name__)


class QrScanner(Protocol):
    """カメラで QR を読むインターフェース。アプリはこのインターフェースだけを知る。"""

    def build(self):
        """プレビューの部品(Flet のコントロール)。ダイアログに入れる。"""

    async def start(self, on_text: Callable[[str], Awaitable[bool]]) -> bool:
        """読み始める。始められなければ False(許可されない、カメラが無い等)。QR の文字が取れたら on_text を呼ぶ。
        on_text が True を返したら読み終え(以後は呼ばない)、False なら読み続ける。同じ文字は続けて渡さない。"""

    async def stop(self) -> None: ...


class CameraQrScanner:
    def __init__(self, reader: QrReader, resolution: str = "HIGH") -> None:
        self._reader = reader
        self._resolution = resolution
        self._camera = None
        self._on_text: Optional[Callable[[str], Awaitable[bool]]] = None
        self._last: Optional[str] = None
        self._busy = False
        self._done = False
        self._running = False

    def build(self):
        import flet_camera as fc
        self._camera = fc.Camera(expand=True, preview_enabled=True, on_stream_image=self._on_frame)
        return self._camera

    async def start(self, on_text) -> bool:
        import flet_camera as fc
        self._on_text = on_text
        self._done = False
        self._last = None
        try:
            cams = None
            for attempt in range(4):     # ダイアログに出した直後は、部品が画面に載り終えていないことがあるので、数回待ち直す
                try:
                    cams = await self._camera.get_available_cameras()
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(0.4)
            if not cams:
                return False
            back = [c for c in cams if c.lens_direction == fc.CameraLensDirection.BACK]
            await self._camera.initialize(back[0] if back else cams[0], getattr(fc.ResolutionPreset, self._resolution),
                                          enable_audio=False)
            await self._camera.start_image_stream()
        except Exception as e:
            log.warning("camera start failed: %s", type(e).__name__)
            return False
        self._running = True
        return True

    async def _on_frame(self, e) -> None:
        if self._busy or self._done or not self._on_text:
            return
        self._busy = True
        try:
            text = await self._reader.read(e.bytes)
            if text and not self._done and text != self._last:
                self._last = text
                self._done = bool(await self._on_text(text))
        finally:
            self._busy = False

    async def stop(self) -> None:
        self._done = True
        if self._camera is not None and self._running:
            self._running = False
            try:
                await self._camera.stop_image_stream()
            except Exception as e:
                log.debug("stop_image_stream: %s", type(e).__name__)
