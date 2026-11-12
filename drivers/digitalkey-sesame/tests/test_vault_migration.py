"""スマートフォンの鍵を、今までの保存領域(SharedPreferences)から端末の保管庫(SecureStorage)へ移す試験。

一番大事なのは、どんな失敗でも鍵を失わないこと。疑似の保存領域(fake.FakeKV)に失敗を起こして、元の鍵が残り、
アプリが今までの置き場のまま動くことを確かめる。実機にも本物の鍵にも触れない。試験の鍵は試験用の決まった値(本物ではない)。
"""
import json
import logging

import pytest

from digitalkey_sesame.fake import FakeKV
from digitalkey_sesame.keystore import DEFAULT_KEY, MOVED_MARKER, ROLE_ADMIN, ROLE_USER, KeyStore, migrate_kv
from sesame_helpers import REGISTERED, TEST_SECRET, UNREGISTERED

OTHER_SECRET = "ffeeddccbbaa99887766554433221100"


def row(uuid_s, secret, name="玄関", role=None):
    r = {"device_uuid": uuid_s, "model_id": "sesame_6_pro", "secret_hex": secret, "name": name,
         "address": "AA:BB", "registered_at": 1.5}
    if role:
        r["role"] = role
    return r


def legacy_raw(*rows):
    """今までのバージョンが SharedPreferences に書いた形(役の欄なし)。"""
    return json.dumps({r["device_uuid"]: r for r in rows}, indent=2)


def stores(*rows):
    old = FakeKV({DEFAULT_KEY: legacy_raw(*rows)} if rows else {})
    return old, FakeKV()


def secrets_in(kv):
    raw = kv.data.get(DEFAULT_KEY)
    return {u: r["secret_hex"] for u, r in json.loads(raw).items()} if raw else {}


A = row(UNREGISTERED, TEST_SECRET)
B = row(REGISTERED, OTHER_SECRET, name="勝手口")


async def test_migration_copies_verifies_then_removes_the_original():
    """R-CM-16: コピーする → 読み戻して一致を確かめる → 元を消す、の順。移した鍵は全部そろい、役の欄の無い鍵は管理者として書かれる。"""
    old, new = stores(A, B)
    mig = await migrate_kv(old, new)
    assert (mig.state, mig.use, mig.moved) == ("migrated", "new", 2)
    assert secrets_in(new) == {UNREGISTERED: TEST_SECRET, REGISTERED: OTHER_SECRET}
    assert DEFAULT_KEY not in old.data and old.data[MOVED_MARKER] == "1"
    assert {r["role"] for r in json.loads(new.data[DEFAULT_KEY]).values()} == {ROLE_ADMIN}
    gets_after_set = [i for i, e in enumerate(new.log) if e == ("get", DEFAULT_KEY) and i > new.log.index(("set", DEFAULT_KEY))]
    assert gets_after_set                                   # 書いたあとに読み戻している
    keys = await KeyStore(kv=new).all()
    assert keys[UNREGISTERED].secret_hex == TEST_SECRET and keys[UNREGISTERED].name == "玄関" and keys[UNREGISTERED].is_admin


async def test_the_original_is_never_removed_before_the_vault_read_back_matches():
    """R-CM-16: 保管庫への書き込みの前後で、元の削除は一度も起きていない(削除の前の時点で、保管庫に鍵がある)。"""
    class Watching(FakeKV):
        async def remove(self, key):
            # 削除が呼ばれた時点で、保管庫には全部の鍵がそろっていなければならない
            assert secrets_in(new) == {UNREGISTERED: TEST_SECRET, REGISTERED: OTHER_SECRET}
            return await super().remove(key)
    old = Watching({DEFAULT_KEY: legacy_raw(A, B)})
    new = FakeKV()
    assert (await migrate_kv(old, new)).state == "migrated"


@pytest.mark.parametrize("flag,state_use", [("fail_set", "old"), ("drop_writes", "old"), ("fail_get_after_set", "old")])
async def test_a_failed_write_or_a_lost_write_keeps_the_original_and_falls_back(flag, state_use):
    """R-CM-16: 保管庫に書けない、書いたはずが読めない、書いた直後に読み戻せない、のどれでも、元は残り、今までの置き場のまま動く。"""
    old, new = stores(A, B)
    setattr(new, flag, True)
    mig = await migrate_kv(old, new)
    assert mig.state == "failed" and mig.use == state_use
    assert secrets_in(old) == {UNREGISTERED: TEST_SECRET, REGISTERED: OTHER_SECRET}      # 元はそのまま
    assert ("remove", DEFAULT_KEY) not in old.log
    # アプリはその置き場のまま鍵を使える
    keys = await KeyStore(kv=old).all()
    assert keys[UNREGISTERED].secret_hex == TEST_SECRET and keys[UNREGISTERED].is_admin


async def test_an_unreadable_vault_is_not_written_to():
    """R-CM-16: 保管庫を読めないときは書かない(読めない中身を空と見なして上書きしない)。元は残る。"""
    old, new = stores(A)
    new.fail_get = True
    mig = await migrate_kv(old, new)
    assert (mig.state, mig.use) == ("failed", "old")
    assert not any(op == "set" for op, _ in new.log)
    assert secrets_in(old) == {UNREGISTERED: TEST_SECRET}


async def test_a_corrupt_vault_or_corrupt_original_is_left_alone():
    """R-CM-16: 中身が壊れていて読めないときは、どちらにも書かず、消さない。"""
    old, new = stores(A)
    new.data[DEFAULT_KEY] = "{not json"
    mig = await migrate_kv(old, new)
    assert (mig.state, mig.use) == ("failed", "old") and new.data[DEFAULT_KEY] == "{not json" and secrets_in(old)
    old2 = FakeKV({DEFAULT_KEY: "{broken"})
    new2 = FakeKV()
    mig = await migrate_kv(old2, new2)
    assert mig.state == "failed" and old2.data[DEFAULT_KEY] == "{broken" and new2.data == {}


async def test_an_unreadable_original_does_not_touch_the_vault():
    """R-CM-16: 元を読めないときは、保管庫に何も書かず、何も消さない。保管庫に鍵があればそれを使い、無ければ元の置き場のまま
    (読めないので、アプリは空と見なさずに止まる)。"""
    old, new = stores(A)
    old.fail_get = True
    new.data[DEFAULT_KEY] = legacy_raw(B)
    mig = await migrate_kv(old, new)
    assert (mig.state, mig.use) == ("failed", "new") and ("set", DEFAULT_KEY) not in new.log
    assert secrets_in(new) == {REGISTERED: OTHER_SECRET}
    mig = await migrate_kv(old, FakeKV())
    assert (mig.state, mig.use) == ("failed", "old")


async def test_a_failed_removal_keeps_a_copy_and_the_next_start_finishes():
    """R-CM-16: 移して確かめたあとで元を消せなくても、鍵は両方にある。次の起動で、書き直さずに元を消す。"""
    old, new = stores(A)
    old.fail_remove = True
    mig = await migrate_kv(old, new)
    assert (mig.state, mig.use) == ("kept", "new") and secrets_in(old) and secrets_in(new)
    old.fail_remove = False
    sets_before = new.log.count(("set", DEFAULT_KEY))
    mig = await migrate_kv(old, new)
    assert mig.state == "migrated" and DEFAULT_KEY not in old.data and secrets_in(new) == {UNREGISTERED: TEST_SECRET}
    assert new.log.count(("set", DEFAULT_KEY)) == sets_before          # 同じ中身を書き直さない


async def test_a_crash_after_writing_the_vault_is_recovered_on_the_next_start():
    """R-CM-16: 保管庫に書いた直後に落ちても(読み戻しの前)、元は残る。次の起動で読み戻しを確かめて、元を消す。"""
    old, new = stores(A, B)
    new.fail_get_after_set = True
    assert (await migrate_kv(old, new)).state == "failed" and secrets_in(old)
    new.fail_get_after_set = False
    assert secrets_in(new) == {UNREGISTERED: TEST_SECRET, REGISTERED: OTHER_SECRET}     # 書いた分は保管庫にある
    mig = await migrate_kv(old, new)
    assert mig.state == "migrated" and DEFAULT_KEY not in old.data


async def test_a_different_key_for_the_same_sesame_is_kept_not_overwritten():
    """R-CM-16: 保管庫に同じ Sesame の別の鍵があるときは、保管庫の鍵を正として、元を消さずに残す(どちらも失わない)。"""
    old, new = stores(A)
    new.data[DEFAULT_KEY] = json.dumps({UNREGISTERED: {**row(UNREGISTERED, OTHER_SECRET), "role": ROLE_USER}})
    mig = await migrate_kv(old, new)
    assert (mig.state, mig.use) == ("kept", "new")
    assert secrets_in(new) == {UNREGISTERED: OTHER_SECRET} and secrets_in(old) == {UNREGISTERED: TEST_SECRET}
    assert json.loads(new.data[DEFAULT_KEY])[UNREGISTERED]["role"] == ROLE_USER


async def test_keys_already_in_the_vault_are_kept_and_only_missing_ones_are_added():
    """R-CM-16: 保管庫に既にある鍵は変えず、足りない鍵だけを足す。"""
    old, new = stores(A, B)
    new.data[DEFAULT_KEY] = json.dumps({UNREGISTERED: {**row(UNREGISTERED, TEST_SECRET), "role": ROLE_USER, "name": "保管庫の名前"}})
    mig = await migrate_kv(old, new)
    assert mig.state == "migrated"
    saved = json.loads(new.data[DEFAULT_KEY])
    assert saved[UNREGISTERED]["name"] == "保管庫の名前" and saved[UNREGISTERED]["role"] == ROLE_USER
    assert saved[REGISTERED]["secret_hex"] == OTHER_SECRET and DEFAULT_KEY not in old.data


async def test_nothing_to_move_and_a_lost_vault_is_reported():
    """R-CM-16: 移す物が無ければ何もしない。移し終えた印があるのに保管庫が空なら、鍵を失った疑いとして知らせる。"""
    old, new = stores()
    assert (await migrate_kv(old, new)).state == "none" and new.data == {}
    old.data[MOVED_MARKER] = "1"
    new.data[DEFAULT_KEY] = json.dumps({})                       # 全部削除したあとの空の保管庫は、失った扱いにしない
    assert (await migrate_kv(old, new)).state == "none"
    new.data.clear()                                             # 保管庫の中身ごと消えた
    mig = await migrate_kv(old, new)
    assert mig.state == "lost" and "失われた" in mig.detail


async def test_migration_touches_only_the_key_item_and_never_clears(caplog):
    """R-CM-16: 移すのは鍵の 1 項目だけ。保存領域のほかの項目には触れず、全消し(clear)もしない。結果の文とログに鍵は入らない。"""
    caplog.set_level(logging.DEBUG)
    old, new = stores(A, B)
    old.data["flutter.other_setting"] = "keep me"
    mig = await migrate_kv(old, new)
    assert old.data["flutter.other_setting"] == "keep me"
    assert all(k in (DEFAULT_KEY, MOVED_MARKER) for _, k in new.log + [x for x in old.log if x[0] != "get"])
    for text in (mig.detail, caplog.text):
        assert TEST_SECRET not in text and OTHER_SECRET not in text
    failing_old, failing_new = stores(A)
    failing_new.fail_set = True
    assert TEST_SECRET not in (await migrate_kv(failing_old, failing_new)).detail


async def test_the_vault_is_configured_not_to_wipe_itself_and_not_to_leave_the_device():
    """R-CM-16: 保管庫の設定: エラーで中身を消さない(reset_on_error を無効)、iOS はこの端末だけ(ThisDeviceOnly)で iCloud に載せない。
    端末の認証の目印の保管庫も、同じく消さない設定で、別の共有の名前を使い、認証を求める。"""
    pytest.importorskip("flet_secure_storage")
    from flet_secure_storage import AccessControlFlag, KeychainAccessibility, KeyCipherAlgorithm
    from digitalkey_sesame import securestore
    android, ios = securestore.vault_options()
    assert android.reset_on_error is False and android.enforce_biometrics is False
    assert ios.accessibility == KeychainAccessibility.UNLOCKED_THIS_DEVICE and ios.synchronizable is False
    gate = securestore.FletDeviceAuthGate()
    assert gate._s.android_options.reset_on_error is False and gate._s.android_options.enforce_biometrics is True
    assert gate._s.android_options.key_cipher_algorithm == KeyCipherAlgorithm.AES_GCM_NO_PADDING
    assert gate._s.android_options.shared_preferences_name not in (None, "FlutterSecureStorage")
    assert gate._s.ios_options.access_control_flags == [AccessControlFlag.USER_PRESENCE]
    assert gate._s.ios_options.accessibility == KeychainAccessibility.UNLOCKED_THIS_DEVICE and gate._s.ios_options.synchronizable is False


def test_the_build_config_excludes_the_app_data_from_backups():
    """R-CM-16: flet build の設定で、Android のアプリのデータをバックアップから外し(allowBackup を false)、カメラと顔認証の用途の文を置く。"""
    import tomllib
    from pathlib import Path
    cfg = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["flet"]
    assert cfg["android"]["manifest_application"]["allowBackup"] == "false"       # 文字列で書く(テンプレートが android:allowBackup="..." に差し込む)
    assert cfg["android"]["permission"]["android.permission.CAMERA"] is True
    assert cfg["ios"]["info"]["NSCameraUsageDescription"] and cfg["ios"]["info"]["NSFaceIDUsageDescription"]
    deps = " ".join(cfg["android"]["dependencies"] + cfg["ios"]["dependencies"])
    for name in ("flet-secure-storage", "flet-camera", "pyzbar", "flet-libzbar", "segno", "pillow"):
        assert name in deps
