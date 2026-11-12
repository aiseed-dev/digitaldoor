"""端末の安全な保管庫(Flet の SecureStorage。flet-secure-storage)に鍵を置く部分と、管理者の画面を開くときの端末の認証。

スマートフォンの鍵は、今までのバージョンでは SharedPreferences(アプリの保存領域)に平文で置いていました。このバージョンからは、
Android Keystore(Android)とキーチェーン(iOS)に守られた保管庫に置きます。初めて起動したときに移し(keystore.migrate_kv)、
移せたことを確かめてから元を消します。

保管庫の設定(flet-secure-storage 1.0.4、中身は flutter_secure_storage 10.3.1):

  reset_on_error = False        既定は True で、読み書きでエラーが起きると保管庫の中身を全部消す。鍵を失わないため無効にする。
                                エラーのときは例外になり、アプリは今までの保存領域のまま動く
  iOS accessibility             UNLOCKED_THIS_DEVICE(端末が解錠されている間だけ読め、別の端末へは移らない=ThisDeviceOnly)。
                                synchronizable は偽のまま(iCloud キーチェーンに乗らない)
  Android のバックアップ         保管庫の中身はバックアップに乗ると別の端末では復号できず、エラーになる。バックアップから外す設定は
                                pyproject.toml の [tool.flet.android.manifest_application] allowBackup = "false" で行う

管理者の画面を開くときの認証: 別の保管庫(共有の名前も別)に目印を一つ置き、読むときに端末の認証(生体認証か PIN)を求める。
  Android   enforce_biometrics = True と AES-GCM の鍵(鍵を使うたびに認証を求める)。MainActivity は FlutterFragmentActivity で足りる
  iOS       access_control_flags = [USER_PRESENCE](生体認証か端末のパスコード)。Info.plist に NSFaceIDUsageDescription が要る
認証できたか(目印を読めたか)だけを見る。端末に画面ロックや生体認証が無いと保管庫が例外を出すので、設定を入れられない。
鍵そのものを認証で守るのではない(鍵の保管庫は認証なしで読める)。管理者の画面に入る前の関所で、画面の歯止めです。

実機(Android、iOS)で動くことは、まだ確かめていない。flet_secure_storage は使うときまで import しない(PC では要らない)。
"""
from __future__ import annotations

from typing import Protocol

GATE_KEY = "digitalkey_sesame.gate"
GATE_VALUE = "ok"


class SecureKV:
    """flet-secure-storage の SecureStorage を、鍵の置き場のインターフェース(get/set/remove)に合わせる。"""

    def __init__(self, storage) -> None:
        self._s = storage

    async def get(self, key: str):
        return await self._s.get(key)

    async def set(self, key: str, value) -> bool:
        await self._s.set(key, value)      # SecureStorage.set は成功すると None を返し、失敗は例外
        return True

    async def remove(self, key: str):
        await self._s.remove(key)
        return True


def vault_options():
    """鍵の保管庫の設定 (android_options, ios_options)。"""
    from flet_secure_storage import AndroidOptions, IOSOptions, KeychainAccessibility
    return (
        AndroidOptions(reset_on_error=False),
        IOSOptions(accessibility=KeychainAccessibility.UNLOCKED_THIS_DEVICE, synchronizable=False),
    )


def make_vault() -> SecureKV:
    """鍵の保管庫。Flet のページの中(イベントの処理の中)で呼ぶ(SecureStorage は作ると自動でページに登録される)。"""
    from flet_secure_storage import SecureStorage
    android, ios = vault_options()
    return SecureKV(SecureStorage(android_options=android, ios_options=ios))


class DeviceAuthGate(Protocol):
    """管理者の画面を開く前の関所。`available()` が偽なら設定を入れられない。"""

    def available(self) -> bool: ...
    async def prepare(self) -> None: ...      # 設定を入れるとき。認証が使えなければ例外
    async def verify(self) -> bool: ...       # 管理者の画面を開くとき。通れば True、取り消しや失敗は False


class FletDeviceAuthGate:
    """端末の認証(生体認証か PIN)を、保管庫の目印を読む形で求める。"""

    def __init__(self) -> None:
        from flet_secure_storage import (AccessControlFlag, AndroidOptions, IOSOptions, KeyCipherAlgorithm,
                                         KeychainAccessibility, SecureStorage)
        android = AndroidOptions(
            reset_on_error=False, enforce_biometrics=True,
            key_cipher_algorithm=KeyCipherAlgorithm.AES_GCM_NO_PADDING,
            shared_preferences_name="digitalkey_sesame_gate",
            biometric_prompt_title="管理者の画面を開く",
            biometric_prompt_subtitle="端末の認証(生体認証か PIN)で確かめます",
        )
        ios = IOSOptions(
            account_name="digitalkey_sesame_gate",
            accessibility=KeychainAccessibility.UNLOCKED_THIS_DEVICE, synchronizable=False,
            access_control_flags=[AccessControlFlag.USER_PRESENCE],
        )
        self._s = SecureStorage(android_options=android, ios_options=ios)

    def available(self) -> bool:
        return True

    async def prepare(self) -> None:
        await self._s.set(GATE_KEY, GATE_VALUE)

    async def verify(self) -> bool:
        try:
            return await self._s.get(GATE_KEY) == GATE_VALUE
        except Exception:
            return False


class NoGate:
    """端末の認証を使えない場所(PC など)。設定の入口を灰色にし、理由を出す。"""

    reason = "この端末では使えません(スマートフォンのアプリで使えます)"

    def available(self) -> bool:
        return False

    async def prepare(self) -> None:
        raise RuntimeError(self.reason)

    async def verify(self) -> bool:
        return True
