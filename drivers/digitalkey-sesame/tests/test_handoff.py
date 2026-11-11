"""鍵の役(管理者、利用者)と、対面での渡し方(QR、32 桁の打ち込み、受け取り、控え)の試験。

疑似の Sesame・疑似の保存領域(fake.py)だけを使う。実機にも Bluetooth にも、本物の鍵の置き場にも触れない。
試験の鍵は、この中で作った値か、試験用の決まった値(本物ではない)。
"""
import io
import json

import pytest

from digitalkey_sesame import cli, fake
from digitalkey_sesame.fake import FakeKV
from digitalkey_sesame.handoff import (HandoffError, HandoffLedger, ReceiveError, admin_code, check_admin_code,
                                       encode_handoff, format_admin_code, parse_handoff, qr_png, receive_key,
                                       typed_handoff)
from digitalkey_sesame.keystore import ROLE_ADMIN, ROLE_USER, KeyStore, KeyStoreError, device_key_from_dict
from sesame_helpers import REGISTERED, TEST_SECRET, UNREGISTERED, ZERO_KEY, make_key

SECRET = bytes.fromhex(TEST_SECRET)


def fake_backend():
    from digitalkey_sesame.app import Backend
    return Backend("fake", fake.FakeScanner, fake.FakeConnection, fake.fake_adapter_available)


# ---- 役 ----
def test_keys_without_a_role_field_are_admin_and_unknown_roles_are_user():
    """R-CM-14: 役の欄が無い今までの鍵は管理者で引き継ぐ。欄はあるが読めない値は、権限の小さい利用者として読む。"""
    legacy = {"device_uuid": UNREGISTERED, "model_id": "sesame_6_pro", "secret_hex": TEST_SECRET,
              "name": "玄関", "address": "", "registered_at": 1.0}
    assert device_key_from_dict(legacy).role == ROLE_ADMIN
    assert device_key_from_dict({**legacy, "role": "admin"}).is_admin
    assert device_key_from_dict({**legacy, "role": "user"}).role == ROLE_USER
    assert device_key_from_dict({**legacy, "role": "owner??"}).role == ROLE_USER
    assert device_key_from_dict({**legacy, "role": None}).role == ROLE_USER
    assert device_key_from_dict({**legacy, "future_field": 1}).secret_hex == TEST_SECRET     # 知らない欄は読み飛ばす
    with pytest.raises(KeyStoreError):
        device_key_from_dict("not a row")


async def test_role_survives_a_save_and_load_and_a_legacy_file_loads_as_admin(tmp_path):
    """R-CM-14: 役は保存して読み戻せる。役の欄が無い鍵のファイル(今までの版)は、管理者として読める。"""
    ks = KeyStore(path=tmp_path / "k.json")
    await ks.put(make_key(UNREGISTERED, role=ROLE_USER))
    await ks.put(make_key(REGISTERED, role=ROLE_ADMIN))
    again = await KeyStore(path=tmp_path / "k.json").all()
    assert again[UNREGISTERED].role == ROLE_USER and again[REGISTERED].role == ROLE_ADMIN
    raw = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
    for row in raw.values():
        del row["role"]
    (tmp_path / "old.json").write_text(json.dumps(raw), encoding="utf-8")
    old = await KeyStore(path=tmp_path / "old.json").all()
    assert {k.role for k in old.values()} == {ROLE_ADMIN}


async def test_a_failed_save_leaves_no_key_behind():
    """R-CM-16: 保存に失敗した鍵は、あるように見せない(控えを元に戻して例外を出す)。"""
    kv = FakeKV()
    ks = KeyStore(kv=kv)
    await ks.put(make_key(UNREGISTERED))
    kv.fail_set = True
    with pytest.raises(RuntimeError):
        await ks.put(make_key(REGISTERED))
    assert set(await ks.all()) == {UNREGISTERED}
    kv.fail_set = False
    kv.drop_writes = False

    class Refusing(FakeKV):
        async def set(self, key, value):
            return False
    with pytest.raises(KeyStoreError):
        await KeyStore(kv=Refusing()).put(make_key(UNREGISTERED))


# ---- 渡す中身 ----
def test_payload_carries_version_uuid_secret_role_and_code_but_no_name():
    """R-CM-15: QR の中身は 1 行で、版、UUID、32 桁、役、管理者の符号(管理者のときだけ)。人の名前は入らない。"""
    key = make_key(name="山田さんの家")
    user = encode_handoff(key, ROLE_USER)
    admin = encode_handoff(key, ROLE_ADMIN)
    assert user == f"DDSESAME1:{UNREGISTERED.upper()}:{TEST_SECRET.upper()}:U"
    assert admin == user[:-1] + "A:" + admin_code(SECRET, UNREGISTERED)
    assert "山田" not in user + admin and len(admin_code(SECRET, UNREGISTERED)) == 8
    for text in (user, admin):
        assert text == text.upper() and text.isascii()          # 大文字だけ: QR の英数字モードに収まる
    h = parse_handoff(admin)
    assert (h.device_uuid, h.secret, h.role, h.notice) == (UNREGISTERED, SECRET, ROLE_ADMIN, "")
    h = parse_handoff(user)
    assert (h.device_uuid, h.secret, h.role) == (UNREGISTERED, SECRET, ROLE_USER)
    assert TEST_SECRET not in repr(h) and TEST_SECRET not in repr(parse_handoff(admin))     # repr に鍵を出さない


def test_a_user_key_cannot_be_handed_over_as_admin_and_the_code_depends_on_the_key():
    """R-CM-14: 管理者として渡せるのは管理者の鍵だけ。符号は鍵と UUID から決まり、別の鍵・別の UUID では合わない。"""
    with pytest.raises(ValueError):
        encode_handoff(make_key(role=ROLE_USER), ROLE_ADMIN)
    assert encode_handoff(make_key(role=ROLE_USER), ROLE_USER).endswith(":U")
    code = admin_code(SECRET, UNREGISTERED)
    assert check_admin_code(SECRET, UNREGISTERED, format_admin_code(code)) and check_admin_code(SECRET, UNREGISTERED, code.lower())
    assert not check_admin_code(bytes(16), UNREGISTERED, code)
    assert not check_admin_code(SECRET, REGISTERED, code)
    assert not check_admin_code(SECRET, UNREGISTERED, "") and not check_admin_code(SECRET, UNREGISTERED, "zz")


def test_without_a_matching_code_the_key_enters_as_user_and_never_upgrades():
    """R-CM-14: 符号の無い鍵、符号が合わない鍵は、利用者として入る(管理者を名乗っても上がらない)。"""
    base = f"DDSESAME1:{UNREGISTERED.upper()}:{TEST_SECRET.upper()}"
    assert parse_handoff(base + ":U").role == ROLE_USER
    forged = parse_handoff(base + ":A")                    # 管理者を名乗るが符号が無い
    assert forged.role == ROLE_USER and "利用者として" in forged.notice
    wrong = parse_handoff(base + ":A:00000000")            # 符号が合わない
    assert wrong.role == ROLE_USER and "利用者として" in wrong.notice
    sneaky = parse_handoff(base + ":U:" + admin_code(SECRET, UNREGISTERED))   # 利用者として渡されたのに符号を添えても上がらない
    assert sneaky.role == ROLE_USER
    typed = typed_handoff(UNREGISTERED, "0011 2233 4455 6677 8899 aabb ccdd eeff")
    assert typed.role == ROLE_USER and typed.secret == SECRET


@pytest.mark.parametrize("bad", ["", "hello", "DDSESAME", "DDSESAME2:" + UNREGISTERED + ":" + TEST_SECRET + ":U",
                                 "DDSESAME1:not-a-uuid:" + TEST_SECRET + ":U", f"DDSESAME1:{UNREGISTERED}:{TEST_SECRET[:-2]}:U",
                                 f"DDSESAME1:{UNREGISTERED}:{'zz' * 16}:U", f"DDSESAME1:{UNREGISTERED}:{TEST_SECRET}:X",
                                 f"DDSESAME1:{UNREGISTERED}:{TEST_SECRET}", f"DDSESAME1:{UNREGISTERED}:{TEST_SECRET}:A:1:2", None])
def test_malformed_payloads_are_refused_without_echoing_the_input(bad):
    """R-CM-12: 形の違う QR は断る。例外の文に、入力(鍵、符号)を入れない。"""
    with pytest.raises(HandoffError) as ei:
        parse_handoff(bad)
    for piece in (TEST_SECRET, TEST_SECRET.upper(), TEST_SECRET[:-2], UNREGISTERED, "zzzz"):
        assert piece not in str(ei.value)


def test_a_qr_is_small_and_reads_back_with_a_real_decoder():
    """R-CM-15: 管理者の QR は英数字モードで版が小さく(読み取りやすい粗さ)、本物の読み取り(pyzbar)で中身が戻る。"""
    segno = pytest.importorskip("segno")
    payload = encode_handoff(make_key(), ROLE_ADMIN)
    qr = segno.make(payload, error="m", micro=False)
    assert qr.mode == "alphanumeric" and qr.version <= 6
    png = qr_png(payload)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pyzbar = pytest.importorskip("pyzbar.pyzbar", exc_type=ImportError)
    PIL = pytest.importorskip("PIL.Image")
    im = PIL.open(io.BytesIO(png)).convert("L")
    assert [d.data.decode() for d in pyzbar.decode((im.tobytes(), im.width, im.height))] == [payload]


async def test_the_reader_reads_a_tilted_compressed_photo_of_a_qr():
    """R-CM-15: 読み取りの口(ZbarQrReader)は、斜めで JPEG に圧縮した写真からも QR の文字を取り出す。QR でない画像は None。"""
    PIL = pytest.importorskip("PIL.Image")
    from digitalkey_sesame.qrread import ZbarQrReader
    reader = ZbarQrReader()
    if not reader.available():
        pytest.skip("libzbar が無い機械")
    payload = encode_handoff(make_key(), ROLE_ADMIN)
    photo = PIL.open(io.BytesIO(qr_png(payload))).convert("RGB").rotate(20, expand=True, fillcolor=(190, 190, 190)).resize((420, 420))
    jpeg = io.BytesIO()
    photo.save(jpeg, "JPEG", quality=55)
    assert await reader.read(jpeg.getvalue()) == payload
    blank = io.BytesIO()
    PIL.new("RGB", (200, 200), (255, 255, 255)).save(blank, "PNG")
    assert await reader.read(blank.getvalue()) is None
    assert await reader.read(b"not an image") is None


async def test_the_reader_degrades_to_none_when_the_decoder_is_missing(monkeypatch):
    """R-CM-15: libzbar を読み込めない機械では、読み取りは使えない(available が偽)と答え、例外を出さない(32 桁を打ち込む道だけになる)。"""
    from digitalkey_sesame import qrread
    reader = qrread.ZbarQrReader()
    monkeypatch.setitem(__import__("sys").modules, "pyzbar.pyzbar", None)    # import が失敗する
    assert reader.available() is False
    assert await reader.read(b"anything") is None


async def test_the_camera_scanner_reads_frames_one_at_a_time_and_stops_when_done():
    """R-CM-15: アプリのカメラの口は、画像の流れを読み取りに渡し、同じ文字は続けて渡さず、読み終えたら以後を捨てる。
    始めるときは背面のカメラを選び、画面に載り終えるまで数回待ち直す。許可されない(例外)と False を返す。"""
    fc = pytest.importorskip("flet_camera")
    from digitalkey_sesame.fake import FakeQrReader
    from digitalkey_sesame.qrcamera import CameraQrScanner

    class Frame:
        def __init__(self, data):
            self.bytes = data

    class Cam:
        def __init__(self, fail_first=0, deny=False):
            self.calls, self.fail_first, self.deny, self.started, self.stopped, self.picked = 0, fail_first, deny, False, False, None

        async def get_available_cameras(self):
            self.calls += 1
            if self.calls <= self.fail_first:
                raise RuntimeError("not mounted yet")
            return [type("C", (), {"lens_direction": fc.CameraLensDirection.FRONT})(),
                    type("C", (), {"lens_direction": fc.CameraLensDirection.BACK})()]

        async def initialize(self, desc, preset, enable_audio=True):
            if self.deny:
                raise PermissionError("camera denied")
            self.picked = desc.lens_direction

        async def start_image_stream(self):
            self.started = True

        async def stop_image_stream(self):
            self.stopped = True

    scanner = CameraQrScanner(FakeQrReader())
    scanner._camera = Cam(fail_first=2)
    got = []

    async def on_text(text):
        got.append(text)
        return text == "good"

    assert await scanner.start(on_text) is True
    assert scanner._camera.calls == 3 and scanner._camera.picked == fc.CameraLensDirection.BACK and scanner._camera.started
    for data in (b"bad", b"bad", b"\xff\xfe not text", b"good", b"other"):
        await scanner._on_frame(Frame(data))
    assert got == ["bad", "good"]                                    # 同じ文字は続けて渡さない。読み終えたあとは捨てる
    await scanner.stop()
    assert scanner._camera.stopped is True
    denied = CameraQrScanner(FakeQrReader())
    denied._camera = Cam(deny=True)
    assert await denied.start(on_text) is False


# ---- 受け取る ----
async def _registered_fake(uuid_s=UNREGISTERED):
    """疑似の Sesame に登録して、鍵の 16 バイトを返す(管理者の端末が登録した鍵に当たる)。"""
    adv = next(a for a in fake._FAKE_ADVS if str(a.device_uuid) == uuid_s)
    conn = fake.FakeConnection(adv)
    await conn.connect()
    return await conn.device.register()


async def test_receiving_saves_only_after_a_successful_login_and_keeps_the_role():
    """R-CM-13: 受け取る側は、その Sesame にログインできたときだけ保存する。符号つきなら管理者、無ければ利用者で入る。"""
    secret = await _registered_fake()
    ks = KeyStore(kv=FakeKV())
    h = parse_handoff(encode_handoff(make_key(secret_hex=secret.hex()), ROLE_USER))
    key = await receive_key(fake_backend(), ks, h, name="わたしの玄関", seconds=2)
    assert key.role == ROLE_USER and key.name == "わたしの玄関" and key.model_id == "sesame_6_pro"
    assert (await ks.get(UNREGISTERED)).role == ROLE_USER
    admin = parse_handoff(encode_handoff(make_key(secret_hex=secret.hex()), ROLE_ADMIN))
    ks2 = KeyStore(kv=FakeKV())
    assert (await receive_key(fake_backend(), ks2, admin, seconds=2)).role == ROLE_ADMIN


async def test_a_wrong_key_or_an_unregistered_or_missing_sesame_saves_nothing():
    """R-CM-13: ログインできない鍵(違う鍵)、未登録の Sesame、近くに無い Sesame では、何も保存しない。"""
    kv = FakeKV()
    ks = KeyStore(kv=kv)
    wrong = parse_handoff(encode_handoff(make_key(uuid_s=REGISTERED, secret_hex=TEST_SECRET), ROLE_USER))   # 疑似の鍵は 16 バイトの 0
    with pytest.raises(ReceiveError) as ei:
        await receive_key(fake_backend(), ks, wrong, seconds=2)
    assert "保存しません" in str(ei.value) and TEST_SECRET not in str(ei.value).lower()
    unregistered = parse_handoff(encode_handoff(make_key(uuid_s=UNREGISTERED), ROLE_USER))
    with pytest.raises(ReceiveError) as ei:
        await receive_key(fake_backend(), ks, unregistered, seconds=2)
    assert "未登録" in str(ei.value)
    gone = parse_handoff(encode_handoff(make_key(uuid_s="99999999-0000-0000-0000-000000000000"), ROLE_USER))
    with pytest.raises(ReceiveError) as ei:
        await receive_key(fake_backend(), ks, gone, seconds=0.2)
    assert "見つかりません" in str(ei.value)
    assert kv.data == {}


async def test_receiving_never_downgrades_an_admin_key_and_replaces_after_a_reset():
    """R-CM-14: 同じ鍵なら、利用者として渡し直されても管理者のまま。リセット後の別の鍵はログインできれば置き換え、渡された役になる。"""
    secret = await _registered_fake()
    ks = KeyStore(kv=FakeKV())
    await ks.put(make_key(secret_hex=secret.hex(), role=ROLE_ADMIN, registered_at=5.0))
    again = parse_handoff(encode_handoff(make_key(secret_hex=secret.hex()), ROLE_USER))
    got = await receive_key(fake_backend(), ks, again, seconds=2)
    assert got.role == ROLE_ADMIN and got.registered_at == 5.0
    # 鍵が変わった(リセットして登録し直した)ときは、新しい鍵でログインできれば置き換わり、役は渡された役
    fake._FAKE_DEVICES.clear()
    new_secret = await _registered_fake()
    assert new_secret != secret
    new = parse_handoff(encode_handoff(make_key(secret_hex=new_secret.hex()), ROLE_USER))
    got = await receive_key(fake_backend(), ks, new, seconds=2)
    assert got.secret_hex == new_secret.hex() and got.role == ROLE_USER and got.registered_at != 5.0


# ---- 控え ----
async def test_the_ledger_only_appends_and_never_holds_a_key():
    """R-CM-15: 渡した相手の控え(誰に、いつ、どの役で、どの道で)は書き足すだけ。前の行は変わらず、鍵は入らない。"""
    kv = FakeKV()
    ledger = HandoffLedger(kv)
    assert await ledger.entries() == []
    first = await ledger.record(UNREGISTERED, "  母  ", ROLE_USER, "qr", at=100.0)
    before = await ledger.entries()
    await ledger.record(UNREGISTERED, "兄", ROLE_ADMIN, "digits", at=200.0)
    await ledger.record(REGISTERED, "友人", ROLE_USER, "qr", at=300.0)
    rows = await ledger.entries()
    assert rows[0] == first == before[0] and first["to"] == "母" and len(rows) == 3
    assert [r["to"] for r in await ledger.entries(UNREGISTERED)] == ["母", "兄"]
    assert TEST_SECRET not in kv.data["digitalkey_sesame.handoffs"]
    assert not any(op == "remove" for op, _ in kv.log)          # 消す操作は一度も無い
    kv.fail_set = True
    with pytest.raises(RuntimeError):
        await ledger.record(UNREGISTERED, "失敗", ROLE_USER, "qr")
    kv.fail_set = False
    assert len(await ledger.entries()) == 3


# ---- PC の import ----
def _import(monkeypatch, capsys, secret_text, code_text, uuid_s=REGISTERED):
    monkeypatch.setattr(cli, "read_secret", lambda: secret_text)
    monkeypatch.setattr(cli, "read_admin_code", lambda: code_text)
    rc = cli.main(["--fake", "import", uuid_s])
    out = capsys.readouterr()
    return rc, out


def test_pc_import_enters_as_admin_only_with_a_matching_code(monkeypatch, capsys, tmp_path):
    """R-CM-14: PC の import も同じ規則。管理者の符号が合ったときだけ管理者、符号が無ければ利用者で、合わない符号は保存しない。
    符号は標準出力と標準エラーに出さない。"""
    code = admin_code(bytes(16), REGISTERED)
    ks_path = tmp_path / "keys" / "sesame-keys-fake.json"

    rc, out = _import(monkeypatch, capsys, ZERO_KEY, "")
    assert rc == 0 and "利用者" in out.out
    assert json.loads(ks_path.read_text(encoding="utf-8"))[REGISTERED]["role"] == ROLE_USER

    rc, out = _import(monkeypatch, capsys, ZERO_KEY, format_admin_code(code))
    assert rc == 0 and "管理者" in out.out
    assert json.loads(ks_path.read_text(encoding="utf-8"))[REGISTERED]["role"] == ROLE_ADMIN
    assert code not in (out.out + out.err).upper().replace(" ", "")

    ks_path.unlink()
    rc, out = _import(monkeypatch, capsys, ZERO_KEY, "00000000")           # 形は合うが、この鍵の符号ではない
    assert rc == 1 and "保存しません" in out.err and not ks_path.exists()
    assert "00000000" not in out.out + out.err
    rc, out = _import(monkeypatch, capsys, ZERO_KEY, "xyz")                # 形が違う
    assert rc == 1 and not ks_path.exists() and "xyz" not in out.err


def test_pc_register_makes_an_admin_key_and_keys_lists_the_role(capsys, tmp_path):
    """R-CM-14: その機械で登録した鍵は管理者。`keys` は役を出す(鍵そのものは出さない)。"""
    assert cli.main(["--fake", "register", UNREGISTERED]) == 0
    saved = json.loads((tmp_path / "keys" / "sesame-keys-fake.json").read_text(encoding="utf-8"))[UNREGISTERED]
    assert saved["role"] == ROLE_ADMIN
    capsys.readouterr()
    assert cli.main(["--fake", "keys"]) == 0
    out = capsys.readouterr().out
    assert "管理者" in out and saved["secret_hex"] not in out
