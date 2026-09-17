#!/usr/bin/env python3
"""Initialize a sample static site in the target directory.

Copies the default scaffold (articles, html, tools/templates, CLAUDE.md,
README.md) from tools/scaffolds/default/ into the target directory so that a
fresh site is immediately buildable with:

    python3 tools/build_article.py --site <target> --all
    python3 tools/serve.py --site <target>

Usage:
    python3 tools/init_site.py /path/to/new-site
    python3 tools/init_site.py .                     # current dir
    python3 tools/init_site.py --force <existing>    # overwrite existing files
    python3 tools/init_site.py --list                # show what would be copied
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent
SCAFFOLD_ROOT = TOOLS_ROOT / "scaffolds"
DEFAULT_SCAFFOLD = "default"

# 新しいサイトに一緒に置くビルドエンジン。**サイトは自分のエンジンを持つ**
# (パーサの正はサイト側に一つ)。これが無いと、作ったサイトは自分だけでは
# ビルドできず、aiseed-builder からも開けない(tools/build/series.py を見る)。
ENGINE_FILES = [
    "build_article.py",
    "serve.py",
    "cloudflare_pages_deploy.py",
    "init_site.py",          # 作ったサイトから、さらに新しいサイトを作れる
    "build/__init__.py",
    "build/config.py",
    "build/frontmatter.py",
    "build/series.py",
    "build/markdown.py",
    "build/images.py",
    "build/template_vars.py",
]


def available_scaffolds() -> list[str]:
    if not SCAFFOLD_ROOT.exists():
        return []
    return sorted(p.name for p in SCAFFOLD_ROOT.iterdir() if p.is_dir())


def iter_scaffold_files(scaffold_dir: Path):
    """Yield (source, relative) for each file under scaffold_dir."""
    for src in scaffold_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(scaffold_dir)
        yield src, rel


def iter_engine_files():
    """(source, relative) for the build engine that ships with a new site."""
    for rel in ENGINE_FILES:
        src = TOOLS_ROOT / rel
        if src.is_file():
            yield src, Path("tools") / rel


def init_site(target: Path, scaffold: str = DEFAULT_SCAFFOLD, *, force: bool = False,
              dry_run: bool = False, with_engine: bool = True) -> int:
    """Copy scaffold files into target. Returns number of files written."""
    scaffold_dir = SCAFFOLD_ROOT / scaffold
    if not scaffold_dir.is_dir():
        choices = ", ".join(available_scaffolds()) or "(none)"
        raise SystemExit(
            f"Unknown scaffold '{scaffold}'. Available: {choices}"
        )

    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped: list[Path] = []
    sources = list(iter_scaffold_files(scaffold_dir))
    if with_engine:
        sources += list(iter_engine_files())
    for src, rel in sources:
        dest = target / rel
        if dest.exists() and not force:
            skipped.append(rel)
            continue
        if dry_run:
            print(f"  would write {rel}")
            written += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        print(f"  + {rel}")
        written += 1

    if skipped:
        print(
            f"\n{len(skipped)} existing file(s) skipped (re-run with --force to overwrite):",
            file=sys.stderr,
        )
        for rel in skipped:
            print(f"  · {rel}", file=sys.stderr)

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", help="Target directory (pass '.' for cwd).")
    parser.add_argument("--scaffold", default=DEFAULT_SCAFFOLD,
                        help=f"Scaffold template name (default: {DEFAULT_SCAFFOLD}).")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite files that already exist in the target.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be written without touching the filesystem.")
    parser.add_argument("--list", action="store_true",
                        help="List available scaffolds and exit.")
    parser.add_argument("--no-engine", action="store_true",
                        help="Do not copy the build engine into the new site "
                             "(the site will need --site to build).")
    args = parser.parse_args()

    if args.list:
        for name in available_scaffolds():
            print(name)
        return

    if not args.target:
        parser.error("target directory is required (pass '.' for cwd)")

    target = Path(args.target)
    print(f"Initializing site in {target.resolve()} (scaffold: {args.scaffold})")
    count = init_site(target, scaffold=args.scaffold, force=args.force,
                      dry_run=args.dry_run, with_engine=not args.no_engine)
    verb = "would write" if args.dry_run else "wrote"
    print(f"\n{verb} {count} file(s).")
    if not args.dry_run and count:
        engine = "" if args.no_engine else " (engine included)"
        print(
            f"\nNext steps{engine}:\n"
            "  pip install -r requirements.txt   # jinja2 markdown-it-py "
            "mdit-py-cjk-friendly pyasciidoc pywashi Pillow watchdog\n"
            f"  cd {target}\n"
            "  python3 tools/build_article.py --all\n"
            "  python3 tools/serve.py\n"
            "\nOr open it in aiseed-builder (a WordPress-like admin)."
        )


if __name__ == "__main__":
    main()
