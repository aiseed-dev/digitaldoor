"""錠のドライバで動く接点 — 扉のコントローラの接点の口を、digitalkey.locks の錠のドライバにつなぐ。

扉のコントローラ(digitaldoor_controller)が見る錠は、`fire()`、`mains()`、`door_open()`、`bolt_out()`、`set_lock(locked)` の
束だけ(扉コントローラ仕様書 R-CT-03)。この接点は `set_lock` を、非同期の錠のドライバ(`unlock()` / `lock()`。
`digitalkey.entrance.keyring.LockDriver`)に渡す。Sesame でも `dummy` でも、錠の銘柄はこの下に隠れ、判断の側は変わらない(R-CT-04)。

- 火災・電源・扉の開閉は、SimContacts と同じく属性(`_fire`、`_mains`、`_door_open`)で持つ。HTTP の `/event` が書き換え、
  基板の入力に替えるときはここだけを差し替える。
- 非同期のドライバは、この接点が持つ専用のイベントループ(別のスレッド)で動かす。扉の側の呼びは同期で、
  HTTP の口の複数のスレッドから呼ばれてよい。同じループを使うほかの非同期の処理(鍵の台帳の `Keyring.open` など)は `run()` で渡す。

錠に届かないとき(ドライバが例外を出す、`timeout_s` の内に終わらない):
- `set_lock` は例外を出さず、錠の位置の記録(`bolt_out()`)を動かさない。位置は「届いて確かめた最後の側」で、起動のときは施錠側。
  届かなかった命令で位置を動いたことにしない。
- `failures` を一つ増やし、`last_error` に短い理由を残す。`outputs` には届いた命令だけを足す(`lock` か `unlock`)。
- 時間切れのときはドライバの処理を取り消す。取り消しの前に錠へ届いていた命令は取り消せないので、位置は確かめるまで分からない。
- 扉の判断は止めない。コントローラは命令を出した側として記録を続け、錠が動いたかは `bolt_out()` と `/state` の `bolt_out` が示す。
  命令を出した時点の `failures` と比べれば、呼び手(実演の道具など)は届かなかったことを知れる。
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Coroutine

from .entrance.keyring import LockDriver


async def _cancel_pending() -> None:
    """ループを止める前に、まだ動いている処理を取り消して終わらせる。"""
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@dataclass
class LockDriverContacts:
    """錠のドライバで動く接点。`driver` は `digitalkey.locks.make_lock` が返す物(Sesame など)でも DummyLock でもよい。"""

    driver: LockDriver
    timeout_s: float = 30.0          # 一回の施錠・解錠を待つ秒数(実機の BLE は探して、つないで、送るので長め)
    _fire: bool = False
    _mains: bool = True
    _door_open: bool = False
    _bolt_out: bool = True           # 錠の位置。True が施錠側。届いて確かめた最後の側
    outputs: list[str] = field(default_factory=list)   # 届いた命令の並び("lock" / "unlock")
    failures: int = 0                # 届かなかった命令の数
    last_error: str = ""             # 最後に届かなかったときの短い理由
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _start: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # ---- 接点の口(Contacts) ----
    def fire(self) -> bool:
        return self._fire

    def mains(self) -> bool:
        return self._mains

    def door_open(self) -> bool:
        return self._door_open

    def bolt_out(self) -> bool:
        return self._bolt_out

    def set_lock(self, locked: bool) -> None:
        """錠に施錠(True)か解錠(False)を送る。届かなくても例外は出さない(モジュールの説明)。"""
        action = "lock" if locked else "unlock"
        try:
            self.run(getattr(self.driver, action)())
        except BaseException as e:  # 機器の例外も時間切れも、判断の側へは出さない
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            self.failures += 1
            self.last_error = f"{type(e).__name__}: {e}"[:200] if str(e) else type(e).__name__
            return
        self.outputs.append(action)
        self._bolt_out = locked

    # ---- 非同期の処理の置き場 ----
    def run(self, coro: Coroutine[Any, Any, Any], timeout: float | None = None) -> Any:
        """専用のループで coro を動かし、結果を返す。`timeout`(既定は `timeout_s`)の内に終わらなければ取り消して TimeoutError。"""
        fut = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        try:
            return fut.result(self.timeout_s if timeout is None else timeout)
        except TimeoutError:
            fut.cancel()
            raise TimeoutError(f"{self.timeout_s if timeout is None else timeout} 秒の内に終わりませんでした") from None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=loop.run_forever, name="lock-contacts", daemon=True)
                thread.start()
                self._loop, self._thread = loop, thread
            return self._loop

    def close(self) -> None:
        """専用のループを止める。もう一度 run か set_lock を呼べば、新しいループが立つ。"""
        with self._start:
            loop, thread, self._loop, self._thread = self._loop, self._thread, None, None
        if loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(_cancel_pending(), loop).result(5)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
            thread.join(5)
            if not thread.is_alive():
                loop.close()
