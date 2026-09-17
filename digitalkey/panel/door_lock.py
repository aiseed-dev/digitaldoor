"""Matter の Door Lock クラスタ(0x0101)と Door Lock 機器種別(0x000A)。CircuitMatter に無いので、ここで定義する。

見せるのは施解錠と扉の開閉だけ。資格情報(暗証番号・カード)は door と発行者の層が持つので、PIN 系の機能は出さない。
"""
from __future__ import annotations

import enum
from typing import Callable

from circuitmatter import data_model, tlv
from circuitmatter.clusters.general.identify import Identify
from circuitmatter.device_types import simple_device


class Feature(enum.IntFlag):
    PIN_CREDENTIAL = 1 << 0
    RFID_CREDENTIAL = 1 << 1
    FINGER_CREDENTIALS = 1 << 2
    LOGGING = 1 << 3
    WEEK_DAY_ACCESS_SCHEDULES = 1 << 4
    DOOR_POSITION_SENSOR = 1 << 5
    FACE_CREDENTIALS = 1 << 6
    CREDENTIAL_OVER_THE_AIR_ACCESS = 1 << 7
    USER = 1 << 8
    YEAR_DAY_ACCESS_SCHEDULES = 1 << 10
    HOLIDAY_SCHEDULES = 1 << 11
    UNBOLTING = 1 << 12
    ALIRO = 1 << 13


class LockState(data_model.Enum8):
    NOT_FULLY_LOCKED = 0
    LOCKED = 1
    UNLOCKED = 2
    UNLATCHED = 3


class LockType(data_model.Enum8):
    DEAD_BOLT = 0
    MAGNETIC = 1
    OTHER = 2
    MORTISE = 3
    RIM = 4
    LATCH_BOLT = 5
    CYLINDRICAL_LOCK = 6
    TUBULAR_LOCK = 7
    INTERCONNECTED_LOCK = 8
    DEAD_LATCH = 9
    DOOR_FURNITURE = 10
    EUROCYLINDER = 11


class DoorState(data_model.Enum8):
    DOOR_OPEN = 0
    DOOR_CLOSED = 1
    DOOR_JAMMED = 2
    DOOR_FORCED_OPEN = 3
    DOOR_UNSPECIFIED_ERROR = 4
    DOOR_AJAR = 5


class OperatingMode(data_model.Enum8):
    NORMAL = 0
    VACATION = 1
    PRIVACY = 2
    NO_REMOTE_LOCK_UNLOCK = 3
    PASSAGE = 4


class SupportedOperatingModes(data_model.Map16):
    """ビットが 0 のモードが対応。5〜15 ビットは常に 1(規格の取り決め)。"""

    NORMAL = 1 << 0
    VACATION = 1 << 1
    PRIVACY = 1 << 2
    NO_REMOTE_LOCK_UNLOCK = 1 << 3
    PASSAGE = 1 << 4


ONLY_NORMAL = 0xFFFF & ~SupportedOperatingModes.NORMAL


class LockRequest(tlv.Structure):
    PINCode = tlv.OctetStringMember(0, max_length=32, optional=True)


class DoorLock(data_model.Cluster):
    CLUSTER_ID = 0x0101

    LockState = data_model.EnumAttribute(0x0000, LockState, default=LockState.LOCKED, X_nullable=True)
    LockType = data_model.EnumAttribute(0x0001, LockType, default=LockType.DEAD_BOLT)
    ActuatorEnabled = data_model.BoolAttribute(0x0002, default=True)
    DoorState = data_model.EnumAttribute(0x0003, DoorState, default=DoorState.DOOR_CLOSED, X_nullable=True,
                                         feature=Feature.DOOR_POSITION_SENSOR)
    OperatingMode = data_model.EnumAttribute(0x0025, OperatingMode, default=OperatingMode.NORMAL, N_nonvolatile=True)
    SupportedOperatingModes = data_model.BitmapAttribute(0x0026, SupportedOperatingModes, default=ONLY_NORMAL)

    lock_door = data_model.Command(0x00, LockRequest)
    unlock_door = data_model.Command(0x01, LockRequest)

    def __init__(self):
        super().__init__()
        self.cluster_revision = 7
        self.feature_map = Feature.DOOR_POSITION_SENSOR


class DoorLockDevice(simple_device.SimpleDevice):
    """Matter の Door Lock 機器。施錠と解錠の依頼を外(door)に渡し、返ってきた状態を属性に写す。"""

    DEVICE_TYPE_ID = 0x000A
    REVISION = 3

    def __init__(self, name: str, on_lock: Callable[[], bool], on_unlock: Callable[[], bool]):
        super().__init__(name)
        self._identify = Identify()
        self.servers.append(self._identify)
        self.lock = DoorLock()
        self.lock.lock_door = self._lock
        self.lock.unlock_door = self._unlock
        self.servers.append(self.lock)
        self._on_lock, self._on_unlock = on_lock, on_unlock

    def _lock(self, session, request):
        if self._on_lock():
            self.lock.LockState = LockState.LOCKED

    def _unlock(self, session, request):
        if self._on_unlock():
            self.lock.LockState = LockState.UNLOCKED

    def mirror(self, locked: bool, door_open: bool) -> None:
        """door の状態を属性へ。"""
        self.lock.LockState = LockState.LOCKED if locked else LockState.UNLOCKED
        self.lock.DoorState = DoorState.DOOR_OPEN if door_open else DoorState.DOOR_CLOSED
