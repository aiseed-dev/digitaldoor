"""ニュースを集める道具 tools/collect_news.py の、AI で整える段(refine、refine_all、main)の試験。

API は呼ばない。疑似のクライアント(beta.messages.create を持つ物)を渡す。網にも出ない(--retry で回す)。
サイトの道具なので、要求仕様(R-XX-nn)の番号は付けない(tests/publish と同じ扱い)。
"""
import importlib.util
import json
import sys
import typing
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("collect_news", ROOT / "tools" / "collect_news.py")
cn = importlib.util.module_from_spec(SPEC)
sys.modules["collect_news"] = cn  # dataclass が自分のモジュールを引くため
SPEC.loader.exec_module(cn)


def raw_item(n: int) -> dict:
    return {"id": f"id{n:03d}", "link": f"https://example.com/{n}", "source": "例の取り口", "publisher": "",
            "lang": "ja", "published": f"2026-11-{(n % 28) + 1:02d}T00:00:00Z", "title_raw": f"題 {n}",
            "summary_raw": f"要約 {n}", "collected": "2026-11-11T00:00:00Z", "status": "raw", "picked": False}


def answer(batch_ids, skip=()):
    items = [{"id": i, "relevant": True, "title": f"整えた題 {i}", "summary": "要約", "category": "錠", "kind": "報道"}
             for i in batch_ids if i not in skip]
    return SimpleNamespace(stop_reason="end_turn", stop_details=None,
                           content=[SimpleNamespace(type="text", text=json.dumps({"items": items}, ensure_ascii=False))])


class FakeClient:
    """疑似のクライアント。behaviors は束ごとの振る舞い("ok"、"refusal"、例外、関数)。"""

    def __init__(self, behaviors=None, api_key="sk-test-not-real"):
        self.api_key = api_key
        self.auth_token = None
        self.credentials = None
        self.calls = []
        self.behaviors = list(behaviors or [])
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        ids = [o["id"] for o in json.loads(kw["messages"][0]["content"].split("\n\n", 1)[1])]
        b = self.behaviors.pop(0) if self.behaviors else "ok"
        if isinstance(b, Exception):
            raise b
        if b == "refusal":
            return SimpleNamespace(stop_reason="refusal", stop_details="cyber", content=[])
        if callable(b):
            return b(ids)
        return answer(ids)


def api_connection_error():
    import anthropic
    import httpx2
    return anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


# ---------------------------------------------------------------- refine の呼び方を SDK と照らす
def _literals(tp) -> set:
    out = set()
    for a in typing.get_args(tp):
        if isinstance(a, str):
            out.add(a)
        else:
            out |= _literals(a)
    return out


def test_refine_call_matches_installed_sdk():
    """refine が beta.messages.create に渡す引数が、入っている anthropic の SDK の引数と型に合うこと。"""
    import inspect

    import anthropic
    from anthropic.resources.beta.messages.messages import Messages
    from anthropic.types.anthropic_beta_param import AnthropicBetaParam
    from anthropic.types.beta.beta_fallbacks_param import BetaFallbacksParam
    from anthropic.types.beta.beta_json_output_format_param import BetaJSONOutputFormatParam
    from anthropic.types.beta.beta_output_config_param import BetaOutputConfigParam
    from anthropic.types.model_param import ModelParam

    client = FakeClient()
    cn.refine([raw_item(1)], client)
    kw = client.calls[0]
    params = inspect.signature(Messages.create).parameters
    assert set(kw) <= set(params), set(kw) - set(params)
    assert kw["model"] in _literals(ModelParam)
    assert set(kw["betas"]) <= _literals(AnthropicBetaParam)
    assert kw["fallbacks"] in _literals(BetaFallbacksParam)
    config_hints = typing.get_type_hints(BetaOutputConfigParam)
    assert set(kw["output_config"]) <= set(config_hints)
    assert kw["output_config"]["effort"] in _literals(config_hints["effort"])
    assert set(kw["output_config"]["format"]) == set(BetaJSONOutputFormatParam.__annotations__)
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert anthropic.__version__


def test_has_credentials_reads_sdk_client(monkeypatch, tmp_path):
    """SDK の client が資格情報を見つけたかを、api_key / auth_token / credentials で見分けること。"""
    import anthropic

    for name in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_CONFIG_DIR",
                 "ANTHROPIC_IDENTITY_TOKEN", "ANTHROPIC_IDENTITY_TOKEN_FILE", "ANTHROPIC_FEDERATION_RULE_ID",
                 "ANTHROPIC_ORGANIZATION_ID"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cn.has_credentials(anthropic.Anthropic()) is False
    assert cn.has_credentials(anthropic.Anthropic(api_key="sk-test-not-real")) is True


# ---------------------------------------------------------------- refine_all
def test_all_batches_fail_counts_reasons():
    """資格情報があるのに全部の束が落ちたら、all_failed になり、件数と理由を数えること。"""
    items = [raw_item(n) for n in range(20)]
    res = cn.refine_all(items, 40, batch_size=15, client=FakeClient([RuntimeError("壊れた返事"), "refusal"]), pause=0)
    assert (res.asked, res.done, res.failed, res.skipped) == (20, 0, 20, 0)
    assert res.all_failed
    assert res.reasons["RuntimeError: 壊れた返事"] == 15
    assert any("AI が断った" in r for r in res.reasons)
    assert all(i["status"] == "raw" for i in items)
    text = "\n".join(res.summary())
    assert "失敗 20 件" in text and "壊れた返事" in text


def test_api_error_stops_and_counts_the_rest():
    """API や網の失敗では止め、回さなかった件数を数えること。"""
    items = [raw_item(n) for n in range(40)]
    res = cn.refine_all(items, 40, batch_size=15, client=FakeClient(["ok", api_connection_error()]), pause=0)
    assert (res.done, res.failed, res.skipped) == (15, 15, 10)
    assert not res.all_failed
    assert sum(i["status"] == "ai" for i in items) == 15
    assert "回さなかった物 10 件" in res.summary()[0]


def test_missing_items_in_answer_are_failures():
    """AI の返事に無い件は失敗として数え、raw のまま残すこと。"""
    items = [raw_item(n) for n in range(3)]
    client = FakeClient([lambda ids: answer(ids, skip={"id001"})])
    res = cn.refine_all(items, 40, client=client, pause=0)
    assert (res.done, res.failed) == (2, 1)
    assert [i["status"] for i in items] == ["ai", "raw", "ai"]
    assert items[0]["title"] == "整えた題 id000" and items[0]["kind"] == "報道"


def test_no_credentials_keeps_titles():
    """資格情報が無ければ AI を呼ばず、題のまま残し、all_failed にしないこと。"""
    items = [raw_item(n) for n in range(3)]
    client = FakeClient(api_key=None)
    res = cn.refine_all(items, 40, client=client, pause=0)
    assert res.no_credentials and not res.all_failed and client.calls == []
    assert "資格情報がありません" in res.summary()[0]


# ---------------------------------------------------------------- main の終了コード
@pytest.fixture
def items_file(tmp_path, monkeypatch):
    path = tmp_path / "items.json"
    path.write_text(json.dumps([raw_item(n) for n in range(5)], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cn, "ITEMS", path)
    monkeypatch.setattr(cn.time, "sleep", lambda s: None)
    return path


def test_main_exits_nonzero_when_nothing_refined(items_file, monkeypatch, capsys):
    """資格情報があるのに一件も整わなければ 0 以外で終わり、集めた物は書き、理由を出すこと。"""
    monkeypatch.setattr(cn, "make_client", lambda: FakeClient([RuntimeError("Error code: 401 - invalid x-api-key")]))
    assert cn.main(["--retry"]) == 1
    out = capsys.readouterr()
    assert "AI で整えた物 0 件" in out.out and "invalid x-api-key" in out.out
    assert "エラー" in out.err
    assert len(json.loads(items_file.read_text(encoding="utf-8"))) == 5


def test_main_exits_zero_without_credentials(items_file, monkeypatch, capsys):
    """資格情報が無いときは、今までどおり題のまま書いて 0 で終わること。"""
    monkeypatch.setattr(cn, "make_client", lambda: FakeClient(api_key=None))
    assert cn.main(["--retry"]) == 0
    assert "資格情報がありません" in capsys.readouterr().out
    assert all(i["status"] == "raw" for i in json.loads(items_file.read_text(encoding="utf-8")))


def test_main_exits_zero_when_some_refined(items_file, monkeypatch, capsys):
    """一件でも整えば 0 で終わり、整えた物を書くこと。"""
    monkeypatch.setattr(cn, "make_client", lambda: FakeClient())
    assert cn.main(["--retry", "--max", "3"]) == 0
    data = json.loads(items_file.read_text(encoding="utf-8"))
    assert sum(i["status"] == "ai" for i in data) == 3
    assert "AI で整えた物 3 件" in capsys.readouterr().out


def test_main_no_ai_and_nothing_to_do(items_file, monkeypatch):
    """--no-ai と、回す物が無いときは、AI を呼ばずに 0 で終わること。"""
    monkeypatch.setattr(cn, "make_client", lambda: pytest.fail("呼ばない"))
    assert cn.main(["--retry", "--no-ai"]) == 0
    assert cn.main(["--retry", "--max", "0"]) == 0
