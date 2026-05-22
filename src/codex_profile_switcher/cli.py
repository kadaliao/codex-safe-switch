"""codex-switch — manage Codex provider profiles.

Storage layout:

    ~/.codex/profiles/
      ├── .active                   # name of the currently-loaded profile
      ├── .official/                # reserved snapshot for official OpenAI login
      └── <name>/
            ├── auth.json           # copied to ~/.codex/auth.json
            ├── provider.toml       # merged into ~/.codex/config.toml
            └── session.toml        # optional session-state scope metadata
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
import shutil
import stat
import sqlite3
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

OFFICIAL_PROFILE_NAME = "official"
OFFICIAL_PROFILE_DIRNAME = ".official"
OFFICIAL_ALIASES = {OFFICIAL_PROFILE_NAME, "openai"}


@dataclass(frozen=True)
class SessionConfig:
    mode: str = "shared"
    scope: Optional[str] = None

    def describe(self) -> str:
        if self.mode == "scoped":
            return f"scoped ({self.scope})"
        return "shared"


@dataclass(frozen=True)
class IdentityConfig:
    provider: Optional[str]
    model: Optional[str]


@dataclass(frozen=True)
class MergeHistoryResult:
    changed_files: int
    changed_lines: int
    state_rows: int
    backup_dir: Optional[Path]


def profile_root() -> Path:
    return Path(os.environ.get("CODEX_PROFILE_ROOT") or Path.home() / ".codex" / "profiles")


def codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def active_file() -> Path:
    return profile_root() / ".active"


def official_profile_dir() -> Path:
    return profile_root() / OFFICIAL_PROFILE_DIRNAME


def session_state_root() -> Path:
    return profile_root() / ".session-state"


def active_name() -> Optional[str]:
    p = active_file()
    if not p.exists():
        return None
    name = p.read_text().strip()
    return name or None


def normalize_profile_name(name: Optional[str]) -> Optional[str]:
    if name is None:
        return None
    stripped = name.strip()
    if stripped in OFFICIAL_ALIASES or stripped == OFFICIAL_PROFILE_DIRNAME:
        return OFFICIAL_PROFILE_NAME
    return stripped or None


def profile_dir_for_name(name: str) -> Path:
    normalized = normalize_profile_name(name)
    if normalized == OFFICIAL_PROFILE_NAME:
        return official_profile_dir()
    return profile_root() / normalized


def current_identity() -> IdentityConfig:
    cfg = _swap.load(codex_dir() / "config.toml")
    provider = cfg.get("model_provider")
    model = cfg.get("model")
    provider = str(provider).strip() if provider is not None else None
    model = str(model).strip() if model is not None else None
    return IdentityConfig(provider=provider or None, model=model or None)


def list_profiles() -> list[str]:
    root = profile_root()
    if not root.exists():
        return [OFFICIAL_PROFILE_NAME] if official_profile_dir().is_dir() else []
    names = sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name != "bin"
    )
    if official_profile_dir().is_dir():
        return [OFFICIAL_PROFILE_NAME, *names]
    return names


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
    next_cfg = load_session_config(profile_dir_for_name(next_name))
    current_name = normalize_profile_name(current_name)
    if current_name:
        current_dir = profile_dir_for_name(current_name)
        if current_dir.is_dir():
            current_cfg = load_session_config(current_dir)
    if current_cfg == next_cfg:
        return
    save_session_state(current_cfg, codex)
    restore_session_state(next_cfg, codex)


def iter_rollout_files(codex: Path) -> list[Path]:
    files: list[Path] = []
    sessions = codex / "sessions"
    archived = codex / "archived_sessions"
    if sessions.exists():
        files.extend(sorted(p for p in sessions.rglob("*.jsonl") if p.is_file()))
    if archived.exists():
        files.extend(sorted(p for p in archived.glob("*.jsonl") if p.is_file()))
    return files


def backup_path(codex: Path, label: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return codex / f"{label}-{stamp}"


def backup_root(codex: Path, label: str) -> Path:
    root = backup_path(codex, label)
    root.mkdir(parents=True, exist_ok=True)
    return root


def backup_copy(src: Path, backup_dir: Path, codex: Path) -> None:
    if not src.exists():
        return
    rel = src.relative_to(codex)
    dst = backup_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def normalize_rollout_file(
    path: Path,
    target: IdentityConfig,
    keep_models: bool,
    *,
    apply: bool = True,
) -> tuple[int, bool]:
    lines_out: list[str] = []
    changed_lines = 0
    changed = False
    original_stat = path.stat()
    with path.open() as fh:
        for raw in fh:
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                lines_out.append(raw)
                continue

            item_changed = False
            payload = item.get("payload")
            if isinstance(payload, dict):
                if item.get("type") == "session_meta" and target.provider:
                    if payload.get("model_provider") != target.provider:
                        payload["model_provider"] = target.provider
                        item_changed = True
                elif item.get("type") == "turn_context":
                    if target.provider and payload.get("model_provider") != target.provider:
                        payload["model_provider"] = target.provider
                        item_changed = True
                    if not keep_models and target.model and payload.get("model") != target.model:
                        payload["model"] = target.model
                        item_changed = True

            if item_changed:
                changed = True
                changed_lines += 1
                lines_out.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            else:
                lines_out.append(raw)

    if changed and apply:
        fd, tmp_str = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        os.close(fd)
        tmp = Path(tmp_str)
        try:
            tmp.write_text("".join(lines_out))
            _copy_mode(path, tmp)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    return changed_lines, changed


def normalize_state_db(path: Path, target: IdentityConfig, keep_models: bool) -> int:
    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        if keep_models:
            if not target.provider:
                return 0
            count = cur.execute(
                "SELECT COUNT(*) FROM threads WHERE model_provider != ?",
                (target.provider,),
            ).fetchone()[0]
            cur.execute("UPDATE threads SET model_provider = ? WHERE model_provider != ?", (target.provider, target.provider))
        else:
            if not target.provider or not target.model:
                return 0
            count = cur.execute(
                "SELECT COUNT(*) FROM threads WHERE model_provider != ? OR ifnull(model, '') != ?",
                (target.provider, target.model),
            ).fetchone()[0]
            cur.execute(
                "UPDATE threads SET model_provider = ?, model = ? WHERE model_provider != ? OR ifnull(model, '') != ?",
                (target.provider, target.model, target.provider, target.model),
            )
        conn.commit()
        return int(count)
    finally:
        conn.close()


def connect_sqlite_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def count_state_rows_to_normalize(path: Path, target: IdentityConfig, keep_models: bool) -> int:
    if not path.exists():
        return 0
    conn = connect_sqlite_readonly(path)
    try:
        cur = conn.cursor()
        if keep_models:
            if not target.provider:
                return 0
            return int(
                cur.execute(
                    "SELECT COUNT(*) FROM threads WHERE model_provider != ?",
                    (target.provider,),
                ).fetchone()[0]
            )
        if not target.provider or not target.model:
            return 0
        return int(
            cur.execute(
                "SELECT COUNT(*) FROM threads WHERE model_provider != ? OR ifnull(model, '') != ?",
                (target.provider, target.model),
            ).fetchone()[0]
        )
    finally:
        conn.close()


def sqlite_threads_columns(conn: sqlite3.Connection) -> list[str]:
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'threads'"
    ).fetchone()
    if not found:
        return []
    return [str(row[1]) for row in conn.execute("PRAGMA table_info(threads)").fetchall()]


def _fmt_value(value) -> str:
    if value is None or value == "":
        return "(empty)"
    return str(value)


def read_thread_distribution(path: Path) -> tuple[list[tuple[str, str, int]], Optional[str]]:
    if not path.exists():
        return [], "missing state_5.sqlite"
    conn = connect_sqlite_readonly(path)
    try:
        columns = sqlite_threads_columns(conn)
        if not columns:
            return [], "missing threads table"
        if "model_provider" not in columns or "model" not in columns:
            return [], "threads table missing model_provider/model columns"
        rows = conn.execute(
            """
            SELECT ifnull(model_provider, ''), ifnull(model, ''), COUNT(*)
            FROM threads
            GROUP BY ifnull(model_provider, ''), ifnull(model, '')
            ORDER BY COUNT(*) DESC, ifnull(model_provider, ''), ifnull(model, '')
            """
        ).fetchall()
        return [(str(provider), str(model), int(count)) for provider, model, count in rows], None
    except sqlite3.Error as exc:
        return [], str(exc)
    finally:
        conn.close()


def read_recent_threads(path: Path, limit: int = 5) -> tuple[list[dict[str, object]], Optional[str]]:
    if not path.exists():
        return [], "missing state_5.sqlite"
    conn = connect_sqlite_readonly(path)
    try:
        columns = sqlite_threads_columns(conn)
        if not columns:
            return [], "missing threads table"
        selected = [c for c in ("id", "thread_id", "title", "model_provider", "model", "updated_at", "created_at") if c in columns]
        if not selected:
            return [], "threads table has no displayable columns"
        order_col = next((c for c in ("updated_at", "created_at") if c in columns), None)
        select_sql = ", ".join(f'"{c}"' for c in selected)
        order_sql = f' ORDER BY "{order_col}" DESC' if order_col else " ORDER BY rowid DESC"
        rows = conn.execute(f"SELECT rowid, {select_sql} FROM threads{order_sql} LIMIT ?", (limit,)).fetchall()
        recent = []
        for row in rows:
            item = {"rowid": row[0]}
            item.update({name: value for name, value in zip(selected, row[1:])})
            recent.append(item)
        return recent, None
    except sqlite3.Error as exc:
        return [], str(exc)
    finally:
        conn.close()


def has_provider_model_drift(
    distribution: list[tuple[str, str, int]],
    identity: IdentityConfig,
) -> Optional[bool]:
    if not distribution:
        return None
    if not identity.provider:
        return None
    for provider, model, _count in distribution:
        if provider != identity.provider:
            return True
        if identity.model and model != identity.model:
            return True
    return False


def plan_merge_history_to_target(codex: Path, target: IdentityConfig, keep_models: bool) -> MergeHistoryResult:
    rollout_files = iter_rollout_files(codex)
    state_db = codex / "state_5.sqlite"
    changed_files = 0
    changed_lines = 0

    for path in rollout_files:
        line_count, changed = normalize_rollout_file(path, target, keep_models, apply=False)
        if changed:
            changed_files += 1
            changed_lines += line_count

    state_rows = 0
    if state_db.exists():
        state_rows = count_state_rows_to_normalize(state_db, target, keep_models)

    backup_dir = backup_path(codex, "history-merge-backup") if changed_files or state_rows else None
    return MergeHistoryResult(changed_files, changed_lines, state_rows, backup_dir)


def merge_history_to_target(codex: Path, target: IdentityConfig, keep_models: bool) -> MergeHistoryResult:
    rollout_files = iter_rollout_files(codex)
    state_db = codex / "state_5.sqlite"
    backup_dir: Optional[Path] = None
    changed_files = 0
    changed_lines = 0

    for path in rollout_files:
        line_count, changed = normalize_rollout_file(path, target, keep_models, apply=False)
        if changed:
            if backup_dir is None:
                backup_dir = backup_root(codex, "history-merge-backup")
            backup_copy(path, backup_dir, codex)
            line_count, _ = normalize_rollout_file(path, target, keep_models)
            changed_files += 1
            changed_lines += line_count

    state_rows = 0
    if state_db.exists():
        preview_state_rows = count_state_rows_to_normalize(state_db, target, keep_models)
        if preview_state_rows:
            if backup_dir is None:
                backup_dir = backup_root(codex, "history-merge-backup")
            backup_copy(state_db, backup_dir, codex)
            for suffix in ("-shm", "-wal"):
                sidecar = codex / f"state_5.sqlite{suffix}"
                backup_copy(sidecar, backup_dir, codex)
            state_rows = normalize_state_db(state_db, target, keep_models)

    return MergeHistoryResult(changed_files, changed_lines, state_rows, backup_dir)


def maybe_auto_merge_history(codex: Path, *, keep_models: bool = False) -> None:
    target = current_identity()
    if not target.provider:
        return
    effective_keep_models = keep_models or not target.model
    result = merge_history_to_target(
        codex,
        target,
        effective_keep_models,
    )
    if result.changed_files or result.state_rows:
        mode = "provider-only" if effective_keep_models else "provider+model"
        model_desc = "(preserved)" if effective_keep_models else target.model
        print(
            f"history aligned → provider={target.provider} "
            f"model={model_desc} mode={mode} "
            f"files={result.changed_files} lines={result.changed_lines} state_rows={result.state_rows}"
        )
        if result.backup_dir is not None:
            print(f"history backup → {result.backup_dir}")


def current_looks_official(codex: Path) -> bool:
    cfg = _swap.load(codex / "config.toml")
    auth_path = codex / "auth.json"
    if not auth_path.exists():
        return False
    try:
        auth = json.loads(auth_path.read_text())
    except json.JSONDecodeError:
        return False
    provider = str(cfg.get("model_provider") or "").strip()
    auth_method = str(cfg.get("preferred_auth_method") or "").strip()
    auth_mode = str(auth.get("auth_mode") or "").strip()
    return provider == "openai" and auth_mode == "chatgpt" and auth_method in {"", "chatgpt"}


def snapshot_official_state(codex: Path) -> None:
    auth = codex / "auth.json"
    cfg = codex / "config.toml"
    if not auth.is_file():
        _die(f"no {auth} to snapshot")
    dir_ = official_profile_dir()
    dir_.mkdir(parents=True, exist_ok=True)
    shutil.copy2(auth, dir_ / "auth.json")
    if cfg.is_file():
        _swap.extract(cfg, dir_ / "provider.toml")
    else:
        (dir_ / "provider.toml").write_text("")
    write_session_config(dir_, SessionConfig())


def ensure_official_snapshot_available(codex: Path) -> None:
    if official_profile_dir().is_dir():
        auth = official_profile_dir() / "auth.json"
        prov = official_profile_dir() / "provider.toml"
        if auth.is_file() and prov.is_file():
            return
    if current_looks_official(codex):
        snapshot_official_state(codex)
        return
    _die("official OpenAI snapshot not found; switch to official once, then run `codex-switch official`")


def switch_to_profile(name: str) -> int:
    normalized_name = normalize_profile_name(name)
    dir_ = profile_dir_for_name(normalized_name)
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
    current = normalize_profile_name(active_name())

    if current_looks_official(codex) and normalized_name != OFFICIAL_PROFILE_NAME:
        snapshot_official_state(codex)

    if not cfg.exists():
        cfg.touch()

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
    maybe_switch_session_state(current, normalized_name, codex)

    active_file().write_text(normalized_name + "\n")
    print(f"switched → {normalized_name}")
    maybe_auto_merge_history(codex)
    return 0


def cmd_merge_history(args) -> int:
    codex = codex_dir()
    identity = current_identity()
    provider = args.provider or identity.provider
    model = identity.model if args.model is None else args.model

    if not provider:
        _die("target provider is empty; pass --provider or set model_provider in ~/.codex/config.toml")
    if not args.keep_models and not model:
        _die("target model is empty; pass --model or use --keep-models")

    target = IdentityConfig(provider=provider, model=model)
    if args.dry_run:
        result = plan_merge_history_to_target(codex, target, args.keep_models)
    else:
        result = merge_history_to_target(codex, target, args.keep_models)

    mode = "provider-only" if args.keep_models else "provider+model"
    model_desc = "(preserved)" if args.keep_models else target.model
    verb = "would merge history" if args.dry_run else "merged history"
    action = "would update" if args.dry_run else "updated"
    print(f"{verb} → provider={target.provider} model={model_desc} mode={mode}")
    if result.backup_dir is not None:
        suffix = " (would create)" if args.dry_run else ""
        print(f"backup → {result.backup_dir}{suffix}")
    else:
        print("backup → (not needed)")
    print(f"rollout files {action} → {result.changed_files}")
    print(f"rollout lines {action} → {result.changed_lines}")
    print(f"state rows {action} → {result.state_rows}")
    return 0


def cmd_doctor_history(_args) -> int:
    codex = codex_dir()
    identity = current_identity()
    profile = normalize_profile_name(active_name())
    if profile:
        profile_dir = profile_dir_for_name(profile)
        session_desc = load_session_config(profile_dir).describe() if profile_dir.is_dir() else "(profile missing)"
    else:
        session_desc = "(none)"

    print("history doctor")
    print(f"current profile → {profile or '(none)'}")
    print(f"current config → provider={identity.provider or '(empty)'} model={identity.model or '(empty)'}")
    print(f"session state → {session_desc}")

    state_db = codex / "state_5.sqlite"
    distribution, dist_error = read_thread_distribution(state_db)
    print("threads provider/model distribution:")
    if dist_error:
        print(f"  ({dist_error})")
    elif not distribution:
        print("  (empty)")
    else:
        for provider, model, count in distribution:
            print(f"  {count}  provider={_fmt_value(provider)} model={_fmt_value(model)}")

    recent, recent_error = read_recent_threads(state_db)
    print("recent threads:")
    if recent_error:
        print(f"  ({recent_error})")
    elif not recent:
        print("  (empty)")
    else:
        for item in recent:
            bits = [f"rowid={item.pop('rowid')}"]
            for key, value in item.items():
                bits.append(f"{key}={_fmt_value(value)}")
            print("  " + " ".join(bits))

    sqlite_drift = has_provider_model_drift(distribution, identity)
    if sqlite_drift is None:
        print("sqlite provider/model drift → unknown")
    else:
        print(f"sqlite provider/model drift → {'yes' if sqlite_drift else 'no'}")

    if identity.provider:
        effective_keep_models = not identity.model
        plan = plan_merge_history_to_target(codex, identity, effective_keep_models)
        rollout_drift = plan.changed_files > 0
        overall_drift = bool(sqlite_drift) or rollout_drift or plan.state_rows > 0
        print(
            "planned history alignment → "
            f"files={plan.changed_files} lines={plan.changed_lines} state_rows={plan.state_rows}"
        )
        print(f"rollout metadata drift → {'yes' if rollout_drift else 'no'}")
        print(f"provider/model drift → {'yes' if overall_drift else 'no'}")
    else:
        print("planned history alignment → unknown (empty provider)")
        print("rollout metadata drift → unknown")
        print("provider/model drift → unknown")
    return 0


def cmd_ls(_args) -> int:
    active = normalize_profile_name(active_name())
    for name in list_profiles():
        prefix = "★" if name == active else " "
        print(f"{prefix} {name}")
    return 0


def cmd_current(_args) -> int:
    name = normalize_profile_name(active_name())
    if name:
        print(name)
        return 0
    print("(none)")
    return 1


def cmd_use(args) -> int:
    name = normalize_profile_name(args.name)
    if not name:
        profiles = list_profiles()
        if not profiles:
            _die("no profiles yet — create one with `codex-switch save <name>`")
        chosen = pick(profiles, active=normalize_profile_name(active_name()), prompt="Switch to which profile?")
        if chosen is None:
            print("cancelled")
            return 1
        name = normalize_profile_name(chosen)
    if name == OFFICIAL_PROFILE_NAME:
        return cmd_official(args)
    return switch_to_profile(name)


def cmd_official(_args) -> int:
    codex = codex_dir()
    ensure_official_snapshot_available(codex)
    return switch_to_profile(OFFICIAL_PROFILE_NAME)


def cmd_save(args) -> int:
    name = normalize_profile_name(args.name)
    if name in {OFFICIAL_PROFILE_NAME, "bin", ".active"} or args.name.startswith("."):
        _die(f"reserved name: {args.name}")
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
    name = normalize_profile_name(args.name)
    dir_ = profile_dir_for_name(name)
    if not dir_.is_dir():
        _die(f"profile not found: {args.name}")
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
    name = normalize_profile_name(args.name)
    dir_ = profile_dir_for_name(name)
    if not dir_.is_dir():
        _die(f"profile not found: {args.name}")
    if name == normalize_profile_name(active_name()):
        _die(f"cannot remove the active profile: {args.name}")
    shutil.rmtree(dir_)
    print(f"removed → {name}")
    return 0


def cmd_state(args) -> int:
    name = normalize_profile_name(args.name)
    dir_ = profile_dir_for_name(name)
    if not dir_.is_dir():
        _die(f"profile not found: {args.name}")

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

    current = normalize_profile_name(active_name())
    if name == current or current is None:
        save_session_state(cfg, codex_dir())

    print(f"{name}: session state → {cfg.describe()}")
    return 0


def cmd_alfred_list(_args) -> int:
    """Emit Alfred Script Filter JSON."""
    active = normalize_profile_name(active_name())
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
    chosen = pick(profiles, active=normalize_profile_name(active_name()), prompt="Switch to which profile?")
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

    s = subs.add_parser("official", aliases=["openai"], help="switch back to official OpenAI ChatGPT login")
    s.set_defaults(func=cmd_official)

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

    s = subs.add_parser("merge-history", help="rewrite local Codex history metadata into one provider/model identity")
    s.add_argument("--provider", help="target provider; defaults to current ~/.codex/config.toml model_provider")
    s.add_argument("--model", help="target model; defaults to current ~/.codex/config.toml model")
    s.add_argument(
        "--keep-models",
        action="store_true",
        help="only normalize provider identity and keep existing per-thread model values",
    )
    s.add_argument("--dry-run", action="store_true", help="report planned history changes without writing files")
    s.set_defaults(func=cmd_merge_history)

    s = subs.add_parser("doctor-history", help="read-only diagnostics for Codex history provider/model state")
    s.set_defaults(func=cmd_doctor_history)

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
