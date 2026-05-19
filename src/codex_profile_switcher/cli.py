"""codex-switch — manage Codex provider profiles.

Storage layout:

    ~/.codex/profiles/
      ├── .active                   # name of the currently-loaded profile
      └── <name>/
            ├── auth.json           # copied to ~/.codex/auth.json
            └── provider.toml       # merged into ~/.codex/config.toml
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional

from . import _swap


def profile_root() -> Path:
    return Path(os.environ.get("CODEX_PROFILE_ROOT") or Path.home() / ".codex" / "profiles")


def codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def active_file() -> Path:
    return profile_root() / ".active"


def active_name() -> Optional[str]:
    p = active_file()
    if not p.exists():
        return None
    name = p.read_text().strip()
    return name or None


def list_profiles() -> list[str]:
    root = profile_root()
    if not root.exists():
        return []
    return sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name != "bin"
    )


def _die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"codex-switch: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _atomic_write_copy(src: Path, dst: Path) -> None:
    """Replace dst with src's contents, preserving dst's mode when present."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=dst.name + ".", dir=dst.parent)
    os.close(fd)
    tmp = Path(tmp_str)
    try:
        shutil.copyfile(src, tmp)
        _copy_mode(dst if dst.exists() else None, tmp)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _copy_mode(ref: Optional[Path], target: Path) -> None:
    """Match target's mode to ref; fall back to 0600 for first-run."""
    if ref is not None and ref.exists():
        os.chmod(target, stat.S_IMODE(ref.stat().st_mode))
    else:
        os.chmod(target, 0o600)


def cmd_ls(_args) -> int:
    active = active_name()
    for name in list_profiles():
        prefix = "★" if name == active else " "
        print(f"{prefix} {name}")
    return 0


def cmd_current(_args) -> int:
    name = active_name()
    if name:
        print(name)
        return 0
    print("(none)")
    return 1


def cmd_use(args) -> int:
    name = args.name
    dir_ = profile_root() / name
    if not dir_.is_dir():
        _die(f"profile not found: {name}")
    auth_src = dir_ / "auth.json"
    prov_src = dir_ / "provider.toml"
    if not auth_src.is_file():
        _die(f"missing {auth_src}")
    if not prov_src.is_file():
        _die(f"missing {prov_src}")

    codex = codex_dir()
    codex.mkdir(parents=True, exist_ok=True)
    cfg = codex / "config.toml"
    auth = codex / "auth.json"

    if not cfg.exists():
        cfg.touch()

    # merge config.toml via a tempfile, then atomic replace
    fd, tmp_cfg_str = tempfile.mkstemp(prefix="config.toml.", dir=codex)
    os.close(fd)
    tmp_cfg = Path(tmp_cfg_str)
    try:
        _swap.merge(cfg, prov_src, tmp_cfg)
        _copy_mode(cfg, tmp_cfg)
        os.replace(tmp_cfg, cfg)
    finally:
        if tmp_cfg.exists():
            tmp_cfg.unlink(missing_ok=True)

    _atomic_write_copy(auth_src, auth)

    active_file().write_text(name + "\n")
    print(f"switched → {name}")
    return 0


def cmd_save(args) -> int:
    name = args.name
    if name in {"bin", ".active"} or name.startswith("."):
        _die(f"reserved name: {name}")
    dir_ = profile_root() / name
    dir_.mkdir(parents=True, exist_ok=True)

    codex = codex_dir()
    auth = codex / "auth.json"
    cfg = codex / "config.toml"
    if not auth.is_file():
        _die(f"no {auth} to snapshot")
    shutil.copy2(auth, dir_ / "auth.json")
    if cfg.is_file():
        _swap.extract(cfg, dir_ / "provider.toml")
    else:
        (dir_ / "provider.toml").write_text("")
    print(f"saved → {name}")
    return 0


def cmd_show(args) -> int:
    name = args.name
    dir_ = profile_root() / name
    if not dir_.is_dir():
        _die(f"profile not found: {name}")
    prov = dir_ / "provider.toml"
    print(f"# {prov}")
    if prov.exists():
        text = prov.read_text()
        print(text if text else "(empty)")
    else:
        print("(missing)")
    print()
    auth = dir_ / "auth.json"
    print(f"# {auth} (keys only)")
    if auth.exists():
        try:
            data = json.loads(auth.read_text())
            for k in sorted(data.keys()):
                print(k)
        except json.JSONDecodeError as e:
            print(f"(invalid json: {e})")
    else:
        print("(missing)")
    return 0


def cmd_rm(args) -> int:
    name = args.name
    dir_ = profile_root() / name
    if not dir_.is_dir():
        _die(f"profile not found: {name}")
    if name == active_name():
        _die(f"cannot remove the active profile: {name}")
    shutil.rmtree(dir_)
    print(f"removed → {name}")
    return 0


def cmd_alfred_list(_args) -> int:
    """Emit Alfred Script Filter JSON."""
    active = active_name()
    items = []
    for name in list_profiles():
        if name == active:
            items.append({
                "uid": name,
                "title": f"★ {name}",
                "arg": name,
                "subtitle": "current profile — pressing return reloads it",
                "autocomplete": name,
            })
        else:
            items.append({
                "uid": name,
                "title": name,
                "arg": name,
                "subtitle": f"switch to {name}",
                "autocomplete": name,
            })
    print(json.dumps({"items": items}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codex-switch",
        description="Switch between Codex provider profiles.",
    )
    subs = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    s = subs.add_parser("ls", aliases=["list"], help="list profiles (★ = active)")
    s.set_defaults(func=cmd_ls)

    s = subs.add_parser("current", help="print the active profile name")
    s.set_defaults(func=cmd_current)

    s = subs.add_parser("use", aliases=["switch"], help="load <name> into ~/.codex/")
    s.add_argument("name")
    s.set_defaults(func=cmd_use)

    s = subs.add_parser("save", help="snapshot the current ~/.codex state as <name>")
    s.add_argument("name")
    s.set_defaults(func=cmd_save)

    s = subs.add_parser("show", help="print <name>'s provider.toml + auth.json keys")
    s.add_argument("name")
    s.set_defaults(func=cmd_show)

    s = subs.add_parser("rm", aliases=["remove"], help="delete profile (active is protected)")
    s.add_argument("name")
    s.set_defaults(func=cmd_rm)

    s = subs.add_parser("alfred-list", help="JSON for Alfred Script Filter")
    s.set_defaults(func=cmd_alfred_list)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
