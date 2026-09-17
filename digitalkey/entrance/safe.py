"""金庫 — 記録の置き場と控え。

記録は経営者の箱にある。控えは経営者の箱で暗号化してから、経営者名義の国内VPSへ上げる。
鍵(合言葉)は経営者が持つ。保存年限のある様式は年限を過ぎた伝票を候補として挙げるだけで、消すのは経営者。
"""
from __future__ import annotations

import shutil
import subprocess
import tarfile
from datetime import datetime, timedelta
from pathlib import Path

from .ledger import Ledger, Slip


def image_dir(root: Path | str) -> Path:
    p = Path(root) / "画像"
    p.mkdir(parents=True, exist_ok=True)
    return p


def store_image(root: Path | str, data: bytes) -> str:
    """画像を内容のハッシュ名で置き、ハッシュを返す(同じ画像は一つ)。"""
    import hashlib
    h = hashlib.sha256(data).hexdigest()
    p = image_dir(root) / f"{h}.bin"
    if not p.exists():
        p.write_bytes(data)
        p.chmod(0o440)
    return h


def make_archive(root: Path | str, out_dir: Path | str, stamp: str | None = None) -> Path:
    root, out_dir = Path(root), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    tar = out_dir / f"digitalkey-{stamp}.tar"
    with tarfile.open(tar, "w") as tf:
        tf.add(root, arcname=root.name)
    return tar


def encrypt(tar: Path, passphrase_file: Path | str) -> Path:
    """gpg の対称暗号(AES256)。合言葉のファイルは経営者の箱にだけある。"""
    if shutil.which("gpg") is None:
        raise RuntimeError("gpg がありません")
    out = tar.with_suffix(".tar.gpg")
    subprocess.run(["gpg", "--batch", "--yes", "--symmetric", "--cipher-algo", "AES256",
                    "--passphrase-file", str(passphrase_file), "-o", str(out), str(tar)], check=True)
    tar.unlink()
    return out


def push(encrypted: Path, remote: str) -> None:
    """rsync で経営者名義のVPSへ。remote は user@host:/path の形。"""
    subprocess.run(["rsync", "-a", "--chmod=F600", str(encrypted), remote], check=True)


def backup(root: Path | str, out_dir: Path | str, *, passphrase_file: Path | str | None = None,
           remote: str | None = None) -> Path:
    tar = make_archive(root, out_dir)
    if passphrase_file is None:
        return tar
    enc = encrypt(tar, passphrase_file)
    if remote:
        push(enc, remote)
    return enc


def retention_candidates(ledger: Ledger, today: datetime | None = None) -> list[Slip]:
    """保存年限(様式の :保存年限: 年)を過ぎた伝票。消すかどうかは経営者が決める。"""
    today = today or datetime.now().astimezone()
    out: list[Slip] = []
    for y in ledger.form_set.values():
        years = y.attrs.get("保存年限")
        if not years:
            continue
        limit = today - timedelta(days=365 * int(years))
        for d in ledger.select(y.name):
            if datetime.fromisoformat(d.issued_at) < limit:
                out.append(d)
    return out
