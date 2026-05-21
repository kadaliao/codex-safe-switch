"""codex-switch — manage Codex provider profiles.

Storage layout:

    ~/.codex/profiles/
      ├── .active                   # name of the currently-loaded profile
      └── <name>/
            ├── auth.json           # copied to ~/.codex/auth.json
            └── provider.toml       # merged into ~/.codex/config.toml
            └── session.toml        # optional session-state scope metadata
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional

import tomlkit

from . import _swap
from .picker import pick


SESSION_STATE_FILES = (
    "history.jsonl",
    "session_index.jsonl",
    "state_5.sqlite",
    "state_5.sqlite-shm",
    "state_5.sqlite-wal",
)


@dataclass(frozen=True)
class SessionConfig:
    mode: str = "shared"
    scope: Optional[str] = None

    def describe(self) -> str:
        if self.mode == "scoped":
            return f"scoped ({self.scope})"
        return "shared"


def profile_root() -> Path:
    return Path(os.environ.get("CODEX_PROFILE_ROOT") or Path.home() / ".codex" / "profiles")


def codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def active_file() -> Path:
    return profile_root() / ".active"


def session_state_root() -> Path:
    return profile_root() / ".session-state"


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


def session_config_path(profile_dir: Path) -> Path:
    return profile_dir / "session.toml"


def load_session_config(profile_dir: Path) -> SessionConfig:
    path = session_config_path(profile_dir)
    if not path.exists() or path.stat().st_size == 0:
        return SessionConfig()
    try:
        doc = tomlkit.parse(path.read_text())
    except Exception as exc:  # pragma: no cover - defensive config parsing
        _die(f"invalid {path}: {exc}")
    mode = str(doc.get("mode", "shared")).strip() or "shared"
    if mode not in {"shared", "scoped"}:
        _die(f"invalid {path}: mode must be 'shared' or 'scoped'")
    scope = doc.get("scope")
    scope = str(scope).strip() if scope is not None else None
    if mode == "scoped" and not scope:
        _die(f"invalid {path}: scoped mode requires a non-empty scope")
    return SessionConfig(mode=mode, scope=scope)


def write_session_config(profile_dir: Path, config: SessionConfig) -> None:
    path = session_config_path(profile_dir)
    if config.mode == "shared":
        path.unlink(missing_ok=True)
        return
    doc = tomlkit.document()
    doc["mode"] = config.mode
    doc["scope"] = config.scope
    path.write_text(tomlkit.dumps(doc))


def session_scope_dir(scope: str) -> Path:
    return session_state_root() / scope


def _copy_optional(src: Path, dst: Path) -> None:
    if src.exists():
        _atomic_write_copy(src, dst)
    else:
        dst.unlink(missing_ok=True)


def save_session_state(config: SessionConfig, codex: Path) -> None:
    if config.mode != "scoped" or not config.scope:
        return
    scope_dir = session_scope_dir(config.scope)
    scope_dir.mkdir(parents=True, exist_ok=True)
    for rel in SESSION_STATE_FILES:
        src = codex / rel
        dst = scope_dir / rel
        _copy_optional(src, dst)


def clear_session_state(codex: Path) -> None:
    for rel in SESSION_STATE_FILES:
        (codex / rel).unlink(missing_ok=True)


def restore_session_state(config: SessionConfig, codex: Path) -> None:
    if config.mode != "scoped" or not config.scope:
        return
    scope_dir = session_scope_dir(config.scope)
    if not scope_dir.exists():
        clear_session_state(codex)
        return
    for rel in SESSION_STATE_FILES:
        src = scope_dir / rel
        dst = codex / rel
        _copy_optional(src, dst)


def maybe_switch_session_state(current_name: Optional[str], next_name: str, codex: Path) -> None:
    current_cfg = SessionConfig()
    next_cfg = load_session_config(profile_root() / next_name)
    if current_name:
        current_dir = profile_root() / current_name
        if current_dir.is_dir():
            current_cfg = load_session_config(current_dir)
    if current_cfg == next_cfg:
        return
    save_session_state(current_cfg, codex)
    restore_session_state(next_cfg, codex)


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
    if not name:
        profiles = list_profiles()
        if not profiles:
            _die("no profiles to choose from")
        chosen = pick(profiles, active=active_name(), prompt="Switch to which profile?")
        if chosen is None:
            print("cancelled")
            return 1
        name = chosen
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
    current = active_name()

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
    maybe_switch_session_state(current, name, codex)

    active_file().write_text(name + "\n")
    print(f"switched → {name}")
    return 0


def cmd_save(args) -> int:
    name = args.name
    if name in {"bin", ".active"} or name.startswith("."):
        _die(f"reserved name: {name}")
    if args.shared and args.scope:
        _die("choose either --shared or --scope, not both")
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

    if args.shared:
        session_cfg = SessionConfig()
        write_session_config(dir_, session_cfg)
    elif args.scope:
        scope = args.scope.strip()
        if not scope:
            _die("scope must not be empty")
        session_cfg = SessionConfig(mode="scoped", scope=scope)
        write_session_config(dir_, session_cfg)
        save_session_state(session_cfg, codex)

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
    print()
    print(f"# {session_config_path(dir_)}")
    session_cfg = load_session_config(dir_)
    print(session_cfg.describe())
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


def cmd_state(args) -> int:
    name = args.name
    dir_ = profile_root() / name
    if not dir_.is_dir():
        _die(f"profile not found: {name}")

    if args.shared and args.scope:
        _die("choose either --shared or --scope, not both")

    if not args.shared and not args.scope:
        print(load_session_config(dir_).describe())
        return 0

    if args.shared:
        cfg = SessionConfig()
    else:
        scope = args.scope.strip()
        if not scope:
            _die("scope must not be empty")
        cfg = SessionConfig(mode="scoped", scope=scope)

    write_session_config(dir_, cfg)

    current = active_name()
    if name == current or current is None:
        save_session_state(cfg, codex_dir())

    print(f"{name}: session state → {cfg.describe()}")
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


def cmd_pick(_args) -> int:
    """Default action when no subcommand is given: interactive switch."""
    profiles = list_profiles()
    if not profiles:
        _die("no profiles yet — create one with `codex-switch save <name>`")
    chosen = pick(profiles, active=active_name(), prompt="Switch to which profile?")
    if chosen is None:
        print("cancelled")
        return 1
    args_ns = argparse.Namespace(name=chosen)
    return cmd_use(args_ns)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codex-switch",
        description="Switch between Codex provider profiles. Run with no subcommand for an interactive picker.",
    )
    subs = p.add_subparsers(dest="cmd", metavar="<command>")

    s = subs.add_parser("ls", aliases=["list"], help="list profiles (★ = active)")
    s.set_defaults(func=cmd_ls)

    s = subs.add_parser("current", help="print the active profile name")
    s.set_defaults(func=cmd_current)

    s = subs.add_parser("use", aliases=["switch"], help="load <name> (interactive if omitted)")
    s.add_argument("name", nargs="?")
    s.set_defaults(func=cmd_use)

    s = subs.add_parser("save", help="snapshot the current ~/.codex state as <name>")
    s.add_argument("name")
    s.add_argument("--scope", help="also bind this profile to a session-state scope and seed it now")
    s.add_argument("--shared", action="store_true", help="clear any session-state scope while saving")
    s.set_defaults(func=cmd_save)

    s = subs.add_parser("show", help="print <name>'s provider.toml + auth.json keys")
    s.add_argument("name")
    s.set_defaults(func=cmd_show)

    s = subs.add_parser("state", help="show or set <name>'s session-state scope")
    s.add_argument("name")
    s.add_argument("--scope", help="store/restore Codex history state under this shared scope")
    s.add_argument("--shared", action="store_true", help="disable session-state swapping for this profile")
    s.set_defaults(func=cmd_state)

    s = subs.add_parser("rm", aliases=["remove"], help="delete profile (active is protected)")
    s.add_argument("name")
    s.set_defaults(func=cmd_rm)

    s = subs.add_parser("alfred-list", help="JSON for Alfred Script Filter")
    s.set_defaults(func=cmd_alfred_list)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        return cmd_pick(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
