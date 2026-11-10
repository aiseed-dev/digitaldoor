"""更新の配布(使い方.md「更新の配布」): 署名、段階配布、緊急の経路、自動で戻す、互換表、記録、サポート期限。"""
import io
import json
import sys
import tarfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from digitalkey import update as U

MAKER = Ed25519PrivateKey.generate()
OTHER = Ed25519PrivateKey.generate()
PY = sys.executable


def write_cfg(tmp: Path, *, stage="全棟", check="", notify="", block=False, components=None, keys=None) -> Path:
    data = tmp / "data"
    data.mkdir(exist_ok=True)
    (data / "台帳.txt").write_text("前からの記録", encoding="utf-8")
    comp = "".join(f'{k} = "{v}"\n' for k, v in (components or {}).items())
    keys = keys or [U.public_hex(MAKER)]
    f = tmp / "update.toml"
    f.write_text(f'''product = "digitalkey"
root = "{tmp / 'u'}"
data = "{data}"
stage = "{stage}"
trusted_keys = {json.dumps(keys)}
check = {json.dumps(check)}
notify = {json.dumps(notify)}
block_on_incompatible = {"true" if block else "false"}
[components]
{comp}''', encoding="utf-8")
    return f


def make_pkg(tmp: Path, version: str, *, key=MAKER, compat=None, body="print('ok')", reason="不具合") -> Path:
    src = tmp / f"src-{version}"
    (src / "bin").mkdir(parents=True, exist_ok=True)
    (src / "bin" / "app.py").write_text(body, encoding="utf-8")
    (src / "VERSION").write_text(version)
    out = tmp / f"{version}.ddupd"
    U.pack(key, product="digitalkey", version=version, reason=reason, summary=f"{version} の直し", source=src,
           out=out, compat=compat)
    return out


def make_index(tmp: Path, entries, *, serial=1, key=MAKER, name="index.json") -> Path:
    out = tmp / name
    U.make_index(key, serial=serial, entries=[{"halted": False, "support_until": "", "emergency": None, **e}
                                              for e in entries], out=out)
    return out


def updater(cfg: Path) -> U.Updater:
    return U.Updater(U.Config.load(cfg))


def test_signed_package_is_applied_and_switches_current(tmp_path):
    """R-DV-30 R-DV-32 R-DV-34: 署名の合う包みを当てると current が付け替わり、控えと秒数が記録に残る。"""
    u = updater(write_cfg(tmp_path))
    r = u.apply(make_pkg(tmp_path, "1.0.0"), make_index(tmp_path, [{"version": "1.0.0", "stage": "全棟"}]))
    assert r["result"] == "適用" and u.current() == "1.0.0"
    assert (tmp_path / "u" / "current" / "VERSION").read_text() == "1.0.0"
    with tarfile.open(r["backup"]) as tf:
        assert any(n.endswith("台帳.txt") for n in tf.getnames())
    row = u.log.rows()[-1]
    assert row["event"] == "適用" and row["version"] == "1.0.0" and row["seconds"] >= 0


@pytest.mark.parametrize("break_it", ["other_key_package", "tampered_file", "other_key_index", "no_index_sig"])
def test_anything_unsigned_or_altered_is_refused_and_changes_nothing(tmp_path, break_it):
    """R-DV-30: 信じない鍵の包み、署名の後に書き換えた中身、信じない鍵の表、署名の無い表は、どれも当てず、current は変わらない。"""
    u = updater(write_cfg(tmp_path))
    u.apply(make_pkg(tmp_path, "1.0.0"), make_index(tmp_path, [{"version": "1.0.0", "stage": "全棟"}]))
    entries = [{"version": "1.0.0", "stage": "全棟"}, {"version": "1.1.0", "stage": "全棟"}]
    pkg = make_pkg(tmp_path, "1.1.0", key=OTHER if break_it == "other_key_package" else MAKER)
    idx = make_index(tmp_path, entries, serial=2, key=OTHER if break_it == "other_key_index" else MAKER,
                     name="i2.json")
    if break_it == "tampered_file":
        with tarfile.open(pkg) as tf:
            members = {m.name: tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()}
        members["files/bin/app.py"] = b"import os; os.system('bad')"
        with tarfile.open(pkg, "w:gz") as tf:
            for name, data in members.items():
                U._add(tf, name, data)
    if break_it == "no_index_sig":
        U.sig_path(idx).unlink()
    with pytest.raises(U.Refused):
        u.apply(pkg, idx)
    assert u.current() == "1.0.0" and not (tmp_path / "u" / "releases" / "1.1.0").exists()
    assert u.log.rows()[-1]["event"] == "拒否"


def test_paths_outside_the_release_are_refused(tmp_path):
    """R-DV-30: 署名が合っていても、道が外を指す包みは当てない。"""
    u = updater(write_cfg(tmp_path))
    manifest = {"format": U.FORMAT, "product": "digitalkey", "version": "9.9.9", "reason": "不具合", "summary": "x",
                "files": {"../escape.txt": __import__("hashlib").sha256(b"x").hexdigest()}, "compat": {}}
    data = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    pkg = tmp_path / "evil.ddupd"
    with tarfile.open(pkg, "w:gz") as tf:
        U._add(tf, "manifest.json", data)
        U._add(tf, "manifest.sig", U.sign(MAKER, data).encode())
        U._add(tf, "files/../escape.txt", b"x")
    with pytest.raises(U.Refused):
        u.apply(pkg, make_index(tmp_path, [{"version": "9.9.9", "stage": "全棟"}]))
    assert not (tmp_path / "u" / "escape.txt").exists() and not (tmp_path / "escape.txt").exists()


def test_staged_rollout_and_halt(tmp_path):
    """R-DV-31: 試験導入棟までの版は、試験導入棟と開発には届き、全棟には届かない。止めた版はどの段にも届かない。"""
    for stage, reach in (("開発", True), ("試験導入棟", True), ("全棟", False)):
        d = tmp_path / stage
        d.mkdir()
        u = updater(write_cfg(d, stage=stage))
        pkg = make_pkg(d, "2.0.0")
        idx = make_index(d, [{"version": "2.0.0", "stage": "試験導入棟"}])
        if reach:
            assert u.apply(pkg, idx)["result"] == "適用"
        else:
            with pytest.raises(U.Refused, match="段"):
                u.apply(pkg, idx)
        halted = make_index(d, [{"version": "2.0.1", "stage": "全棟", "halted": True}], serial=2, name="h.json")
        with pytest.raises(U.Refused, match="止め"):
            u.apply(make_pkg(d, "2.0.1"), halted)
    # 開発の段だけの版は、試験導入棟には届かない
    d = tmp_path / "x"
    d.mkdir()
    with pytest.raises(U.Refused):
        updater(write_cfg(d, stage="試験導入棟")).apply(make_pkg(d, "3.0.0"),
                                                      make_index(d, [{"version": "3.0.0", "stage": "開発"}]))


def test_emergency_route_skips_the_stages_and_is_recorded(tmp_path):
    """R-DV-39: 緊急の判断がある版は、段を待たずに全棟の機械にも届き、判断(誰が、なぜ)が記録に残る。"""
    u = updater(write_cfg(tmp_path, stage="全棟"))
    idx = make_index(tmp_path, [{"version": "1.0.1", "stage": "開発",
                                 "emergency": {"by": "製造者の品質責任者", "why": "鍵が漏れる不具合"}}])
    r = u.apply(make_pkg(tmp_path, "1.0.1", reason="セキュリティ"), idx)
    assert r["result"] == "適用"
    row = u.log.rows()[-1]
    assert row["emergency"] == {"by": "製造者の品質責任者", "why": "鍵が漏れる不具合"} and row["reason"] == "セキュリティ"


def test_failed_check_rolls_back_and_notifies(tmp_path):
    """R-DV-32: 当てた後の確かめに失敗したら、直前の版へ自動で戻し、戻したことを記録に残し、知らせる。"""
    note = tmp_path / "note.txt"
    check = f"{PY} bin/app.py"
    notify = f"{PY} -c \"import sys; open({str(note)!r}, 'w').write(sys.stdin.read())\""
    u = updater(write_cfg(tmp_path, check=check, notify=notify))
    u.apply(make_pkg(tmp_path, "1.0.0"), make_index(tmp_path, [{"version": "1.0.0", "stage": "全棟"}]))
    entries = [{"version": "1.0.0", "stage": "全棟"}, {"version": "1.1.0", "stage": "全棟"}]
    r = u.apply(make_pkg(tmp_path, "1.1.0", body="raise SystemExit(3)"), make_index(tmp_path, entries, serial=2,
                                                                                     name="i2.json"))
    assert r["result"] == "自動で戻した" and "3" in r["why"]
    assert u.current() == "1.0.0"
    assert u.log.rows()[-1]["event"] == "自動で戻した"
    assert "1.1.0" in note.read_text(encoding="utf-8") and "1.0.0" in note.read_text(encoding="utf-8")
    assert Path(r["backup"]).exists()


def test_manual_rollback(tmp_path):
    """R-DV-32: 手で直前の版へ戻せ、記録に残る。"""
    u = updater(write_cfg(tmp_path))
    entries = [{"version": "1.0.0", "stage": "全棟"}, {"version": "1.1.0", "stage": "全棟"}]
    u.apply(make_pkg(tmp_path, "1.0.0"), make_index(tmp_path, entries))
    u.apply(make_pkg(tmp_path, "1.1.0"), make_index(tmp_path, entries, name="i2.json"))
    assert u.rollback()["version"] == "1.0.0" and u.current() == "1.0.0"
    assert u.log.rows()[-1]["event"] == "手で戻した"


def test_an_older_index_cannot_replace_a_newer_one(tmp_path):
    """R-DV-30 R-DV-31: 前に受けた表より通し番号の小さい表は受けない(止めた版を古い表で戻させない)。"""
    u = updater(write_cfg(tmp_path))
    u.apply(make_pkg(tmp_path, "1.0.0"), make_index(tmp_path, [{"version": "1.0.0", "stage": "全棟"}], serial=5))
    old = make_index(tmp_path, [{"version": "1.1.0", "stage": "全棟"}], serial=4, name="old.json")
    with pytest.raises(U.Refused, match="古い"):
        u.apply(make_pkg(tmp_path, "1.1.0"), old)


def test_compat_table_warns_and_can_block(tmp_path):
    """R-DV-36: 互換表と合わない部品は警告として記録に残す。止める設定なら当てない。"""
    pkg = make_pkg(tmp_path, "1.0.0", compat={"matter-controller": ["1.5", "1.6"], "lock-firmware": ["2.1"]})
    idx = make_index(tmp_path, [{"version": "1.0.0", "stage": "全棟"}])
    a = tmp_path / "a"; a.mkdir()
    r = updater(write_cfg(a, components={"matter-controller": "1.4"})).apply(pkg, idx)
    assert r["result"] == "適用" and len(r["compat_warnings"]) == 2
    assert any("matter-controller" in w and "1.4" in w for w in r["compat_warnings"])
    b = tmp_path / "b"; b.mkdir()
    ub = updater(write_cfg(b, components={"matter-controller": "1.4"}, block=True))
    with pytest.raises(U.Refused, match="互換"):
        ub.apply(pkg, idx)
    c = tmp_path / "c"; c.mkdir()
    ok = updater(write_cfg(c, components={"matter-controller": "1.6", "lock-firmware": "2.1"}, block=True))
    assert ok.apply(pkg, idx)["compat_warnings"] == []


def test_usb_media_route_through_the_cli(tmp_path, capsys):
    """R-DV-33: 網の無い現場で、持ち込んだ包みと表を命令行から当てる。確かめは同じ。"""
    cfg = write_cfg(tmp_path)
    media = tmp_path / "usb"; media.mkdir()
    pkg = make_pkg(media, "1.0.0")
    idx = make_index(media, [{"version": "1.0.0", "stage": "全棟"}])
    assert U.main(["--config", str(cfg), "apply", "--package", str(pkg), "--index", str(idx)]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "適用"
    bad = make_pkg(media, "1.1.0", key=OTHER)
    assert U.main(["--config", str(cfg), "apply", "--package", str(bad), "--index", str(idx)]) == 1
    assert "署名" in capsys.readouterr().err


def test_notice_and_log_chain(tmp_path):
    """R-DV-38: 事前の知らせに版、理由、内容が入る。更新の記録は前の行のハッシュでつながり、書き換えが分かる。"""
    u = updater(write_cfg(tmp_path))
    pkg = make_pkg(tmp_path, "1.0.0", reason="規格改定")
    idx = make_index(tmp_path, [{"version": "1.0.0", "stage": "全棟", "support_until": "2099-12-31"}])
    m, _ = u.open_package(pkg)
    text = U.notice(m, U._entry(json.loads(idx.read_text(encoding="utf-8")), "1.0.0"))
    assert "1.0.0" in text and "規格改定" in text and "1.0.0 の直し" in text and "2099-12-31" in text
    u.apply(pkg, idx)
    with pytest.raises(U.Refused):
        u.apply(make_pkg(tmp_path, "1.0.1"), idx)
    assert u.log.verify()[0] and len(u.log.rows()) == 2
    lines = u.log.path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("規格改定", "不具合")
    u.log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert u.log.verify()[0] is False


def test_support_period_and_fleet(tmp_path):
    """R-DV-40: サポートの終わりを過ぎた版で動いていることを status が示し、fleet が一覧にする。"""
    past = (date.today() - timedelta(days=1)).isoformat()
    u = updater(write_cfg(tmp_path))
    u.apply(make_pkg(tmp_path, "1.0.0"), make_index(tmp_path, [{"version": "1.0.0", "stage": "全棟",
                                                                 "support_until": past}]))
    s = u.status()
    assert s["version"] == "1.0.0" and s["out_of_support"] is True and s["support_until"] == past
    fl = tmp_path / "fleet"; fl.mkdir()
    (fl / "棟A.json").write_text(json.dumps(s), encoding="utf-8")
    (fl / "棟B.json").write_text(json.dumps({**s, "out_of_support": False}), encoding="utf-8")
    assert [r["server"] for r in U.fleet(fl)] == ["棟A"]
