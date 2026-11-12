"""CircuitMatter の機器を一つ立て、扉の錠として公開する。別スレッドで回す。"""
from __future__ import annotations

import io
import shutil
import threading
import time
from pathlib import Path

import circuitmatter as cm
from circuitmatter import pase

from .door_client import Door
from .door_lock import DoorLockDevice


AVAHI_COMMAND = "avahi-publish-service"
AVAHI_MISSING = ("avahi-utils が入っていません。`sudo apt install avahi-utils` で入れてください"
                 f"(Matter の広告に `{AVAHI_COMMAND}` を使います。avahi-daemon も動いている必要があります)")
AVAHI_SOCKET = Path("/run/avahi-daemon/socket")


class AvahiMissing(RuntimeError):
    """Matter の広告に使う avahi-publish-service が無い。メッセージは利用者に見せてよい文。"""


def avahi_problem() -> str | None:
    """パネルを起動する前の確かめ。avahi-publish-service が無ければ、利用者に見せる文を返す(CircuitMatter は無いと FileNotFoundError で落ちる)。"""
    return None if shutil.which(AVAHI_COMMAND) else AVAHI_MISSING


def avahi_daemon_warning() -> str | None:
    """avahi-daemon が動いていないように見えるときの注意(止めはしない。ソケットの場所は配布で違うことがある)。"""
    if AVAHI_SOCKET.exists():
        return None
    return f"avahi-daemon が動いていないようです({AVAHI_SOCKET} がありません)。`systemctl status avahi-daemon` で確かめてください"


class MatterNode:
    def __init__(self, door: Door, state_dir: Path | str = "vault", vendor_id: int = 0xFFF4,
                 product_id: int = 0x1234, product_name: str = "door 扉") -> None:
        # CircuitMatter の試験用証明書は vendor 0xFFF4 / product 0x1234 だけを認める(製品にするときは自前の DAC)
        if (problem := avahi_problem()) is not None:
            raise AvahiMissing(problem)   # CircuitMatter を作る前に止める(作ると UDP 5541 を開いたまま落ちる)
        self.door = door
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        self.matter = cm.CircuitMatter(state_filename=str(d / "matter-state.json"), vendor_id=vendor_id,
                                       product_id=product_id, product_name=product_name)
        self.device = DoorLockDevice("玄関", self._lock, self._unlock)
        self.matter.add_device(self.device)
        self.vendor_id, self.product_id = vendor_id, product_id
        self._stop = threading.Event()
        self.last_error = ""

    # ---- 命令は door へ ----
    def _lock(self) -> bool:
        try:
            return self.door.lock("Matter") == "施錠"
        except Exception as e:
            self.last_error = str(e)
            return False

    def _unlock(self) -> bool:
        try:
            return self.door.unlock("Matter") != "拒否"
        except Exception as e:
            self.last_error = str(e)
            return False

    # ---- ペアリング ----
    @property
    def qr_payload(self) -> str:
        nv = self.matter.nonvolatile
        return "MT:" + pase.compute_qr_code(self.vendor_id, self.product_id, nv["discriminator"], nv["passcode"])

    @property
    def manual_code(self) -> str:
        nv = self.matter.nonvolatile
        return str(nv["manual_code"]) if "manual_code" in nv else ""

    @property
    def commissioned(self) -> bool:
        return self.matter.root_node.fabric_count > 0

    def qr_png(self) -> bytes:
        import qrcode

        img = qrcode.make(self.qr_payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # ---- 回す ----
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.matter.process_packets()
            except Exception as e:  # 一つの壊れた包で止まらない
                self.last_error = str(e)
            time.sleep(0.01)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True, name="matter")
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
        # mDNS の広告(avahi-publish-service の子プロセス)も止める。残すと親が終わっても広告が出続ける
        for proc in list(getattr(self.matter.mdns_server, "active_services", {}).values()):
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def mirror(self) -> dict:
        s = self.door.state()
        self.device.mirror(bool(s["locked"]), bool(s["door_open"]))
        return s
