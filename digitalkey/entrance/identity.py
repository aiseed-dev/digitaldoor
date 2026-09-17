"""本人確認 — 顔と身分証と場所と時刻を一組にして記録する。

要領の条件は二つ。A: 顔と旅券が画像で鮮明に確認できる。B: その画像が届出住宅かその近傍から発信されたと確認できる。
客のスマートフォンが身分証の文字を、ドアホンが場所と生の顔を受け持つ。位置情報と時刻差はBの裏づけ。
判定はAI社員でよい。ただし迷うものは「保留」にして人に回し、最後に人が判定し直した伝票を足す。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .ledger import Ledger, Slip
from .safe import store_image


@dataclass
class Evidence:
    """一回のチェックインで集まる証拠。"""

    selfie: bytes | None = None
    id_image: bytes | None = None
    doorbell: bytes | None = None
    lat: float | None = None
    lon: float | None = None
    submitted_at: datetime | None = None
    doorbell_at: datetime | None = None
    kiosk: str | None = None  # 届出住宅の中に据えた受付端末の識別子。登録済みなら B の裏づけになる


class Matcher(Protocol):
    def score(self, a: bytes, b: bytes) -> float: ...


@dataclass
class StubMatcher:
    """試験と配線確認用。同じバイト列なら 1.0、違えば default。本番は顔照合の実装に差し替える。"""

    default: float = 0.0

    def score(self, a: bytes, b: bytes) -> float:
        return 1.0 if a == b else self.default


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Identity:
    def __init__(self, ledger: Ledger, matcher: Matcher, *, house_lat: float, house_lon: float,
                 radius_m: float = 200.0, window_s: float = 600.0, approve_at: float = 0.8,
                 reject_below: float = 0.5, decider: str = "AI社員",
                 kiosks: frozenset[str] | set[str] = frozenset()) -> None:
        self.ledger = ledger
        self.matcher = matcher
        self.house = (house_lat, house_lon)
        self.radius_m = radius_m
        self.window_s = window_s
        self.approve_at = approve_at
        self.reject_below = reject_below
        self.decider = decider
        self.kiosks = frozenset(kiosks)

    def verify(self, *, subject: str, evidence: Evidence, reservation_id: str = "") -> Slip:
        reasons: list[str] = []
        hold = False
        if evidence.selfie is None:
            hold, _ = True, reasons.append("自撮りがない")
        if evidence.id_image is None:
            hold, _ = True, reasons.append("身分証がない")
        dist: float | None = None
        gap: float | None = None
        at_kiosk = evidence.kiosk is not None and evidence.kiosk in self.kiosks
        if evidence.kiosk is not None and not at_kiosk:
            hold, _ = True, reasons.append("登録のない受付端末")
        if at_kiosk:
            # 届出住宅の中に据えた端末で撮ったので、場所と時刻はその端末が裏づける
            dist, gap = 0.0, 0.0
        elif evidence.lat is not None and evidence.lon is not None:
            dist = distance_m(evidence.lat, evidence.lon, *self.house)
            if dist > self.radius_m:
                hold, _ = True, reasons.append(f"届出住宅から{int(dist)}m")
        else:
            hold, _ = True, reasons.append("位置情報がない")
        if at_kiosk:
            pass
        elif evidence.doorbell is None:
            hold, _ = True, reasons.append("ドアホンの映像がない")
        elif evidence.submitted_at and evidence.doorbell_at:
            gap = abs((evidence.submitted_at - evidence.doorbell_at).total_seconds())
            if gap > self.window_s:
                hold, _ = True, reasons.append(f"ドアホンとの時刻差{int(gap)}秒")
        score: float | None = None
        if evidence.selfie is not None and evidence.id_image is not None:
            scores = [self.matcher.score(evidence.selfie, evidence.id_image)]
            if evidence.doorbell is not None:
                scores.append(self.matcher.score(evidence.doorbell, evidence.selfie))
            score = min(scores)
        if score is not None and score < self.reject_below:
            decision = "拒否"
            reasons.append("顔が一致しない")
        elif hold or score is None or score < self.approve_at:
            decision = "保留"
            if score is not None and score < self.approve_at and not hold:
                reasons.append("一致の度合いが足りない")
        else:
            decision = "承認"
        root = self.ledger.root
        values = {
            "主体": subject,
            "予約番号": reservation_id,
            "自撮り": store_image(root, evidence.selfie) if evidence.selfie else "",
            "身分証": store_image(root, evidence.id_image) if evidence.id_image else "",
            "ドアホン": store_image(root, evidence.doorbell) if evidence.doorbell else "",
            "位置": (f"受付端末:{evidence.kiosk}" if at_kiosk
                   else f"{evidence.lat},{evidence.lon}" if dist is not None else ""),
            "距離m": str(int(dist)) if dist is not None else "",
            "時刻差s": str(int(gap)) if gap is not None else "",
            "一致度": f"{score:.3f}" if score is not None else "",
            "判定": decision,
            "判定者": self.decider,
            "理由": "、".join(reasons),
        }
        basis = [reservation_id] if reservation_id else []
        return self.ledger.append("本人確認", values, issuer=self.decider, to=subject, basis=basis)

    def decide(self, verification_id: str, decision: str, *, by: str, reason: str = "",
               issuer: str | None = None) -> Slip:
        """人が判定し直す。前の伝票を根拠にして新しい伝票を足す(前の伝票は変えない)。

        by は判定者の名前、issuer は伝票の発行者(既定は by。門から呼ぶときは経営者の符号を確かめたうえで「経営者」)。
        """
        prev = self.ledger.get(verification_id)
        if prev.form != "本人確認":
            raise ValueError("本人確認の伝票ではありません")
        values = dict(prev.values)
        values.update({"判定": decision, "判定者": by, "理由": reason or f"{prev['判定']}を人が見直した"})
        return self.ledger.append("本人確認", values, issuer=issuer or by, to=prev.to, basis=[verification_id])

    def latest(self, subject: str) -> Slip | None:
        rows = self.ledger.select("本人確認", where={"主体": subject})
        return rows[-1] if rows else None
