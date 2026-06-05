"""codex-safe-switch — manage Codex provider profiles.

Storage layout:

    ~/.codex/profiles/
      ├── .active                   # name of the currently-loaded profile
      ├── .official/                # reserved snapshot for official OpenAI login
      └── <name>/
            ├── provider.toml       # merged into ~/.codex/config.toml
            └── session.toml        # optional session-state scope metadata
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import signal
import shutil
import stat
import sqlite3
import subprocess
import sys
import tempfile
import time
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
ALFRED_INIT_ARG = "__init__"
REMOTE_CONTROL_INSTALL_COMMAND = "curl -fsSL https://chatgpt.com/codex/install.sh | sh"
REMOTE_CONTROL_REPAIR_ENV = "CODEX_SWITCH_REMOTE_REPAIR"
CLI_SURFACE_CHECK_ENV = "CODEX_SWITCH_CLI_SURFACE_CHECK"
DESKTOP_CODEX_PATH_ENV = "CODEX_DESKTOP_CODEX_PATH"
REMOTE_PROXY_RESPAWN_GRACE_SECONDS = 1.5
GLOBAL_STATE_FILES = (".codex-global-state.json", ".codex-global-state.json.bak")
REMOTE_HOST_PREFIX = "remote-ssh-"
ELECTRON_REMOTE_STATE_KEYS = (
    "electron-local-remote-control-environment-id",
    "electron-local-remote-control-installation-id",
)


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


@dataclass(frozen=True)
class CodexCliSurface:
    label: str
    version: str
    path: Optional[Path]


@dataclass(frozen=True)
class SessionIndexThreadState:
    latest_ms: int = 0
    latest_name: Optional[str] = None
    best_name: Optional[str] = None
    best_name_ms: int = 0


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


def _safe_profile_name(raw: Optional[str]) -> str:
    name = (raw or "current").strip().lower()
    chars = [ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in name]
    safe = "".join(chars).strip(".-_") or "current"
    if normalize_profile_name(safe) == OFFICIAL_PROFILE_NAME:
        return "openai-api"
    return safe


def bootstrap_current_profile(*, verbose: bool = True) -> Optional[str]:
    """Import the current Codex config on first run, if no profiles exist yet."""
    if list_profiles():
        return None

    codex = codex_dir()
    cfg = codex / "config.toml"
    if not cfg.is_file():
        return None

    if current_provider_looks_official(codex):
        snapshot_official_state(codex)
        name = OFFICIAL_PROFILE_NAME
    else:
        identity = current_identity()
        name = _safe_profile_name(identity.provider)
        dir_ = profile_root() / name
        dir_.mkdir(parents=True, exist_ok=True)
        if cfg.is_file():
            _swap.extract(cfg, dir_ / "provider.toml")
        else:
            (dir_ / "provider.toml").write_text("")
        write_session_config(dir_, SessionConfig())

    active_file().parent.mkdir(parents=True, exist_ok=True)
    active_file().write_text(name + "\n")
    if verbose:
        print(f"initialized profile from existing Codex config → {name}")
    return name


def no_profiles_message() -> str:
    return (
        "no profiles yet — configure Codex once, then run `codex-safe-switch` to import it, "
        "or create one with `codex-safe-switch save <name>`"
    )


def _die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"codex-safe-switch: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _ps_output() -> str:
    result = subprocess.run(
        ["ps", "-axo", "pid=,args="],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.stdout


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _looks_like_codex_process(pid: int, args: str) -> bool:
    if pid == os.getpid():
        return False
    lower = args.lower()
    if "codex-safe-switch" in lower or "codex_safe_switch" in lower:
        return False
    if _looks_like_remote_proxy_process(pid, args):
        return True
    executable = Path(args.split(maxsplit=1)[0]).name.lower() if args.strip() else ""
    if executable in {"codex", "codex-app-server", "codex-server"}:
        return True
    return (
        "/codex.app/contents/" in lower
        or " codex app-server" in lower
        or " codex-app-server" in lower
        or " codex server" in lower
    )


def find_codex_processes() -> list[int]:
    pids: list[int] = []
    for line in _ps_output().splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if _looks_like_codex_process(pid, parts[1]):
            pids.append(pid)
    return pids


def restart_codex_processes() -> int:
    pids = find_codex_processes()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and any(_pid_exists(pid) for pid in pids):
        time.sleep(0.05)

    for pid in pids:
        if not _pid_exists(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return len(pids)


def _looks_like_unmanaged_app_server(pid: int, args: str) -> bool:
    if pid == os.getpid():
        return False
    lower = args.lower()
    if "codex-safe-switch" in lower or "codex_safe_switch" in lower:
        return False
    if "--listen unix://" not in lower:
        return False
    parts = args.split(maxsplit=1)
    executable = Path(parts[0]).name.lower() if parts else ""
    rest = parts[1].lower() if len(parts) == 2 else ""
    return executable == "codex" and rest.startswith("app-server")


def find_unmanaged_app_server_processes() -> list[int]:
    pids: list[int] = []
    for line in _ps_output().splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if _looks_like_unmanaged_app_server(pid, parts[1]):
            pids.append(pid)
    return pids


def _terminate_processes(pids: list[int]) -> set[int]:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and any(_pid_exists(pid) for pid in pids):
        time.sleep(0.05)

    for pid in pids:
        if not _pid_exists(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return set(pids)


def stop_unmanaged_app_server_processes() -> set[int]:
    return _terminate_processes(find_unmanaged_app_server_processes())


def _looks_like_remote_proxy_process(pid: int, args: str) -> bool:
    if pid == os.getpid():
        return False
    lower = args.lower()
    if "codex-safe-switch" in lower or "codex_safe_switch" in lower:
        return False
    if "codex app-server proxy" in lower:
        return True
    parts = args.split(maxsplit=1)
    executable = Path(parts[0]).name.lower() if parts else ""
    rest = parts[1].lower() if len(parts) == 2 else ""
    return executable == "codex" and rest.startswith("app-server proxy")


def find_remote_proxy_processes() -> list[int]:
    pids: list[int] = []
    for line in _ps_output().splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if _looks_like_remote_proxy_process(pid, parts[1]):
            pids.append(pid)
    return pids


def stop_remote_proxy_processes(*, exclude: Optional[set[int]] = None) -> set[int]:
    ignored = exclude or set()
    pids = [pid for pid in find_remote_proxy_processes() if pid not in ignored]
    return _terminate_processes(pids)


def _custom_codex_home_is_active(codex: Path) -> bool:
    configured = os.environ.get("CODEX_HOME")
    if not configured:
        return False
    try:
        return Path(configured).expanduser().resolve() != (Path.home() / ".codex").resolve()
    except OSError:
        return Path(configured).expanduser() != Path.home() / ".codex"


def _env_bool(name: str) -> Optional[bool]:
    value = os.environ.get(name)
    if value is None:
        return None
    return value.strip().lower() not in {"0", "false", "no", "off"}


def remote_control_repair_allowed(codex: Path) -> bool:
    override = _env_bool(REMOTE_CONTROL_REPAIR_ENV)
    if override is not None:
        return override
    return not _custom_codex_home_is_active(codex) and has_remote_control_enrollment(codex)


def cli_surface_check_allowed(codex: Path) -> bool:
    override = _env_bool(CLI_SURFACE_CHECK_ENV)
    if override is not None:
        return override
    return not _custom_codex_home_is_active(codex) and remote_control_repair_allowed(codex)


def has_remote_control_enrollment(codex: Path) -> bool:
    state_db = codex / "state_5.sqlite"
    if not state_db.exists():
        return False
    try:
        conn = connect_sqlite_readonly(state_db)
    except sqlite3.Error:
        return False
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'remote_control_enrollments'"
        ).fetchone()
        if not exists:
            return False
        row = conn.execute("SELECT COUNT(*) FROM remote_control_enrollments").fetchone()
        return bool(row and int(row[0]) > 0)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _run_codex_command(args: list[str]) -> Optional[subprocess.CompletedProcess[str]]:
    if shutil.which("codex") is None:
        return None
    try:
        return subprocess.run(
            ["codex", *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return None


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _load_daemon_version(codex: Path) -> dict:
    result = _run_codex_command(["app-server", "daemon", "version"])
    if result is None:
        return {}
    if not result.stdout.strip():
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _managed_standalone_path(codex: Path, daemon_version: dict) -> Path:
    raw = daemon_version.get("managedCodexPath")
    if raw:
        return Path(str(raw)).expanduser()
    return codex / "packages" / "standalone" / "current" / "codex"


def _desktop_bundled_codex_path() -> Path:
    raw = os.environ.get(DESKTOP_CODEX_PATH_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path("/Applications/Codex.app/Contents/Resources/codex")


def _normalize_codex_version(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    for token in text.replace("\n", " ").split():
        cleaned = token.strip(" \t,;()[]")
        if cleaned and cleaned[0].isdigit():
            return cleaned
    return text


def _run_codex_version(executable: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = result.stdout.strip() or result.stderr.strip()
    if result.returncode != 0 and not output:
        return None
    return _normalize_codex_version(output)


def maybe_warn_cli_surface_mismatch(codex: Path, daemon_version: dict) -> None:
    if not cli_surface_check_allowed(codex):
        return

    surfaces: list[CodexCliSurface] = []
    shell_path = shutil.which("codex")
    shell_version = _normalize_codex_version(daemon_version.get("cliVersion"))
    if shell_path and shell_version:
        surfaces.append(CodexCliSurface("shell codex", shell_version, Path(shell_path)))

    managed_path = _managed_standalone_path(codex, daemon_version)
    managed_version = _normalize_codex_version(daemon_version.get("managedCodexVersion"))
    if managed_path.is_file() and managed_version:
        surfaces.append(CodexCliSurface("managed standalone", managed_version, managed_path))

    desktop_path = _desktop_bundled_codex_path()
    if desktop_path.is_file():
        desktop_version = _run_codex_version(desktop_path)
        if desktop_version:
            surfaces.append(CodexCliSurface("Desktop bundled", desktop_version, desktop_path))

    versions = {surface.version for surface in surfaces}
    if len(versions) <= 1:
        return

    print("codex cli warning → multiple Codex CLI versions are active")
    for surface in surfaces:
        path = f" ({surface.path})" if surface.path is not None else ""
        print(f"  {surface.label}: {surface.version}{path}")
    print("codex cli note → Desktop app owns its bundled CLI; update/restart Codex.app instead of overwriting the app bundle")


def _remote_control_needs_standalone_install(text: str) -> bool:
    lower = text.lower()
    return "managed standalone codex install not found" in lower


def _remote_control_is_unmanaged(text: str) -> bool:
    lower = text.lower()
    return (
        "not managed by codex app-server daemon" in lower
        or "unmanaged" in lower and "app-server" in lower
    )


def _remote_control_pending_description(result: subprocess.CompletedProcess[str]) -> Optional[str]:
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    status = str(data.get("status") or "").strip()
    timed_out = data.get("timedOut") is True
    if status != "connecting" and not timed_out:
        return None
    bits = []
    if status:
        bits.append(f"status={status}")
    if timed_out:
        bits.append("timedOut=true")
    environment = data.get("environmentId")
    if environment:
        bits.append(f"environment={environment}")
    return " ".join(bits) if bits else "status=connecting"


def _print_missing_standalone_warning(path: Path) -> None:
    print(f"remote-control warning → managed standalone Codex missing at {path}")
    print(f"remote-control install → {REMOTE_CONTROL_INSTALL_COMMAND}")


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_str)
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _copy_mode(path if path.exists() else None, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _repair_remote_selection_payload(data: dict) -> bool:
    changed = False
    selected = data.get("selected-remote-host-id")
    if isinstance(selected, str) and selected.startswith(REMOTE_HOST_PREFIX):
        data.pop("selected-remote-host-id", None)
        changed = True

    for key in ELECTRON_REMOTE_STATE_KEYS:
        if key in data:
            data.pop(key, None)
            changed = True

    auto_connect = data.get("remote-connection-auto-connect-by-host-id")
    if isinstance(auto_connect, dict):
        for host_id in list(auto_connect):
            if isinstance(host_id, str) and host_id.startswith(REMOTE_HOST_PREFIX):
                auto_connect.pop(host_id, None)
                changed = True

    analytics = data.get("remote-connection-analytics-id-by-host-id")
    if isinstance(analytics, dict):
        for host_id in list(analytics):
            if isinstance(host_id, str) and host_id.startswith(REMOTE_HOST_PREFIX):
                analytics.pop(host_id, None)
                changed = True

    for key in ("codex-managed-remote-connections", "remote-projects"):
        value = data.get(key)
        if not isinstance(value, list):
            continue
        filtered = [
            item
            for item in value
            if not (
                isinstance(item, dict)
                and isinstance(item.get("hostId"), str)
                and item["hostId"].startswith(REMOTE_HOST_PREFIX)
            )
        ]
        if len(filtered) != len(value):
            data[key] = filtered
            changed = True
    return changed


def maybe_repair_remote_selection_state(codex: Path) -> None:
    if not remote_control_repair_allowed(codex):
        return

    changed_files = 0
    for rel in GLOBAL_STATE_FILES:
        path = codex / rel
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if _repair_remote_selection_payload(data):
            _atomic_write_json(path, data)
            changed_files += 1

    if changed_files:
        print(f"remote selection repaired → {changed_files} files")


def maybe_repair_remote_control(codex: Path) -> set[int]:
    if not remote_control_repair_allowed(codex):
        return set()

    daemon_version = _load_daemon_version(codex)
    maybe_warn_cli_surface_mismatch(codex, daemon_version)
    managed_path = _managed_standalone_path(codex, daemon_version)
    if not managed_path.is_file():
        _print_missing_standalone_warning(managed_path)
        return set()

    first = _run_codex_command(["remote-control", "start", "--json"])
    if first is None:
        return set()
    pending = _remote_control_pending_description(first)
    if pending:
        print(f"remote-control warning → daemon still connecting ({pending})")
        return set()
    if first.returncode == 0:
        return set()

    first_output = _combined_output(first)
    if _remote_control_needs_standalone_install(first_output):
        _print_missing_standalone_warning(managed_path)
        return set()
    if not _remote_control_is_unmanaged(first_output):
        return set()

    stopped = stop_unmanaged_app_server_processes()
    retry = _run_codex_command(["remote-control", "start", "--json"])
    if retry is not None and retry.returncode == 0:
        print(f"remote-control repaired → restarted managed app-server (stopped {len(stopped)})")
        return stopped

    retry_output = _combined_output(retry) if retry is not None else first_output
    if _remote_control_needs_standalone_install(retry_output):
        _print_missing_standalone_warning(managed_path)
    else:
        print("remote-control warning → could not repair managed app-server automatically")
    return stopped


def maybe_stop_remote_proxy_processes(codex: Path, *, exclude: Optional[set[int]] = None) -> None:
    if not remote_control_repair_allowed(codex):
        return
    stopped = stop_remote_proxy_processes(exclude=exclude)
    if not stopped:
        return
    print(f"remote-control repaired → stopped stale remote proxy processes (stopped {len(stopped)})")
    print("remote-control hint → restart Codex Desktop if old remote proxy processes reappear")
    time.sleep(REMOTE_PROXY_RESPAWN_GRACE_SECONDS)
    remaining = [pid for pid in find_remote_proxy_processes() if pid not in stopped]
    if remaining:
        joined = ", ".join(str(pid) for pid in remaining)
        print(f"remote-control warning → Desktop respawned remote proxy processes (pids: {joined})")
        print("remote-control hint → restart Codex Desktop to drop stale in-memory remote state")


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


def _parse_index_timestamp_ms(raw: object) -> int:
    if not isinstance(raw, str) or not raw.strip():
        return 0
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _format_index_timestamp(updated_at: object, updated_at_ms: object) -> str:
    millis: Optional[int] = None
    if isinstance(updated_at_ms, int):
        millis = updated_at_ms
    elif isinstance(updated_at_ms, float):
        millis = int(updated_at_ms)
    elif isinstance(updated_at, int):
        millis = updated_at * 1000
    elif isinstance(updated_at, float):
        millis = int(updated_at * 1000)
    if millis is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def _looks_like_prompt_title(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    lower = text.lower()
    return lower.startswith("<aside") or "<aside" in lower[:120] or (len(text) > 160 and "\n" in text)


def _looks_like_human_title(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not _looks_like_prompt_title(value)


def _read_session_index_state(path: Path) -> dict[str, SessionIndexThreadState]:
    state: dict[str, SessionIndexThreadState] = {}
    if not path.exists():
        return state
    with path.open() as fh:
        for raw in fh:
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            thread_id = item.get("id")
            if not isinstance(thread_id, str) or not thread_id:
                continue
            timestamp_ms = _parse_index_timestamp_ms(item.get("updated_at"))
            name = item.get("thread_name")
            current = state.get(thread_id, SessionIndexThreadState())
            latest_ms = current.latest_ms
            latest_name = current.latest_name
            if timestamp_ms >= latest_ms:
                latest_ms = timestamp_ms
                latest_name = str(name) if isinstance(name, str) else None
            best_name = current.best_name
            best_name_ms = current.best_name_ms
            if _looks_like_human_title(name) and timestamp_ms >= best_name_ms:
                best_name = str(name).strip()
                best_name_ms = timestamp_ms
            state[thread_id] = SessionIndexThreadState(
                latest_ms=latest_ms,
                latest_name=latest_name,
                best_name=best_name,
                best_name_ms=best_name_ms,
            )
    return state


def _read_session_index_latest(path: Path) -> dict[str, int]:
    return {thread_id: item.latest_ms for thread_id, item in _read_session_index_state(path).items()}


def _session_index_thread_columns(conn: sqlite3.Connection) -> list[str]:
    columns = sqlite_threads_columns(conn)
    needed = {"id", "updated_at"}
    if not needed.issubset(set(columns)):
        return []
    return columns


def repair_prompt_like_thread_titles_from_index(
    codex: Path,
    rows: list[dict[str, object]],
    index_state: dict[str, SessionIndexThreadState],
) -> set[str]:
    state_db = codex / "state_5.sqlite"
    repairs: list[tuple[str, str, object]] = []
    for item in rows:
        thread_id = item.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        current_title = item.get("title")
        best_name = index_state.get(thread_id, SessionIndexThreadState()).best_name
        if not best_name or not _looks_like_prompt_title(current_title):
            continue
        if str(current_title).strip() == best_name:
            continue
        repairs.append((best_name, thread_id, current_title))

    if not repairs:
        return set()

    backup_dir = backup_root(codex, "thread-title-repair-backup")
    backup_copy(state_db, backup_dir, codex)
    for suffix in ("-shm", "-wal"):
        sidecar = codex / f"state_5.sqlite{suffix}"
        backup_copy(sidecar, backup_dir, codex)

    repaired: set[str] = set()
    try:
        conn = sqlite3.connect(state_db)
    except sqlite3.Error:
        return repaired
    try:
        columns = sqlite_threads_columns(conn)
        if "id" not in columns or "title" not in columns:
            return repaired
        for title, thread_id, old_title in repairs:
            cur = conn.execute(
                "UPDATE threads SET title = ? WHERE id = ? AND title = ?",
                (title, thread_id, old_title),
            )
            if cur.rowcount:
                repaired.add(thread_id)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        return set()
    finally:
        conn.close()

    if repaired:
        print(f"thread titles repaired → {len(repaired)}")
        print(f"thread title backup → {backup_dir}")
    return repaired


def maybe_repair_session_index(codex: Path) -> None:
    index = codex / "session_index.jsonl"
    state_db = codex / "state_5.sqlite"
    if not state_db.exists():
        return
    try:
        conn = connect_sqlite_readonly(state_db)
    except sqlite3.Error:
        return
    try:
        columns = _session_index_thread_columns(conn)
        if not columns:
            return
        selected = [c for c in ("id", "title", "preview", "updated_at", "updated_at_ms", "archived") if c in columns]
        select_sql = ", ".join(f'"{c}"' for c in selected)
        rows = conn.execute(
            f"""
            SELECT {select_sql}
            FROM threads
            WHERE ifnull(archived, 0) = 0
            ORDER BY ifnull(updated_at_ms, updated_at * 1000) ASC
            """
        ).fetchall()
    except sqlite3.Error:
        return
    finally:
        conn.close()

    row_items = [{name: value for name, value in zip(selected, row)} for row in rows]
    index_state = _read_session_index_state(index)
    repaired_titles = repair_prompt_like_thread_titles_from_index(codex, row_items, index_state)
    if repaired_titles:
        for item in row_items:
            thread_id = item.get("id")
            if isinstance(thread_id, str) and thread_id in repaired_titles:
                best_name = index_state.get(thread_id, SessionIndexThreadState()).best_name
                if best_name:
                    item["title"] = best_name

    additions: list[dict[str, str]] = []
    for item in row_items:
        thread_id = item.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        updated_at_ms = item.get("updated_at_ms")
        updated_at = item.get("updated_at")
        if isinstance(updated_at_ms, (int, float)):
            current_ms = int(updated_at_ms)
        elif isinstance(updated_at, (int, float)):
            current_ms = int(updated_at * 1000)
        else:
            continue
        latest = index_state.get(thread_id, SessionIndexThreadState())
        title = item.get("title") or item.get("preview") or "Untitled session"
        should_append_repaired_title = (
            thread_id in repaired_titles
            and isinstance(title, str)
            and latest.latest_name != title
        )
        if current_ms <= latest.latest_ms and not should_append_repaired_title:
            continue
        additions.append(
            {
                "id": thread_id,
                "thread_name": str(title),
                "updated_at": _format_index_timestamp(updated_at, updated_at_ms),
            }
        )

    if not additions:
        return

    index.parent.mkdir(parents=True, exist_ok=True)
    with index.open("a") as fh:
        for item in additions:
            fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"session index repaired → {len(additions)} entries")


def current_provider_looks_official(codex: Path) -> bool:
    cfg = _swap.load(codex / "config.toml")
    provider = str(cfg.get("model_provider") or "").strip()
    auth_method = str(cfg.get("preferred_auth_method") or "").strip()
    return provider == "openai" and auth_method in {"", "chatgpt"}


def snapshot_official_state(codex: Path) -> None:
    cfg = codex / "config.toml"
    dir_ = official_profile_dir()
    dir_.mkdir(parents=True, exist_ok=True)
    if cfg.is_file():
        _swap.extract(cfg, dir_ / "provider.toml")
    else:
        (dir_ / "provider.toml").write_text("")
    (dir_ / "auth.json").unlink(missing_ok=True)
    write_session_config(dir_, SessionConfig())


def ensure_official_snapshot_available(codex: Path) -> None:
    if official_profile_dir().is_dir():
        prov = official_profile_dir() / "provider.toml"
        if prov.is_file():
            return
    if current_provider_looks_official(codex):
        snapshot_official_state(codex)
        return
    _die("official OpenAI snapshot not found; switch to official once, then run `codex-safe-switch official`")


def switch_to_profile(name: str, *, restart_codex: bool = False) -> int:
    normalized_name = normalize_profile_name(name)
    dir_ = profile_dir_for_name(normalized_name)
    if not dir_.is_dir():
        bootstrap_current_profile()
        dir_ = profile_dir_for_name(normalized_name)
        if not dir_.is_dir():
            _die(f"profile not found: {name}")
    prov_src = dir_ / "provider.toml"
    if not prov_src.is_file():
        _die(f"missing {prov_src}")

    codex = codex_dir()
    codex.mkdir(parents=True, exist_ok=True)
    cfg = codex / "config.toml"
    current = normalize_profile_name(active_name())

    if current_provider_looks_official(codex) and normalized_name != OFFICIAL_PROFILE_NAME:
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

    maybe_switch_session_state(current, normalized_name, codex)

    active_file().write_text(normalized_name + "\n")
    print(f"switched → {normalized_name}")
    maybe_auto_merge_history(codex)
    maybe_repair_session_index(codex)
    maybe_repair_remote_selection_state(codex)
    stopped_runtime = maybe_repair_remote_control(codex)
    if restart_codex:
        count = restart_codex_processes()
        print(f"restarted Codex processes → {count}")
    else:
        maybe_stop_remote_proxy_processes(codex, exclude=stopped_runtime)
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
    bootstrap_current_profile()
    active = normalize_profile_name(active_name())
    profiles = list_profiles()
    if not profiles:
        _die(no_profiles_message())
    for name in profiles:
        prefix = "★" if name == active else " "
        print(f"{prefix} {name}")
    return 0


def cmd_current(_args) -> int:
    bootstrap_current_profile()
    name = normalize_profile_name(active_name())
    if name:
        print(name)
        return 0
    print("(none)")
    return 1


def cmd_use(args) -> int:
    name = normalize_profile_name(args.name)
    if args.name == ALFRED_INIT_ARG:
        initialized = bootstrap_current_profile()
        if initialized is None:
            _die(no_profiles_message())
        return 0
    if not name:
        bootstrap_current_profile()
        profiles = list_profiles()
        if not profiles:
            _die(no_profiles_message())
        chosen = pick(profiles, active=normalize_profile_name(active_name()), prompt="Switch to which profile?")
        if chosen is None:
            print("cancelled")
            return 1
        name = normalize_profile_name(chosen)
    if name == OFFICIAL_PROFILE_NAME:
        return cmd_official(args)
    return switch_to_profile(name, restart_codex=getattr(args, "restart_codex", False))


def cmd_official(args) -> int:
    codex = codex_dir()
    ensure_official_snapshot_available(codex)
    return switch_to_profile(OFFICIAL_PROFILE_NAME, restart_codex=getattr(args, "restart_codex", False))


def cmd_save(args) -> int:
    name = normalize_profile_name(args.name)
    if name in {OFFICIAL_PROFILE_NAME, "bin", ".active"} or args.name.startswith("."):
        _die(f"reserved name: {args.name}")
    if args.shared and args.scope:
        _die("choose either --shared or --scope, not both")
    dir_ = profile_root() / name
    dir_.mkdir(parents=True, exist_ok=True)

    codex = codex_dir()
    cfg = codex / "config.toml"
    if cfg.is_file():
        _swap.extract(cfg, dir_ / "provider.toml")
    else:
        (dir_ / "provider.toml").write_text("")
    (dir_ / "auth.json").unlink(missing_ok=True)

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
    bootstrap_current_profile(verbose=False)
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
    if not items:
        items.append({
            "uid": "initialize",
            "title": "Initialize Codex profiles",
            "arg": ALFRED_INIT_ARG,
            "subtitle": "Run codex-safe-switch save <name> after configuring Codex once",
            "autocomplete": "initialize",
        })
    print(json.dumps({"items": items}, ensure_ascii=False))
    return 0


def cmd_pick(_args) -> int:
    """Default action when no subcommand is given: interactive switch."""
    bootstrap_current_profile()
    profiles = list_profiles()
    if not profiles:
        _die(no_profiles_message())
    chosen = pick(profiles, active=normalize_profile_name(active_name()), prompt="Switch to which profile?")
    if chosen is None:
        print("cancelled")
        return 1
    args_ns = argparse.Namespace(name=chosen, restart_codex=False)
    return cmd_use(args_ns)


def cmd_restart_codex(_args) -> int:
    count = restart_codex_processes()
    print(f"restarted Codex processes → {count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codex-safe-switch",
        description="Switch between Codex provider profiles. Run with no subcommand for an interactive picker.",
    )
    subs = p.add_subparsers(dest="cmd", metavar="<command>")

    s = subs.add_parser("ls", aliases=["list"], help="list profiles (★ = active)")
    s.set_defaults(func=cmd_ls)

    s = subs.add_parser("current", help="print the active profile name")
    s.set_defaults(func=cmd_current)

    s = subs.add_parser("official", aliases=["openai"], help="switch back to the official OpenAI provider")
    s.add_argument("--restart-codex", action="store_true", help="terminate Codex app/server processes after switching")
    s.set_defaults(func=cmd_official)

    s = subs.add_parser("use", aliases=["switch"], help="load <name> (interactive if omitted)")
    s.add_argument("name", nargs="?")
    s.add_argument("--restart-codex", action="store_true", help="terminate Codex app/server processes after switching")
    s.set_defaults(func=cmd_use)

    s = subs.add_parser("save", help="snapshot the current provider config as <name>")
    s.add_argument("name")
    s.add_argument("--scope", help="also bind this profile to a session-state scope and seed it now")
    s.add_argument("--shared", action="store_true", help="clear any session-state scope while saving")
    s.set_defaults(func=cmd_save)

    s = subs.add_parser("show", help="print <name>'s provider.toml and session state")
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

    s = subs.add_parser("restart-codex", help="terminate Codex app/server processes so config changes take effect")
    s.set_defaults(func=cmd_restart_codex)

    s = subs.add_parser("rm", aliases=["remove"], help="delete profile (active is protected)")
    s.add_argument("name")
    s.set_defaults(func=cmd_rm)

    s = subs.add_parser("alfred-list", help="JSON for Alfred Script Filter")
    s.set_defaults(func=cmd_alfred_list)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.cmd is None:
            return cmd_pick(args)
        return args.func(args)
    except KeyboardInterrupt:
        print("codex-safe-switch: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
