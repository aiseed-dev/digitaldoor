"""CircuitMatter の機器を一つ立て、扉の錠として公開する。別スレッドで回す。"""
from __future__ import annotations

import io
import threading
import time
from pathlib import Path

import circuitmatter as cm
from circuitmatter import pase

from .door_client import Door
from .door_lock import DoorLockDevice


class MatterNode:
    def __init__(self, door: Door, state_dir: Path | str = "vault", vendor_id: int = 0xFFF4,
                 product_id: int = 0x1234, product_name: str = "door 扉") -> None:
        # CircuitMatter の試験用証明書は vendor 0xFFF4 / product 0x1234 だけを認める(製品にするときは自前の DAC)
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
            except Exception:
                pass

    def mirror(self) -> dict:
        s = self.door.state()
        self.device.mirror(bool(s["locked"]), bool(s["door_open"]))
        return s
