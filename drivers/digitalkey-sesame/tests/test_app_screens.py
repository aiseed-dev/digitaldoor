"""Sesame のアプリの画面の流れの試験: 役で画面を分けること、対面で渡す・受け取ること、鍵を画面とログに出さないこと。

Flet の Page は代わりのページ(sesame_helpers.Page)に差し替え、疑似の Sesame・疑似の保存領域・疑似のカメラ(fake.py)だけを使う。
実機にも Bluetooth にも、カメラにも、本物の鍵の置き場にも触れない。試験の鍵は、この中で作った値か、試験用の決まった値(本物ではない)。
"""
import io
import json
import logging

import pytest

ft = pytest.importorskip("flet")

from digitalkey_sesame import app as appmod
from digitalkey_sesame import fake
from digitalkey_sesame.fake import FakeCameraScanner, FakeGate, FakeKV
from digitalkey_sesame.handoff import HandoffLedger, encode_handoff, parse_handoff
from digitalkey_sesame.keystore import (DEFAULT_KEY, ROLE_ADMIN, ROLE_USER, KeyStore, Settings, format_secret_hex)
from sesame_helpers import (REGISTERED, TEST_SECRET, UNREGISTERED, Page, adv_for, find_all, make_key, texts)

OTHER_UUID = "11111111-2222-3333-4444-555555555599"
OTHER_SECRET = "ffeeddccbbaa99887766554433221100"


@pytest.fixture
def build(monkeypatch):
    """画面(SesameApp)を作る道具。`await build(鍵...)`。保存領域は疑似のメモリ、Bluetooth は疑似の Sesame。"""
    monkeypatch.setattr(appmod.SesameApp, "_close_dialog", lambda self, dlg: setattr(dlg, "open", False))

    async def _build(*keys, gate=None, scanner=None):
        page = Page()
        app = appmod.SesameApp(page)
        app.backend = appmod.Backend("fake", fake.FakeScanner, fake.FakeConnection, fake.fake_adapter_available)
        kv = FakeKV()
        app.kv = kv
        app.keystore = KeyStore(kv=kv)
        app.settings = await Settings(kv).load()
        app.ledger = HandoffLedger(kv)
        app.gate = gate or FakeGate()
        app.qr_scanner = scanner
        for k in keys:
            await app.keystore.put(k)
        app.keys = await app.keystore.all()
        app._refresh_roles()
        return app, page
    return _build


async def run_tasks(page, only=None):
    """画面が `page.run_task` に預けた仕事のうち、`only`(関数名の集合。省略は全部)を順に走らせる。ほかの仕事は預けたままにする。"""
    mine = [t for t in page.tasks if only is None or t[0].__name__ in only]
    page.tasks = [t for t in page.tasks if t not in mine]
    for fn, args, kwargs in mine:
        await fn(*args, **kwargs)


def snack_text(page):
    last = page.shown[-1]
    return last.content.value if isinstance(last, ft.SnackBar) else texts(last)


def app_everything(app, caplog):
    """画面のログ欄、logging、画面にあるすべての文字を、ひとつの文字列にする。"""
    return "\n".join([" ".join(c.value for c in app.log_view.controls), caplog.text,
                      texts(app.home_view), texts(app.admin_view), texts(app.panel)])


def no_secret(text, *secrets):
    flat = text.lower().replace(" ", "").replace("-", "")
    return all(s.lower().replace(" ", "") not in flat for s in secrets)


# ---- 役で画面を分ける ----
@pytest.mark.parametrize("keys,opens", [
    ([], True),                                               # 鍵が一つも無い: 最初の Sesame を登録するために開く
    ([make_key(role=ROLE_ADMIN)], True),
    ([make_key(role=ROLE_USER)], False),                      # 利用者の鍵しか無い: 開かない
    ([make_key(UNREGISTERED, role=ROLE_USER), make_key(REGISTERED, role=ROLE_ADMIN)], True),   # 一台に両方
])
async def test_the_admin_screen_opens_only_where_there_is_an_admin_key(build, keys, opens):
    """R-CM-14: 管理者の画面は、管理者の鍵がある端末(鍵が一つも無い端末を含む)だけで開く。利用者の鍵しか無い端末では開かない。"""
    app, page = await build(*keys)
    assert app.btn_admin.visible is opens
    await app.on_open_admin()
    assert app.admin_view.visible is opens and app.home_view.visible is (not opens)
    if not opens:
        assert "管理者の画面は開けません" in snack_text(page)


async def test_a_user_key_cannot_be_chosen_for_handing_over(build):
    """R-CM-14: 「渡す」の選びには管理者の鍵の Sesame だけが並び、利用者の鍵の Sesame は選べても渡せない(出さず、控えも残さない)。"""
    app, page = await build(make_key(UNREGISTERED, role=ROLE_USER), make_key(REGISTERED, secret_hex=OTHER_SECRET, role=ROLE_ADMIN))
    assert [o.key for o in app.target_dd.options] == [REGISTERED]
    app.target_dd.value = UNREGISTERED                     # 画面を細工して利用者の鍵を指しても
    app.to_field.value = "母"
    await app.on_hand_qr()
    await app.on_hand_digits()
    assert all(isinstance(d, ft.SnackBar) for d in page.shown) and not page.tasks
    assert await app.ledger.entries() == []


async def test_register_is_offered_only_in_the_admin_screen_for_an_unregistered_sesame(build):
    """R-CM-14: 「この Sesame を登録」は、管理者の画面で、未登録の Sesame とつないでいるときだけ出る。"""
    class Conn:
        is_connected = True
        class device:
            logged_in = False
    app, _ = await build()
    app.conn = Conn()
    app.current_adv = adv_for(UNREGISTERED, registered=False)
    app._set_connected_ui(True)
    assert app.btn_register.visible is False               # 利用者の画面では出ない
    await app.on_open_admin()
    app._set_connected_ui(True)
    assert app.btn_register.visible is True
    app.current_adv = adv_for(REGISTERED, registered=True)
    app._set_connected_ui(True)
    assert app.btn_register.visible is False


async def test_registering_makes_an_admin_key_and_the_admin_screen_stays_open(build):
    """R-CM-14: その端末で登録した鍵は管理者の鍵になる。疑似の Sesame と実際に登録して確かめる。"""
    app, page = await build()
    await app.on_open_admin()
    app.found[UNREGISTERED] = (fake._FAKE_ADVS[0], fake._FAKE_ADVS[0])
    await app.connect_to(UNREGISTERED)
    assert app.btn_register.visible is True
    await app.on_register()
    assert app.keys[UNREGISTERED].role == ROLE_ADMIN and (await app.keystore.get(UNREGISTERED)).is_admin
    assert [o.key for o in app.target_dd.options] == [UNREGISTERED]


async def test_the_guard_note_is_on_the_admin_screen_and_in_every_handover_dialog(build, caplog):
    """R-CM-14: 画面に「これは画面の歯止めで、鍵の守りではありません」をそのまま書く(管理者の画面と、渡すときの確かめと表示)。"""
    app, page = await build(make_key(role=ROLE_ADMIN))
    note = "これは画面の歯止めで、鍵の守りではありません"
    assert note in texts(app.admin_view)
    app.to_field.value = "母"
    await app.on_hand_qr()
    assert note in texts(page.shown[-1])
    page.shown[-1].actions[0].on_click(None)
    await run_tasks(page, only={"_show_qr"})
    assert note in texts(page.shown[-1])
    await app.on_hand_digits()
    assert note in texts(page.shown[-1])


async def test_forgetting_a_key_says_what_stays_and_closes_the_admin_door_when_the_last_admin_key_goes(build):
    """R-CM-14: 「鍵を削除」の確かめは、ほかの端末のコピーの扱いを書く(管理者の鍵では、渡した相手の鍵が消えないことも)。
    最後の管理者の鍵を消して利用者の鍵だけが残ると、管理者の画面の入口は消える。"""
    app, page = await build(make_key(UNREGISTERED, role=ROLE_ADMIN), make_key(REGISTERED, secret_hex=OTHER_SECRET, role=ROLE_USER))
    app.current_adv = adv_for(REGISTERED)
    await app.on_forget_key()
    text = texts(page.shown[-1])
    assert "ほかの端末" in text and "コピーがあれば" in text and "コピーが無い" in text and "渡した相手" not in text
    app.current_adv = adv_for(UNREGISTERED)
    await app.on_forget_key()
    text = texts(page.shown[-1])
    assert "ほかの端末" in text and "渡した相手の鍵は、消えません" in text
    assert app.btn_admin.visible is True
    await page.shown[-1].actions[0].on_click(None)                 # 削除
    assert set(app.keys) == {REGISTERED} and app.btn_admin.visible is False
    assert set(await app.keystore.all()) == {REGISTERED}


# ---- 管理者の画面を開くときの端末の認証 ----
async def test_the_device_auth_gate_is_asked_only_when_the_admin_chose_it(build):
    """R-CM-14: 管理者の画面を開くときの端末の認証は、管理者が選んだときだけ求める。通らなければ開かない。"""
    gate = FakeGate(allow=True)
    app, page = await build(make_key(), gate=gate)
    await app.on_open_admin()
    assert app.admin_view.visible and gate.verified == 0            # 選んでいなければ尋ねない
    app.on_close_admin()
    app.auth_switch.value = True
    await app.on_auth_toggle()                                      # 選ぶ: 使えるか確かめてから設定を残す
    assert gate.prepared == 1 and app.settings["require_device_auth"] is True
    assert (await Settings(app.kv).load())["require_device_auth"] is True
    await app.on_open_admin()
    assert gate.verified >= 2 and app.admin_view.visible            # 求める
    app.on_close_admin()
    gate.allow = False
    await app.on_open_admin()
    assert not app.admin_view.visible and "端末の認証" in snack_text(page)
    gate.allow = True
    await app.on_open_admin()
    app.auth_switch.value = False
    await app.on_auth_toggle()
    assert app.settings["require_device_auth"] is False


async def test_the_device_auth_setting_refuses_to_turn_on_where_the_device_cannot_do_it(build):
    """R-CM-14: 端末の認証を使えない端末(PC、画面ロックの無い端末)では、設定を入れられない。"""
    app, page = await build(make_key(), gate=FakeGate(available=False))
    app._render_auth_note()
    assert app.auth_switch.disabled is True and "使えません" in app.auth_note.value
    app.auth_switch.value = True
    await app.on_auth_toggle()
    assert app.auth_switch.value is False and app.settings["require_device_auth"] is False
    # 認証が通らない端末でも、設定は入らない
    app2, _ = await build(make_key(), gate=FakeGate(allow=False))
    app2.auth_switch.value = True
    await app2.on_auth_toggle()
    assert app2.auth_switch.value is False and app2.settings["require_device_auth"] is False


# ---- 渡す ----
def decode_qr_image(dlg):
    """ダイアログの中の QR の画像を、本物の読み取り(pyzbar)で読み戻す。"""
    pyzbar = pytest.importorskip("pyzbar.pyzbar", exc_type=ImportError)
    PIL = pytest.importorskip("PIL.Image")
    img = find_all(dlg, ft.Image)[0]
    im = PIL.open(io.BytesIO(img.src)).convert("L")
    return [d.data.decode() for d in pyzbar.decode((im.tobytes(), im.width, im.height))]


async def test_handing_over_by_qr_asks_first_records_the_ledger_and_shows_a_readable_qr(build, caplog):
    """R-CM-15: QR で渡すときは、確かめを挟み、相手の呼び名を求め、了承されたときだけ QR を出し、控えを残す。
    出した QR は読み戻せて、役と管理者コードが合う。ログと画面の記録欄には鍵もコードも出ない(R-CM-12)。"""
    caplog.set_level(logging.DEBUG)
    key = make_key(secret_hex=TEST_SECRET, role=ROLE_ADMIN)
    app, page = await build(key)
    await app.on_hand_qr()                                         # 呼び名が空
    assert isinstance(page.shown[-1], ft.SnackBar) and "呼び名" in snack_text(page) and not page.tasks
    app.to_field.value = "母"
    await app.on_hand_qr()
    confirm = page.shown[-1]
    assert "撮られない所" in texts(confirm) and "カメラアプリ" in texts(confirm) and "母" in texts(confirm)
    assert not find_all(confirm, ft.Image) and await app.ledger.entries() == []     # 確かめの段階では出さず、控えも残さない
    confirm.actions[1].on_click(None)                              # キャンセル
    assert not page.tasks and await app.ledger.entries() == []
    await app.on_hand_qr()
    page.shown[-1].actions[0].on_click(None)                       # 出す
    await run_tasks(page, only={"_show_qr"})
    shown = page.shown[-1]
    assert decode_qr_image(shown) == [encode_handoff(key, ROLE_USER)]
    assert parse_handoff(decode_qr_image(shown)[0]).role == ROLE_USER
    rows = await app.ledger.entries(UNREGISTERED)
    assert [(r["to"], r["role"], r["via"]) for r in rows] == [("母", ROLE_USER, "qr")]
    assert "あと" in texts(shown) and "母" in app.ledger_list.controls[0].value
    assert no_secret(app_everything(app, caplog), TEST_SECRET, format_secret_hex(TEST_SECRET))
    # 管理者として渡す
    app.to_field.value = "兄"
    app.role_seg.selected = [ROLE_ADMIN]
    await app.on_hand_qr()
    page.shown[-1].actions[0].on_click(None)
    await run_tasks(page, only={"_show_qr"})
    payload = decode_qr_image(page.shown[-1])[0]
    assert payload == encode_handoff(key, ROLE_ADMIN) and parse_handoff(payload).role == ROLE_ADMIN
    from digitalkey_sesame.handoff import admin_code
    assert no_secret(app_everything(app, caplog), TEST_SECRET, admin_code(key.secret, UNREGISTERED))
    assert [r["role"] for r in await app.ledger.entries()] == [ROLE_USER, ROLE_ADMIN]


async def test_the_shown_qr_closes_by_itself_and_leaves_nothing_on_the_screen(build):
    """R-CM-15: 画面に出す時間は短く、過ぎたら自動で閉じる。閉じたら QR の部品も画面から消える。閉じるボタンでも同じ。"""
    app, page = await build(make_key())
    app.to_field.value = "母"
    await app.on_hand_qr()
    page.shown[-1].actions[0].on_click(None)
    await run_tasks(page, only={"_show_qr"})
    dlg = page.shown[-1]
    assert find_all(dlg, ft.Image) and appmod.SHOW_SECONDS <= 120
    _, args, _ = next(t for t in page.tasks if t[0].__name__ == "_count_down")
    await app._count_down(args[0], 1, args[2], args[3])            # 1 秒の持ち時間で走らせる
    assert dlg.open is False and not find_all(dlg, ft.Image) and "閉じました" in texts(dlg)
    # 閉じるボタン
    await app.on_hand_qr()
    page.shown[-1].actions[0].on_click(None)
    await run_tasks(page, only={"_show_qr"})
    dlg2 = page.shown[-1]
    dlg2.actions[0].on_click(None)
    assert dlg2.open is False and not find_all(dlg2, ft.Image)


async def test_showing_the_digits_for_a_pc_asks_first_and_adds_the_code_only_for_an_admin_handover(build, caplog):
    """R-CM-12: 32 桁を見せるのは、確かめのあと、渡す画面の中だけ。管理者として渡すときだけ管理者コードも出る。閉じると消え、ログには出ない。"""
    caplog.set_level(logging.DEBUG)
    key = make_key(secret_hex=TEST_SECRET, role=ROLE_ADMIN)
    from digitalkey_sesame.handoff import admin_code, format_admin_code
    code = format_admin_code(admin_code(key.secret, key.device_uuid))
    app, page = await build(key)
    app.to_field.value = "PC"
    await app.on_hand_digits()
    confirm = page.shown[-1]
    assert "開けられます" in texts(confirm) and format_secret_hex(TEST_SECRET) not in texts(confirm)
    confirm.actions[0].on_click(None)
    await run_tasks(page, only={"_show_digits"})
    user_view = page.shown[-1]
    assert format_secret_hex(TEST_SECRET) in texts(user_view) and code not in texts(user_view)
    assert no_secret(app_everything(app, caplog), TEST_SECRET)
    user_view.actions[0].on_click(None)
    assert TEST_SECRET not in texts(user_view) and "閉じました" in texts(user_view)
    app.role_seg.selected = [ROLE_ADMIN]
    await app.on_hand_digits()
    page.shown[-1].actions[0].on_click(None)
    await run_tasks(page, only={"_show_digits"})
    admin_view = page.shown[-1]
    assert format_secret_hex(TEST_SECRET) in texts(admin_view) and code in texts(admin_view)
    assert no_secret(app_everything(app, caplog), TEST_SECRET, code)
    admin_view.actions[0].on_click(None)
    assert code not in texts(admin_view)
    assert [(r["role"], r["via"]) for r in await app.ledger.entries()] == [(ROLE_USER, "digits"), (ROLE_ADMIN, "digits")]


async def test_nothing_is_shown_when_the_ledger_cannot_be_written(build):
    """R-CM-15: 控えを残せないときは、QR も 32 桁も出さない(渡したのに控えが無い状態を作らない)。"""
    app, page = await build(make_key())
    app.to_field.value = "母"
    app.kv.fail_set = True
    await app.on_hand_qr()
    page.shown[-1].actions[0].on_click(None)
    await run_tasks(page, only={"_show_qr"})
    assert isinstance(page.shown[-1], ft.SnackBar) and "出しません" in snack_text(page)
    app.kv.fail_set = False
    assert await app.ledger.entries() == []


async def test_the_ledger_view_marks_rows_from_before_a_reset(build):
    """R-CM-15: 渡した相手の控えは、新しい行ほど上。リセットして登録し直した鍵(登録の時刻が後)では、それより前の行に「リセット前」の印が付く(消さない)。"""
    key = make_key(registered_at=1000.0)
    app, _ = await build(key)
    await app.ledger.record(UNREGISTERED, "母", ROLE_USER, "qr", at=500.0)
    await app.ledger.record(UNREGISTERED, "兄", ROLE_ADMIN, "digits", at=1500.0)
    await app._render_ledger()
    lines = [c.value for c in app.ledger_list.controls]
    assert "兄" in lines[0] and "リセット前" not in lines[0]
    assert "母" in lines[1] and "リセット前" in lines[1]
    assert len(await app.ledger.entries()) == 2


# ---- 受け取る ----
async def _giver_key():
    """渡す側の端末が持つ管理者の鍵: 疑似の Sesame に登録して作る。"""
    adv = fake._FAKE_ADVS[0]
    conn = fake.FakeConnection(adv)
    await conn.connect()
    secret = await conn.device.register()
    return make_key(UNREGISTERED, secret_hex=secret.hex(), role=ROLE_ADMIN)


async def test_receiving_a_user_qr_makes_a_user_key_whose_admin_screen_stays_closed(build, caplog):
    """R-CM-14: 利用者として渡された鍵を受け取った人は、その鍵で開け閉めできるが、管理者の画面は開かない。"""
    caplog.set_level(logging.DEBUG)
    giver = await _giver_key()
    app, page = await build()
    done, msg = await app.accept_text(encode_handoff(giver, ROLE_USER), "わが家の玄関")
    assert done and "利用者" in msg
    saved = await app.keystore.get(UNREGISTERED)
    assert saved.role == ROLE_USER and saved.name == "わが家の玄関" and saved.secret_hex == giver.secret_hex
    assert app.btn_admin.visible is False and len(app.key_list.controls) == 1
    await app.on_open_admin()
    assert not app.admin_view.visible
    assert no_secret(app_everything(app, caplog), giver.secret_hex, msg)
    # 受け取った鍵で、実際に開け閉めできる
    await app.connect_key(UNREGISTERED)
    assert app.conn is not None and app.conn.device.logged_in
    await app.on_unlock()
    assert fake._FAKE_DEVICES[UNREGISTERED].locked is False


async def test_receiving_an_admin_qr_makes_an_admin_key(build):
    """R-CM-14: 管理者として渡された鍵は、コードが合えば管理者の鍵になり、その端末で管理者の画面が開く。コードが無ければ利用者のまま。"""
    giver = await _giver_key()
    app, _ = await build(make_key(REGISTERED, role=ROLE_USER))
    done, msg = await app.accept_text(encode_handoff(giver, ROLE_ADMIN))
    assert done and "管理者" in msg and (await app.keystore.get(UNREGISTERED)).role == ROLE_ADMIN
    assert app.btn_admin.visible is True
    # コードを落とした管理者の QR は、利用者として入る
    app2, _ = await build()
    forged = encode_handoff(giver, ROLE_ADMIN).rsplit(":", 1)[0]
    done, msg = await app2.accept_text(forged)
    assert done and "利用者として" in msg and (await app2.keystore.get(UNREGISTERED)).role == ROLE_USER


async def test_a_wrong_or_unreadable_qr_saves_nothing_and_shows_no_secret(build, caplog):
    """R-CM-13: ログインできない鍵、読めない QR、近くに無い Sesame では、何も保存せず、画面の文にも鍵を出さない。"""
    caplog.set_level(logging.DEBUG)
    app, page = await build()
    wrong = encode_handoff(make_key(REGISTERED, secret_hex=TEST_SECRET), ROLE_USER)       # 疑似の Sesame 5 の鍵は 0 だけ
    done, msg = await app.accept_text(wrong)
    assert done is False and "保存しません" in msg
    done, msg2 = await app.accept_text("https://example.com/not-a-key")
    assert done is False and "QR ではありません" in msg2
    done, msg3 = await app.accept_text(encode_handoff(make_key(UNREGISTERED), ROLE_USER))   # 未登録
    assert done is False and "未登録" in msg3
    assert await app.keystore.all() == {} and app.kv.data.get(DEFAULT_KEY) in (None, "{}")
    assert no_secret(app_everything(app, caplog) + msg + msg2 + msg3, TEST_SECRET)


async def test_the_receive_dialog_reads_the_qr_with_the_apps_own_camera_and_closes(build):
    """R-CM-15: 受け取る画面は、アプリのカメラ(差し替えのインターフェース)で読んだ QR から受け取り、保存できたら閉じてカメラを止める。"""
    giver = await _giver_key()
    scanner = FakeCameraScanner(text=encode_handoff(giver, ROLE_USER))
    app, page = await build(scanner=scanner)
    await app.on_receive()
    dlg = page.shown[-1]
    assert scanner.started and "カメラアプリ" in texts(dlg)
    import asyncio
    for _ in range(40):
        if dlg.open is False:
            break
        await asyncio.sleep(0.1)
    assert dlg.open is False and (await app.keystore.get(UNREGISTERED)).role == ROLE_USER
    assert scanner.stopped is True


async def test_the_receive_dialog_keeps_scanning_after_a_bad_qr_and_points_to_typing_when_the_camera_is_off(build):
    """R-CM-15: 合わない QR のあとも読み続ける。カメラを始められない、読み取りが無い端末では、32 桁を打ち込む道を案内する。"""
    import asyncio
    giver = await _giver_key()
    app, page = await build()
    scanner = FakeCameraScanner(text="DDSESAME1:garbage")
    app.qr_scanner = scanner
    await app.on_receive()
    dlg = page.shown[-1]
    await asyncio.sleep(0.5)
    assert dlg.open is True and any("形が違います" in (c.value or "") for c in find_all(dlg, ft.Text))   # 閉じずに、理由を出す
    assert await app.keystore.all() == {}
    scanner.text = encode_handoff(giver, ROLE_USER)                 # 次に正しい QR が読まれる
    for _ in range(40):
        if dlg.open is False:
            break
        await asyncio.sleep(0.1)
    assert dlg.open is False and (await app.keystore.get(UNREGISTERED)) is not None
    await scanner.stop()
    # カメラを始められない
    app2, page2 = await build(scanner=FakeCameraScanner(ok=False))
    await app2.on_receive()
    assert "32 桁を打ち込む" in texts(page2.shown[-1])
    # 読み取りが無い端末
    app3, page3 = await build(scanner=None)
    await app3.on_receive()
    assert "読めません" in texts(page3.shown[-1]) and "32 桁を打ち込む" in texts(page3.shown[-1])


async def test_typing_the_32_digits_enters_as_a_user_after_a_login_check(build, caplog):
    """R-CM-15: 読み取りが使えないときの 32 桁の打ち込みは、利用者として入る。ログインできたときだけ保存し、入力欄は使い終わると空になる。"""
    caplog.set_level(logging.DEBUG)
    giver = await _giver_key()
    app, page = await build()
    await app.on_receive_typed()
    dlg = page.shown[-1]
    search = find_all(dlg, ft.OutlinedButton)[0]
    dd = find_all(dlg, ft.Dropdown)[0]
    secret_field, name_field = find_all(dlg, ft.TextField)
    assert secret_field.password is True
    app.found.clear()
    await search.on_click(None)
    assert UNREGISTERED in [o.key for o in dd.options] and REGISTERED in [o.key for o in dd.options]
    dd.value = UNREGISTERED
    secret_field.value = format_secret_hex(TEST_SECRET)             # 違う鍵
    await dlg.actions[0].on_click(None)
    assert await app.keystore.all() == {} and secret_field.value == ""
    secret_field.value = "12 34"                                    # 形が違う
    await dlg.actions[0].on_click(None)
    assert "32 桁" in [c for c in find_all(dlg, ft.Text) if "桁" in (c.value or "")][-1].value and await app.keystore.all() == {}
    secret_field.value = format_secret_hex(giver.secret_hex)        # 正しい鍵
    name_field.value = "友人宅"
    await dlg.actions[0].on_click(None)
    saved = await app.keystore.get(UNREGISTERED)
    assert saved.role == ROLE_USER and saved.name == "友人宅" and secret_field.value == "" and dlg.open is False
    assert no_secret(app_everything(app, caplog), giver.secret_hex, TEST_SECRET)


async def test_a_phone_to_phone_handover_end_to_end(build, caplog):
    """R-CM-15: 渡す端末が QR を出し、受け取る端末が自分のカメラ(疑似)でそれを読んで受け取るまで。ネットワークは通らず、受け取った側で管理者の画面は開かない。"""
    import asyncio
    caplog.set_level(logging.DEBUG)
    giver_key = await _giver_key()
    giver, gpage = await build(giver_key)
    giver.to_field.value = "妹"
    await giver.on_hand_qr()
    gpage.shown[-1].actions[0].on_click(None)
    await run_tasks(gpage, only={"_show_qr"})
    payload = decode_qr_image(gpage.shown[-1])[0]                   # 渡す側の画面の QR を、読み取りで読み戻す
    receiver, rpage = await build(scanner=FakeCameraScanner(text=payload))
    await receiver.on_receive()
    for _ in range(40):
        if rpage.shown[-1].open is False:
            break
        await asyncio.sleep(0.1)
    key = await receiver.keystore.get(UNREGISTERED)
    assert key.role == ROLE_USER and key.secret_hex == giver_key.secret_hex
    await receiver.on_open_admin()
    assert not receiver.admin_view.visible
    assert [(r["to"], r["role"]) for r in await giver.ledger.entries()] == [("妹", ROLE_USER)]
    for a in (giver, receiver):
        assert no_secret(app_everything(a, caplog), giver_key.secret_hex)


# ---- 起動と鍵の置き場 ----
async def test_start_on_the_desktop_with_the_fake_sesame_uses_json_files(monkeypatch, tmp_path):
    """R-CM-14: PC の疑似の画面は、鍵を疑似の JSON(sesame-keys-fake.json)から読み、設定と控えは別の JSON に置く。今までの鍵(役の欄なし)は管理者で現れる。"""
    monkeypatch.setattr(appmod, "FAKE_MODE", True)
    monkeypatch.setattr(appmod.SesameApp, "_close_dialog", lambda self, dlg: setattr(dlg, "open", False))
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "sesame-keys-fake.json").write_text(json.dumps({UNREGISTERED: {
        "device_uuid": UNREGISTERED, "model_id": "sesame_6_pro", "secret_hex": TEST_SECRET, "name": "玄関",
        "address": "", "registered_at": 1.0}}), encoding="utf-8")
    page = Page()
    page.platform = ft.PagePlatform.LINUX
    app = appmod.SesameApp(page)
    await app.start()
    assert app.keys[UNREGISTERED].role == ROLE_ADMIN and app.btn_admin.visible and app.backend.name == "fake"
    assert len(app.key_list.controls) == 1
    await app.settings.set("history_tag", "母のスマホ")
    assert json.loads((keys / "sesame-app-fake.json").read_text(encoding="utf-8"))["digitalkey_sesame.settings"]
    assert not (keys / "sesame-keys.json").exists()                  # 本物の鍵のファイルには触れない


async def test_the_phone_moves_keys_into_the_vault_on_first_start_and_keeps_working_if_it_cannot(monkeypatch, tmp_path, caplog):
    """R-CM-16: スマートフォンの起動で、今までの保存領域の鍵が保管庫に移る。保管庫が書けないときは、鍵を失わず今までの置き場で動き、利用者に知らせる。"""
    from digitalkey_sesame import securestore
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(appmod.SesameApp, "_close_dialog", lambda self, dlg: setattr(dlg, "open", False))
    legacy_row = {"device_uuid": UNREGISTERED, "model_id": "sesame_6_pro", "secret_hex": TEST_SECRET, "name": "玄関",
                  "address": "", "registered_at": 1.0}

    async def start_phone(legacy, vault):
        monkeypatch.setattr(ft, "SharedPreferences", lambda: legacy)
        monkeypatch.setattr(securestore, "make_vault", lambda: vault)
        page = Page()
        page.platform = ft.PagePlatform.ANDROID
        app = appmod.SesameApp(page)
        data_kv = await app._open_mobile_stores()
        app.settings = await Settings(data_kv).load()
        app.keys = await app.keystore.all()
        return app, page, data_kv

    # 移せる
    legacy = FakeKV({DEFAULT_KEY: json.dumps({UNREGISTERED: legacy_row})})
    vault = FakeKV()
    app, page, data_kv = await start_phone(legacy, vault)
    assert data_kv is vault and app.migration.state == "migrated" and DEFAULT_KEY not in legacy.data
    assert app.keys[UNREGISTERED].secret_hex == TEST_SECRET and app.keys[UNREGISTERED].role == ROLE_ADMIN
    # 次の起動は、保管庫だけを使う
    app, page, data_kv = await start_phone(legacy, vault)
    assert app.migration.state == "none" and app.keys[UNREGISTERED].secret_hex == TEST_SECRET
    # 保管庫が書けない: 今までの置き場のまま動く
    legacy = FakeKV({DEFAULT_KEY: json.dumps({UNREGISTERED: legacy_row})})
    broken = FakeKV()
    broken.fail_set = True
    app, page, data_kv = await start_phone(legacy, broken)
    assert data_kv is legacy and app.migration.state == "failed" and app.keys[UNREGISTERED].secret_hex == TEST_SECRET
    assert isinstance(page.shown[-1], ft.SnackBar) and "元は消していません" in snack_text(page)
    assert DEFAULT_KEY in legacy.data and no_secret(app_everything(app, caplog), TEST_SECRET)
    # 保管庫を作れない(部品が読み込めない等)
    legacy = FakeKV({DEFAULT_KEY: json.dumps({UNREGISTERED: legacy_row})})

    def no_vault():
        raise ImportError("flet_secure_storage")
    monkeypatch.setattr(ft, "SharedPreferences", lambda: legacy)
    monkeypatch.setattr(securestore, "make_vault", no_vault)
    page = Page()
    page.platform = ft.PagePlatform.ANDROID
    app = appmod.SesameApp(page)
    data_kv = await app._open_mobile_stores()
    app.keys = await app.keystore.all()
    assert data_kv is legacy and app.keys[UNREGISTERED].secret_hex == TEST_SECRET and DEFAULT_KEY in legacy.data


async def test_a_keystore_that_cannot_be_read_stops_the_app_without_writing(monkeypatch):
    """R-CM-16: 鍵の置き場を読めないときは、空と見なさずに止まる(読めない中身を上書きしない)。"""
    from digitalkey_sesame import securestore
    monkeypatch.setattr(appmod.SesameApp, "_close_dialog", lambda self, dlg: setattr(dlg, "open", False))
    legacy = FakeKV()
    legacy.fail_get = True
    monkeypatch.setattr(ft, "SharedPreferences", lambda: legacy)
    monkeypatch.setattr(securestore, "make_vault", lambda: FakeKV())
    page = Page()
    page.platform = ft.PagePlatform.ANDROID
    app = appmod.SesameApp(page)
    await app.start()
    assert "鍵の置き場" in app.adapter_text.value and app.backend is None
    assert not any(op == "set" for op, _ in legacy.log)
