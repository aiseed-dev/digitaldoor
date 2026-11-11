import datetime as dt
import json

from kagi import app, cli, tokens

TODAY = dt.date(2026, 11, 6)


def write(path, items):
    path.write_text(json.dumps({"tokens": items}, ensure_ascii=False))


def item(name, expires, **kw):
    return {
        "name": name,
        "app": "seed",
        "scope": ["buyer"],
        "sha256": "0" * 64,
        "created": "2026-10-01",
        "expires": expires,
        **kw,
    }


def test_rows_and_table(tmp_path, capsys):
    f = tmp_path / "tokens.json"
    write(
        f,
        [
            item("遠い人", "2027-06-01"),
            item("近い人", "2026-11-20"),
            item("切れた人", "2026-10-31"),
            item("やめた人", "2026-10-31", revoked="2026-10-15"),
            item("期限の無い人", ""),
        ],
    )
    rs = tokens.rows(tokens.load(f), 30, TODAY)
    assert [r["state"] for r in rs] == ["live", "soon", "expired", "revoked", "expired"]
    assert tokens.fixes(rs) == 2
    text = tokens.table(rs, f)
    assert "出した token 5 件（生きている 2）" in text and "0" * 64 not in text
    assert text.index("切れた人") < text.index("近い人") < text.index("遠い人") < text.index("やめた人")
    v = app.tokens_view(f, today=TODAY)
    assert v["fixes"] == 2 and ["近い人", "seed", "buyer", "期限が近い", "2026-11-20"] in v["rows"]
    assert "0" * 64 not in repr(v)
    assert cli.main(["check", "tokens", "--file", str(f)]) == 1
    assert "取り消すか出し直す" in capsys.readouterr().out


def test_refuses_plain_tokens_and_missing(tmp_path, capsys):
    f = tmp_path / "tokens.json"
    assert cli.main(["check", "tokens", "--file", str(f)]) == 2
    assert app.tokens_view(f)["error"]
    write(f, [{**item("x", "2027-01-01"), "token": "plain-not-a-real-one"}])
    assert cli.main(["check", "tokens", "--file", str(f)]) == 2
    out = capsys.readouterr()
    assert "ハッシュだけ" in out.err and "plain-not-a-real-one" not in out.err + out.out
    write(f, [item("ok", "2999-01-01")])
    assert cli.main(["check", "tokens", "--file", str(f)]) == 0
