from __future__ import annotations

import argparse

from .core.store import JsonStore


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="laoban")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化公司目录")
    init.add_argument("--root", default=".laoban", help="数据目录（默认 .laoban）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "init":
        JsonStore(args.root)
        print(f"已初始化公司目录：{args.root}")
        return 0
    return 1
