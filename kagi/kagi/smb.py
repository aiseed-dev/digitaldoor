"""Samba の設定（smb.conf）を読み、口と認証の形を見る。設計書 §3.2、鍵の管理 §5e。K-07。

値は出さない。`password` を名に含む行は「ある」とだけ出す。`include` は辿らない。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path

from kagi.inventory import Refused

GLOBAL = "global"
# 同じ物の別名（Samba の名。空白を抜き小文字にした形）
SYNONYMS = {
    "minprotocol": "serverminprotocol",
    "public": "guestok",
}
TRUE = {"yes", "true", "1", "on"}
SIGNING_OK = {"mandatory", "required"}
ROLES_OK = {"standalone", "standalone server", "active directory domain controller"}
_URL_USER = re.compile(r"://[^/@\s]*@")


@dataclass
class Conf:
    sections: dict[str, dict[str, str]] = field(default_factory=dict)  # 節 → 名 → 値
    password_lines: list[tuple[str, int]] = field(default_factory=list)  # （節、行）
    includes: list[tuple[str, int]] = field(default_factory=list)

    def get(self, section: str, key: str) -> str | None:
        return self.sections.get(section, {}).get(key)

    def shares(self) -> list[str]:
        return [s for s in self.sections if s != GLOBAL]

    def share_value(self, share: str, key: str) -> str | None:
        """共有に無ければ [global] の値（Samba の既定の継ぎ方）。"""
        v = self.get(share, key)
        return v if v is not None else self.get(GLOBAL, key)


@dataclass
class Finding:
    fix: bool  # 直す物か、見るだけか
    section: str
    what: str
    result: str


def _norm_key(key: str) -> str:
    k = re.sub(r"\s+", "", key).lower()
    return SYNONYMS.get(k, k)


def parse(text: str) -> Conf:
    conf = Conf()
    section = GLOBAL
    conf.sections[GLOBAL] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        start = i + 1
        line = lines[i].strip()
        i += 1
        while line.endswith("\\") and i < len(lines):  # 行の続き
            line = line[:-1] + " " + lines[i].strip()
            i += 1
        if not line or line[0] in ";#":
            continue
        if line.startswith("["):
            name = line[1:].split("]", 1)[0].strip().lower()
            section = name or section
            conf.sections.setdefault(section, {})
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        k = _norm_key(key)
        if "password" in k:
            conf.password_lines.append((section, start))
            continue  # 値は持たない
        if k == "include":
            conf.includes.append((section, start))
            continue
        conf.sections[section][k] = value.strip()
    return conf


def load(path: Path) -> Conf:
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError as e:
        raise Refused(f"smb.conf を読めない: {path} ({type(e).__name__})") from None
    if "\0" in text:
        raise Refused(f"smb.conf が素の文字でない: {path}")
    return parse(text)


def _is_true(v: str | None) -> bool:
    return v is not None and v.strip().lower() in TRUE


def _outward_interfaces(value: str) -> list[str]:
    """LAN の外を向く項目（0.0.0.0、公の番地）の数だけ返す。名（eth0 など）は LAN とみなす。"""
    bad = []
    for item in re.split(r"[\s,]+", value):
        if not item:
            continue
        addr = item.split(";", 1)[0]  # 192.0.2.10;speed=… の形
        try:
            net = ipaddress.ip_network(addr, strict=False)
        except ValueError:
            continue
        if net.prefixlen == 0 or not (net.is_private or net.is_loopback or net.is_link_local):
            bad.append(item)
    return bad


def _people_only(value: str) -> bool:
    """項目がみな個人名（@ + & で始まらず、%S などの置き換えでもない）か。"""
    items = [x for x in re.split(r"[\s,]+", value) if x]
    if not items:
        return False
    return all(not x.startswith(("@", "+", "&", "%")) for x in items)


def _location(v: str) -> str:
    return _URL_USER.sub("://…@", v)


def check(conf: Conf) -> list[Finding]:
    out: list[Finding] = []
    g = GLOBAL

    proto = conf.get(g, "serverminprotocol")
    if proto is None:
        out.append(Finding(True, g, "server min protocol", "SMB1 が通る（書いていない）"))
    elif not proto.upper().startswith(("SMB2", "SMB3")):
        out.append(Finding(True, g, "server min protocol", f"SMB1 が通る（{proto}）"))

    sign = conf.get(g, "serversigning")
    if sign is None or sign.lower() not in SIGNING_OK:
        out.append(Finding(True, g, "server signing", f"署名が任意（{sign or '書いていない'}）"))

    mtg = conf.get(g, "maptoguest")
    if mtg is not None and mtg.strip().lower() != "never":
        out.append(Finding(True, g, "map to guest", "ゲストが入れる（Never でない）"))
    if _is_true(conf.get(g, "guestok")):
        out.append(Finding(True, g, "guest ok / public", "ゲストが入れる（全部の共有の既定）"))
    for s in conf.shares():
        if _is_true(conf.get(s, "guestok")):
            out.append(Finding(True, s, "guest ok / public", "ゲストが入れる"))

    ifs = conf.get(g, "interfaces")
    bio = conf.get(g, "bindinterfacesonly")
    if not ifs or not _is_true(bio):
        why = "interfaces が無い" if not ifs else "bind interfaces only が yes でない"
        out.append(Finding(True, g, "interfaces / bind interfaces only", f"全部の口に聞く（{why}）"))
    elif bad := _outward_interfaces(ifs):
        out.append(
            Finding(True, g, "interfaces", f"LAN の外を向く項目が {len(bad)} 個（0.0.0.0 か公の番地）")
        )

    for s in conf.shares():
        for key, name in (("validusers", "valid users"), ("writelist", "write list")):
            v = conf.share_value(s, key)
            if v and _people_only(v):
                out.append(Finding(True, s, name, "個人で付けている（グループ @group で）"))

    role = conf.get(g, "serverrole")
    if role is None:
        out.append(Finding(False, g, "server role", "書いていない（既定は auto）"))
    else:
        note = (
            "" if role.lower() in ROLES_OK else "（standalone か active directory domain controller でない）"
        )
        out.append(Finding(False, g, "server role", role + note))
    for key, name in (("smbpasswdfile", "smb passwd file"), ("passdbbackend", "passdb backend")):
        v = conf.get(g, key)
        if v:
            out.append(Finding(False, g, name, f"場所: {_location(v)}"))
    for s, line in conf.password_lines:
        out.append(Finding(False, s, f"password を含む行（{line} 行目）", "ある（中身は出さない）"))
    for s, line in conf.includes:
        out.append(Finding(False, s, f"include（{line} 行目）", "辿っていない。その先は別に見る"))
    return out


def fixes(findings: list[Finding]) -> int:
    return sum(f.fix for f in findings)


def table(findings: list[Finding], path: Path | str) -> str:
    n = fixes(findings)
    head = f"smb.conf {path}: " + (f"直す物 {n} 件。" if n else "直す物は無い。")
    lines = [head, "", "| | 節 | 見る物 | 結果 |", "|---|---|---|---|"]
    for f in sorted(findings, key=lambda f: not f.fix):
        lines.append(f"| {'直す' if f.fix else '見るだけ'} | {f.section} | {f.what} | {f.result} |")
    return "\n".join(lines)
