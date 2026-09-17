"""CANDY HOUSE Sesame OS3 (Sesame 5 / 6 / 6 Pro) BLE protocol in pure Python. 公式アプリとクラウドを使わず、BLE で直接 登録→ログイン→施錠/解錠。

Reference: https://github.com/CANDY-HOUSE/SesameSDK_Android_with_DemoApp
           https://github.com/CANDY-HOUSE/API_document
"""
from .protocol import (
    SESAME_SERVICE_UUID,
    SESAME_WRITE_CHAR_UUID,
    SESAME_NOTIFY_CHAR_UUID,
    CANDY_HOUSE_COMPANY_ID,
    PRODUCT_MODELS,
    ItemCode,
    OpCode,
    ResultCode,
    SegmentType,
    Advertisement,
    MechStatus,
    MechSetting,
    parse_advertisement,
)
from .device import SesameDevice, SesameError
from .keystore import KeyStore, DeviceKey

__all__ = [
    "SESAME_SERVICE_UUID", "SESAME_WRITE_CHAR_UUID", "SESAME_NOTIFY_CHAR_UUID",
    "CANDY_HOUSE_COMPANY_ID", "PRODUCT_MODELS", "ItemCode", "OpCode", "ResultCode",
    "SegmentType", "Advertisement", "MechStatus", "MechSetting", "parse_advertisement",
    "SesameDevice", "SesameError", "KeyStore", "DeviceKey",
]
