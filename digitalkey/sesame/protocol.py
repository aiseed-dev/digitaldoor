"""Transport-independent pieces of the Sesame OS3 BLE protocol.

Everything here is pure Python + pycryptodome/cryptography so it can be unit
tested without a Bluetooth adapter and reused on any platform (bleak on
desktop, a native BLE bridge on Android).

Byte layouts were taken from the official Android SDK
(sesame-sdk/.../ble/os3/base/*.kt, ble/SesameBleReceiver.kt,
open/devices/CHSesame5.kt) and the API_document repository.
"""
from __future__ import annotations

import struct
import time
import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Hash import CMAC
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

# --------------------------------------------------------------------------
# Bluetooth SIG identifiers registered by CANDY HOUSE
# --------------------------------------------------------------------------
SESAME_SERVICE_UUID = "0000fd81-0000-1000-8000-00805f9b34fb"
SESAME_WRITE_CHAR_UUID = "16860002-a5ae-9856-b6d3-dbb4c676993e"   # write without response
SESAME_NOTIFY_CHAR_UUID = "16860003-a5ae-9856-b6d3-dbb4c676993e"  # notify
CANDY_HOUSE_COMPANY_ID = 0x055A

# productType -> (deviceModel, human name)   (CHProductModel in CHDeivceProtocols.kt)
PRODUCT_MODELS: dict[int, tuple[str, str]] = {
    0: ("sesame_2", "Sesame 3"),
    1: ("wm_2", "WiFi Module 2"),
    2: ("ssmbot_1", "Sesame Bot 1"),
    3: ("bike_1", "Sesame Bike 1"),
    4: ("sesame_4", "Sesame 4"),
    5: ("sesame_5", "Sesame 5"),
    6: ("bike_2", "Sesame Bike 2"),
    7: ("sesame_5_pro", "Sesame 5 Pro"),
    8: ("open_sensor_1", "Open Sensor 1"),
    9: ("ssm_touch_pro", "Sesame Touch 1 Pro"),
    10: ("ssm_touch", "Sesame Touch 1"),
    11: ("BLE_Connector_1", "BLE Connector 1"),
    13: ("hub_3", "Hub 3"),
    14: ("remote", "Remote"),
    15: ("remote_nano", "Remote Nano"),
    16: ("sesame_5_us", "Sesame 5 US"),
    17: ("bot_2", "Sesame Bot 2"),
    18: ("sesame_face_Pro", "Sesame Face 1 Pro"),
    19: ("sesame_face", "Sesame Face 1"),
    20: ("sesame_6", "Sesame 6"),
    21: ("sesame_6_pro", "Sesame 6 Pro"),
    22: ("sesame_face_pro_ai", "Sesame Face 1 Pro AI"),
    23: ("sesame_face_ai", "Sesame Face 1 AI"),
    24: ("open_sensor_2", "Open Sensor 2"),
    25: ("ssm_touch_2", "Sesame Touch 2"),
    26: ("ssm_touch_2_pro", "Sesame Touch 2 Pro"),
    27: ("sesame_face_2", "Sesame Face 2"),
    28: ("ssm_face_2_pro", "Sesame Face 2 Pro"),
    29: ("sesame_miwa", "Sesame miwa"),
    30: ("sesame_face_2_ai", "Sesame Face 2 AI"),
    31: ("sesame_face_2_pro_ai", "Sesame Face 2 Pro AI"),
    32: ("sesame_6_pro_slidingdoor", "Sesame 6 Pro SlidingDoor"),
}

# Product types that are OS3 "lock" devices handled by CHSesame5Device.kt
LOCK_PRODUCT_TYPES = {5, 7, 11, 16, 20, 21, 29, 32}


class OpCode(IntEnum):
    CREATE = 0x01
    READ = 0x02
    UPDATE = 0x03
    DELETE = 0x04
    SYNC = 0x05
    ASYNC = 0x06
    RESPONSE = 0x07
    PUBLISH = 0x08


class ItemCode(IntEnum):
    NONE = 0
    REGISTRATION = 1
    LOGIN = 2
    USER = 3
    HISTORY = 4
    VERSION_TAG = 5
    DISCONNECT_REBOOT_NOW = 6
    ENABLE_DFU = 7
    TIME = 8
    BLE_CONNECTION_PARAM = 9
    BLE_ADV_PARAM = 10
    AUTOLOCK = 11
    SERVER_ADV_KICK = 12
    SSMTOKEN = 13
    INITIAL = 14
    IRER = 15
    TIME_PHONE = 16
    MAGNET = 17
    HISTORY_DELETE = 18
    SENSOR_DETECT_INTERVAL_SETTING = 19
    LOCK_UNLOCK_SWITCH_POINT_SETTING = 20
    MECH_SETTING = 80
    MECH_STATUS = 81
    LOCK = 82
    UNLOCK = 83
    MOVE_TO = 84
    DRIVE_DIRECTION = 85
    STOP = 86
    DETECT_DIR = 87
    TOGGLE = 88
    CLICK = 89
    DOOR_OPEN = 90
    DOOR_CLOSE = 91
    OPS_CONTROL = 92
    RESET = 104
    BATTERY_VOLTAGE = 202
    SET_ADV_PRODUCT_TYPE = 205
    BLE_TX_POWER_SETTING = 206


class ResultCode(IntEnum):
    SUCCESS = 0
    INVALID_FORMAT = 1
    NOT_SUPPORTED = 2
    STORAGE_FAIL = 3
    INVALID_SIG = 4
    NOT_FOUND = 5
    UNKNOWN = 6
    BUSY = 7
    INVALID_PARAM = 8
    INVALID_ACTION = 9

    @classmethod
    def name_of(cls, value: int) -> str:
        try:
            return cls(value).name
        except ValueError:
            return f"UNKNOWN({value})"


class SegmentType(IntEnum):
    PLAIN = 1
    CIPHER = 2


# --------------------------------------------------------------------------
# Advertisement (SesameOS3):  manufacturer data for company 0x055A
#   [0..1] product type (LE u16)   [2] flags: bit0=registered bit1=has history
#   [3..18] 16-byte device UUID
# --------------------------------------------------------------------------
@dataclass
class Advertisement:
    address: str
    name: Optional[str]
    rssi: Optional[int]
    product_type: int
    is_registered: bool
    has_history: bool
    device_uuid: Optional[uuid.UUID]

    @property
    def model_id(self) -> str:
        return PRODUCT_MODELS.get(self.product_type, (f"unknown_{self.product_type}", ""))[0]

    @property
    def model_name(self) -> str:
        return PRODUCT_MODELS.get(self.product_type, ("", f"Unknown model {self.product_type}"))[1]

    @property
    def is_lock(self) -> bool:
        return self.product_type in LOCK_PRODUCT_TYPES


def parse_advertisement(address: str, name: Optional[str], rssi: Optional[int],
                        manufacturer_data: dict[int, bytes]) -> Optional[Advertisement]:
    """Return an Advertisement for a CANDY HOUSE device or None if it is not one."""
    mfg = manufacturer_data.get(CANDY_HOUSE_COMPANY_ID)
    if mfg is None or len(mfg) < 3:
        return None
    product_type = mfg[0]  # Kotlin: copyOfRange(0,1).toBigLong()
    flags = mfg[2]
    if product_type in (13,):  # Hub3 uses byte 1 for the registered bit
        is_registered = bool(mfg[1] & 1)
    else:
        is_registered = bool(flags & 1)
    has_history = bool(flags & 2)
    dev_uuid = None
    if len(mfg) >= 19:
        dev_uuid = uuid.UUID(bytes=bytes(mfg[3:19]))
    return Advertisement(address=address, name=name, rssi=rssi, product_type=product_type,
                         is_registered=is_registered, has_history=has_history,
                         device_uuid=dev_uuid)


# --------------------------------------------------------------------------
# Transport layer: 20-byte GATT segments  (SesameBleReceiver.kt / SesameBleTransmit)
#   header byte: bit0 = start-of-message, bits1.. = parsing type
#                (0 = more segments follow, 1 = last segment/plain, 2 = last/cipher)
# --------------------------------------------------------------------------
SEGMENT_PAYLOAD = 19


def split_segments(segment_type: SegmentType, data: bytes) -> list[bytes]:
    chunks: list[bytes] = []
    is_start = 1
    remaining = bytes(data)
    while True:
        if len(remaining) <= SEGMENT_PAYLOAD:
            header = (int(segment_type) << 1) | is_start
            chunks.append(bytes([header]) + remaining)
            return chunks
        chunks.append(bytes([is_start]) + remaining[:SEGMENT_PAYLOAD])
        remaining = remaining[SEGMENT_PAYLOAD:]
        is_start = 0


class SegmentReceiver:
    """Reassembles notifications into (SegmentType, payload) messages."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> Optional[tuple[SegmentType, bytes]]:
        if not chunk:
            return None
        header = chunk[0]
        is_start = header & 1
        parsing_type = header >> 1
        body = bytes(chunk[1:])
        if is_start:
            self._buffer = body
        else:
            self._buffer += body
        if parsing_type:
            buf, self._buffer = self._buffer, b""
            return SegmentType(parsing_type), buf
        return None


# --------------------------------------------------------------------------
# Security layer: AES-CCM with 4-byte tag  (SesameOS3BleCipher.kt)
#   nonce = counter (u64 LE) + sault (0x00 + 4-byte session token)
#   AAD   = b"\x00"
# --------------------------------------------------------------------------
class SesameCipher:
    def __init__(self, session_key: bytes, session_token: bytes) -> None:
        if len(session_key) != 16:
            raise ValueError("session key must be 16 bytes")
        if len(session_token) != 4:
            raise ValueError("session token must be 4 bytes")
        self._key = bytes(session_key)
        self._sault = b"\x00" + bytes(session_token)
        self.encrypt_counter = 0
        self.decrypt_counter = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = struct.pack("<Q", self.encrypt_counter) + self._sault
        self.encrypt_counter += 1
        c = AES.new(self._key, AES.MODE_CCM, nonce=nonce, mac_len=4)
        c.update(b"\x00")
        ct, tag = c.encrypt_and_digest(plaintext)
        return ct + tag

    def decrypt(self, ciphertext: bytes) -> bytes:
        nonce = struct.pack("<Q", self.decrypt_counter) + self._sault
        self.decrypt_counter += 1
        c = AES.new(self._key, AES.MODE_CCM, nonce=nonce, mac_len=4)
        c.update(b"\x00")
        return c.decrypt_and_verify(ciphertext[:-4], ciphertext[-4:])


def aes_cmac(key: bytes, data: bytes) -> bytes:
    """AES-CMAC (RFC 4493), full 16-byte tag."""
    mac = CMAC.new(bytes(key), ciphermod=AES)
    mac.update(bytes(data))
    return mac.digest()


def session_key_from_secret(device_secret: bytes, session_token: bytes) -> bytes:
    """sessionAuth = AES-CMAC(secretKey, token). Full 16 bytes are the CCM key;
    the first 4 bytes are sent in the login command."""
    return aes_cmac(device_secret, session_token)


# --------------------------------------------------------------------------
# Registration: ECDH on secp256r1 (EccKey.kt).  deviceSecret = X(shared)[0:16]
# --------------------------------------------------------------------------
class EphemeralKey:
    def __init__(self) -> None:
        self._private = ec.generate_private_key(ec.SECP256R1())

    def public_bytes_raw(self) -> bytes:
        """64-byte X||Y (uncompressed point without the 0x04 prefix)."""
        pub = self._private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return pub[1:]

    def derive_device_secret(self, peer_public_raw: bytes) -> bytes:
        if len(peer_public_raw) != 64:
            raise ValueError("peer public key must be 64 bytes")
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + bytes(peer_public_raw))
        shared = self._private.exchange(ec.ECDH(), peer)  # 32-byte X coordinate
        return shared[:16]


# --------------------------------------------------------------------------
# Application layer payloads
# --------------------------------------------------------------------------
def build_command(item_code: int, data: bytes = b"") -> bytes:
    return bytes([item_code & 0xFF]) + bytes(data)


def timestamp_le32(now: Optional[float] = None) -> bytes:
    """Long.toUInt32ByteArray(): unix seconds, little endian."""
    t = int(now if now is not None else time.time())
    return struct.pack("<I", t & 0xFFFFFFFF)


def build_registration(public_key_raw: bytes, now: Optional[float] = None) -> bytes:
    return build_command(ItemCode.REGISTRATION, bytes(public_key_raw) + timestamp_le32(now))


def build_login(session_key: bytes) -> bytes:
    return build_command(ItemCode.LOGIN, session_key[:4])


def build_time(now: Optional[float] = None) -> bytes:
    return build_command(ItemCode.TIME, timestamp_le32(now))


HISTORY_TAG_TYPE_BLE = 14  # UUID4HistoryTagTypeEnum.NAME_UUID_TYPE_ANDROID_USER_BLE_UUID


def build_history_tag(tag: bytes | str = b"") -> bytes:
    """CHDevice.historyTagBLE(): 16-bit big-endian tag type + tag, max 20 bytes."""
    if isinstance(tag, str):
        tag = tag.encode("utf-8")
    return (struct.pack(">H", HISTORY_TAG_TYPE_BLE) + bytes(tag))[:20]


def build_lock(tag: bytes | str = b"") -> bytes:
    return build_command(ItemCode.LOCK, build_history_tag(tag))


def build_unlock(tag: bytes | str = b"") -> bytes:
    return build_command(ItemCode.UNLOCK, build_history_tag(tag))


def build_toggle(tag: bytes | str = b"") -> bytes:
    return build_command(ItemCode.TOGGLE, build_history_tag(tag))


@dataclass
class Response:
    item_code: int
    result_code: int
    payload: bytes

    @property
    def ok(self) -> bool:
        return self.result_code == ResultCode.SUCCESS


@dataclass
class Publish:
    item_code: int
    payload: bytes


def parse_notification(plaintext: bytes) -> Response | Publish | None:
    """Decode a reassembled (and decrypted if needed) notification."""
    if len(plaintext) < 2:
        return None
    op = plaintext[0]
    if op == OpCode.RESPONSE:
        if len(plaintext) < 3:
            return None
        return Response(item_code=plaintext[1], result_code=plaintext[2], payload=bytes(plaintext[3:]))
    if op == OpCode.PUBLISH:
        return Publish(item_code=plaintext[1], payload=bytes(plaintext[2:]))
    return None


# --------------------------------------------------------------------------
# mech_status_t (7 bytes) / mech settings (6 bytes)   (CHSesame5.kt, 81_mechstatus.md)
# --------------------------------------------------------------------------
@dataclass
class MechStatus:
    battery_raw: int
    target: Optional[int]
    position: int
    flags: int

    @classmethod
    def parse(cls, data: bytes) -> "MechStatus":
        if len(data) < 7:
            raise ValueError(f"mech status needs 7 bytes, got {len(data)}")
        battery, target, position = struct.unpack_from("<Hhh", data, 0)
        flags = data[6]
        return cls(battery_raw=battery, target=None if target == -32768 else target,
                   position=position, flags=flags)

    @property
    def is_clutch_failed(self) -> bool: return bool(self.flags & 0x01)
    @property
    def is_in_lock_range(self) -> bool: return bool(self.flags & 0x02)
    @property
    def is_in_unlock_range(self) -> bool: return bool(self.flags & 0x04)
    @property
    def is_critical(self) -> bool: return bool(self.flags & 0x08)
    @property
    def is_stop(self) -> bool: return bool(self.flags & 0x10)
    @property
    def is_low_battery(self) -> bool: return bool(self.flags & 0x20)
    @property
    def is_clockwise(self) -> bool: return bool(self.flags & 0x40)

    @property
    def battery_voltage(self) -> float:
        # Sesame 5 series report the ADC value in millivolts/2 (see pysesame3 /
        # SesameSDK iOS CHSesame5MechStatus: batteryVoltage = battery * 2 / 1000)
        return self.battery_raw * 2 / 1000

    @property
    def battery_percent(self) -> int:
        """Piecewise-linear estimate used by the Sesame apps for 2x CR123A (6V)."""
        v = self.battery_voltage
        table = [(6.0, 100), (5.8, 50), (5.7, 40), (5.6, 32), (5.4, 21), (5.2, 13),
                 (5.1, 10), (5.0, 7), (4.8, 3), (4.6, 0)]
        if v >= table[0][0]:
            return 100
        if v <= table[-1][0]:
            return 0
        for (v_hi, p_hi), (v_lo, p_lo) in zip(table, table[1:]):
            if v_lo <= v <= v_hi:
                return int(round(p_lo + (p_hi - p_lo) * (v - v_lo) / (v_hi - v_lo)))
        return 0

    @property
    def state(self) -> str:
        if self.is_in_lock_range:
            return "locked"
        if self.is_in_unlock_range:
            return "unlocked"
        return "moving"


@dataclass
class MechSetting:
    lock_position: int
    unlock_position: int
    auto_lock_second: int

    @classmethod
    def parse(cls, data: bytes) -> "MechSetting":
        if len(data) < 6:
            raise ValueError(f"mech setting needs 6 bytes, got {len(data)}")
        lock_pos, unlock_pos, auto = struct.unpack_from("<hhh", data, 0)
        return cls(lock_position=lock_pos, unlock_position=unlock_pos, auto_lock_second=auto)


@dataclass
class RegistrationResult:
    mech_status: MechStatus
    mech_setting: MechSetting
    device_public_key: bytes

    @classmethod
    def parse(cls, payload: bytes) -> "RegistrationResult":
        if len(payload) < 77:
            raise ValueError(f"registration payload needs 77 bytes, got {len(payload)}")
        return cls(mech_status=MechStatus.parse(payload[0:7]),
                   mech_setting=MechSetting.parse(payload[7:13]),
                   device_public_key=bytes(payload[13:77]))
