from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_safe_switch import cli


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def write_rollout(path: Path, provider: str, model: str = "gpt-5.4") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "session_meta", "payload": {"model_provider": provider}},
        {"type": "turn_context", "payload": {"model_provider": provider, "model": model}},
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


def write_threads_db(path: Path, provider: str, model: str = "gpt-5.4") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE threads (model_provider TEXT, model TEXT)")
        conn.execute("INSERT INTO threads (model_provider, model) VALUES (?, ?)", (provider, model))
        conn.commit()
    finally:
        conn.close()


def write_thread_index_db(
    path: Path,
    *,
    thread_id: str,
    title: str,
    updated_at: int,
    updated_at_ms: int,
    provider: str,
    model: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE threads (
                id TEXT,
                title TEXT,
                updated_at INTEGER,
                updated_at_ms INTEGER,
                archived INTEGER,
                model_provider TEXT,
                model TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO threads
                (id, title, updated_at, updated_at_ms, archived, model_provider, model)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (thread_id, title, updated_at, updated_at_ms, provider, model),
        )
        conn.commit()
    finally:
        conn.close()


class CodexSwitchCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.codex_home = self.tmp / "codex"
        self.profile_root = self.tmp / "profiles"
        self.codex_home.mkdir()
        self.profile_root.mkdir()
        self.env = {
            "CODEX_HOME": str(self.codex_home),
            "CODEX_PROFILE_ROOT": str(self.profile_root),
        }

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_cli(self, *argv: str) -> int:
        with patch.dict(os.environ, self.env, clear=False):
            return cli.main(list(argv))

    def run_cli_output(self, *argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with patch.dict(os.environ, self.env, clear=False), redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def run_cli_streams(self, *argv: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, self.env, clear=False),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = cli.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def snapshot_official(self) -> None:
        with patch.dict(os.environ, self.env, clear=False):
            cli.snapshot_official_state(self.codex_home)

    def read_config(self) -> str:
        return (self.codex_home / "config.toml").read_text()

    def set_current_official(self) -> None:
        (self.codex_home / "config.toml").write_text(
            'model = "gpt-5.4"\nmodel_provider = "openai"\npreferred_auth_method = "chatgpt"\n'
        )
        write_json(self.codex_home / "auth.json", {"auth_mode": "chatgpt", "token": "official"})

    def set_current_relay(self) -> None:
        (self.codex_home / "config.toml").write_text(
            '\n'.join([
                'model = "gpt-5.5"',
                'model_provider = "relay"',
                'preferred_auth_method = "apikey"',
                '',
                '[model_providers.relay]',
                'name = "relay"',
                'base_url = "https://relay.example/openai"',
                'wire_api = "responses"',
                'env_key = "RELAY_KEY"',
                '',
            ])
        )
        write_json(self.codex_home / "auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": "relay"})

    def test_first_run_ls_imports_existing_relay_config(self) -> None:
        self.set_current_relay()

        _code, output = self.run_cli_output("ls")

        self.assertIn("initialized profile from existing Codex config → relay", output)
        self.assertIn("★ relay", output)
        self.assertEqual((self.profile_root / ".active").read_text().strip(), "relay")
        self.assertTrue((self.profile_root / "relay" / "auth.json").is_file())
        provider = (self.profile_root / "relay" / "provider.toml").read_text()
        self.assertIn('model_provider = "relay"', provider)

    def test_save_env_key_profile_does_not_store_chatgpt_tokens(self) -> None:
        self.set_current_official()
        (self.codex_home / "config.toml").write_text(
            '\n'.join([
                'model = "gpt-5.5"',
                'model_provider = "relay"',
                'preferred_auth_method = "apikey"',
                '',
                '[model_providers.relay]',
                'name = "relay"',
                'base_url = "https://relay.example/openai"',
                'wire_api = "responses"',
                'requires_openai_auth = false',
                'env_key = "RELAY_KEY"',
                '',
            ])
        )

        _code, output = self.run_cli_output("save", "relay")

        self.assertIn("saved → relay", output)
        self.assertFalse((self.profile_root / "relay" / "auth.json").exists())
        provider = (self.profile_root / "relay" / "provider.toml").read_text()
        self.assertIn('requires_openai_auth = false', provider)
        self.assertIn('env_key = "RELAY_KEY"', provider)

    def test_first_run_ls_imports_existing_official_config(self) -> None:
        self.set_current_official()

        _code, output = self.run_cli_output("ls")

        self.assertIn("initialized profile from existing Codex config → official", output)
        self.assertIn("★ official", output)
        self.assertEqual((self.profile_root / ".active").read_text().strip(), "official")
        self.assertTrue((self.profile_root / ".official" / "auth.json").is_file())

    def test_alfred_list_offers_initialize_action_when_profiles_and_config_are_missing(self) -> None:
        _code, output = self.run_cli_output("alfred-list")

        payload = json.loads(output)
        self.assertEqual(payload["items"][0]["title"], "Initialize Codex profiles")
        self.assertEqual(payload["items"][0]["arg"], "__init__")
        self.assertIn("Run codex-safe-switch save", payload["items"][0]["subtitle"])

    def test_restart_codex_kills_only_matching_codex_processes(self) -> None:
        ps_output = "\n".join([
            "101 /Applications/Codex.app/Contents/MacOS/Codex",
            "102 /usr/local/bin/codex app-server",
            "103 /Users/me/.local/bin/codex-safe-switch restart-codex",
            "104 /usr/bin/python other.py",
            "",
        ])

        with patch("codex_safe_switch.cli._ps_output", return_value=ps_output), patch(
            "codex_safe_switch.cli._pid_exists", return_value=False
        ), patch("codex_safe_switch.cli.os.kill") as kill:
            _code, output = self.run_cli_output("restart-codex")

        self.assertIn("restarted Codex processes → 2", output)
        self.assertEqual([call.args[0] for call in kill.call_args_list], [101, 102])

    def test_use_restart_codex_restarts_after_switch(self) -> None:
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        write_json(relay_dir / "auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": "relay"})
        (relay_dir / "provider.toml").write_text('model_provider = "relay"\n')

        with patch("codex_safe_switch.cli.restart_codex_processes", return_value=3) as restart:
            _code, output = self.run_cli_output("use", "relay", "--restart-codex")

        self.assertIn("switched → relay", output)
        self.assertIn("restarted Codex processes → 3", output)
        restart.assert_called_once()

    def test_use_env_key_profile_without_auth_preserves_existing_chatgpt_login(self) -> None:
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        (relay_dir / "provider.toml").write_text(
            '\n'.join([
                'model = "gpt-5.5"',
                'model_provider = "relay"',
                'preferred_auth_method = "apikey"',
                '',
                '[model_providers.relay]',
                'name = "relay"',
                'base_url = "https://relay.example/openai"',
                'wire_api = "responses"',
                'requires_openai_auth = false',
                'env_key = "RELAY_KEY"',
                '',
            ])
        )

        _code, output = self.run_cli_output("use", "relay")

        self.assertIn("switched → relay", output)
        config = self.read_config()
        self.assertIn('model_provider = "relay"', config)
        self.assertIn('requires_openai_auth = false', config)
        self.assertIn('env_key = "RELAY_KEY"', config)
        self.assertEqual(
            json.loads((self.codex_home / "auth.json").read_text()),
            {"auth_mode": "chatgpt", "token": "official"},
        )

    def test_use_env_key_profile_ignores_stale_profile_chatgpt_tokens(self) -> None:
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        write_json(
            relay_dir / "auth.json",
            {"auth_mode": "chatgpt", "tokens": {"refresh_token": "stale"}},
        )
        (relay_dir / "provider.toml").write_text(
            '\n'.join([
                'model = "gpt-5.5"',
                'model_provider = "relay"',
                'preferred_auth_method = "apikey"',
                '',
                '[model_providers.relay]',
                'name = "relay"',
                'base_url = "https://relay.example/openai"',
                'wire_api = "responses"',
                'requires_openai_auth = false',
                'env_key = "RELAY_KEY"',
                '',
            ])
        )

        self.run_cli("use", "relay")

        self.assertEqual(
            json.loads((self.codex_home / "auth.json").read_text()),
            {"auth_mode": "chatgpt", "token": "official"},
        )

    def test_ctrl_c_exits_cleanly_without_traceback(self) -> None:
        with patch("codex_safe_switch.cli.cmd_pick", side_effect=KeyboardInterrupt):
            code, stdout, stderr = self.run_cli_streams()

        self.assertEqual(code, 130)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "codex-safe-switch: interrupted\n")

    def test_use_openai_alias_restores_official_snapshot_and_merges_history(self) -> None:
        self.set_current_official()
        self.snapshot_official()

        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        write_json(relay_dir / "auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": "relay"})
        (relay_dir / "provider.toml").write_text(
            '\n'.join([
                'model = "gpt-5.4"',
                'model_provider = "relay"',
                'preferred_auth_method = "apikey"',
                '',
                '[model_providers.relay]',
                'name = "relay"',
                'base_url = "https://relay.example/openai"',
                'wire_api = "responses"',
                'env_key = "RELAY_KEY"',
                '',
            ])
        )

        write_rollout(self.codex_home / "sessions" / "2026" / "05" / "test.jsonl", "relay", model="gpt-5.5")
        write_threads_db(self.codex_home / "state_5.sqlite", "relay", model="gpt-5.5")

        self.run_cli("use", "openai")

        config = self.read_config()
        self.assertIn('model_provider = "openai"', config)
        self.assertIn('"official"', (self.codex_home / "auth.json").read_text())
        self.assertEqual((self.profile_root / ".active").read_text().strip(), "official")
        rollout = (self.codex_home / "sessions" / "2026" / "05" / "test.jsonl").read_text()
        self.assertIn('"model_provider":"openai"', rollout)
        self.assertIn('"model":"gpt-5.4"', rollout)

        conn = sqlite3.connect(self.codex_home / "state_5.sqlite")
        try:
            provider, model = conn.execute("SELECT model_provider, model FROM threads").fetchone()
        finally:
            conn.close()
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-5.4")

    def test_use_profile_auto_merges_history_to_target_provider(self) -> None:
        self.set_current_official()

        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        write_json(relay_dir / "auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": "relay"})
        (relay_dir / "provider.toml").write_text(
            '\n'.join([
                'model = "gpt-5.5"',
                'model_provider = "relay"',
                'preferred_auth_method = "apikey"',
                '',
                '[model_providers.relay]',
                'name = "relay"',
                'base_url = "https://relay.example/openai"',
                'wire_api = "responses"',
                'env_key = "RELAY_KEY"',
                '',
            ])
        )

        write_rollout(self.codex_home / "sessions" / "2026" / "05" / "test.jsonl", "openai", model="gpt-5.4")
        write_threads_db(self.codex_home / "state_5.sqlite", "openai", model="gpt-5.4")

        self.run_cli("use", "relay")

        config = self.read_config()
        self.assertIn('model_provider = "relay"', config)
        self.assertEqual((self.profile_root / ".active").read_text().strip(), "relay")
        rollout = (self.codex_home / "sessions" / "2026" / "05" / "test.jsonl").read_text()
        self.assertIn('"model_provider":"relay"', rollout)
        self.assertIn('"model":"gpt-5.5"', rollout)

        conn = sqlite3.connect(self.codex_home / "state_5.sqlite")
        try:
            provider, model = conn.execute("SELECT model_provider, model FROM threads").fetchone()
        finally:
            conn.close()
        self.assertEqual(provider, "relay")
        self.assertEqual(model, "gpt-5.5")

    def test_merge_history_keep_models_preserves_existing_thread_models(self) -> None:
        self.set_current_official()
        write_rollout(self.codex_home / "sessions" / "2026" / "05" / "test.jsonl", "relay", model="gpt-5.5")
        write_threads_db(self.codex_home / "state_5.sqlite", "relay", model="gpt-5.5")

        self.run_cli("merge-history", "--provider", "openai", "--keep-models")

        rollout = (self.codex_home / "sessions" / "2026" / "05" / "test.jsonl").read_text()
        self.assertIn('"model_provider":"openai"', rollout)
        self.assertIn('"model":"gpt-5.5"', rollout)

        conn = sqlite3.connect(self.codex_home / "state_5.sqlite")
        try:
            provider, model = conn.execute("SELECT model_provider, model FROM threads").fetchone()
        finally:
            conn.close()
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-5.5")

    def test_merge_history_dry_run_reports_without_writing_files(self) -> None:
        self.set_current_official()
        rollout_path = self.codex_home / "sessions" / "2026" / "05" / "test.jsonl"
        db_path = self.codex_home / "state_5.sqlite"
        write_rollout(rollout_path, "relay", model="gpt-5.5")
        write_threads_db(db_path, "relay", model="gpt-5.5")
        rollout_before = rollout_path.read_text()

        _code, output = self.run_cli_output("merge-history", "--provider", "openai", "--dry-run")

        self.assertIn("would merge history", output)
        self.assertIn("backup →", output)
        self.assertIn("(would create)", output)
        self.assertIn("rollout files would update → 1", output)
        self.assertIn("rollout lines would update → 2", output)
        self.assertIn("state rows would update → 1", output)
        self.assertEqual(rollout_path.read_text(), rollout_before)
        self.assertFalse(list(self.codex_home.glob("history-merge-backup-*")))

        conn = sqlite3.connect(db_path)
        try:
            provider, model = conn.execute("SELECT model_provider, model FROM threads").fetchone()
        finally:
            conn.close()
        self.assertEqual(provider, "relay")
        self.assertEqual(model, "gpt-5.5")

    def test_doctor_history_prints_current_state_and_drift(self) -> None:
        self.set_current_official()
        self.snapshot_official()
        (self.profile_root / ".active").write_text("official\n")
        write_threads_db(self.codex_home / "state_5.sqlite", "relay", model="gpt-5.5")

        _code, output = self.run_cli_output("doctor-history")

        self.assertIn("history doctor", output)
        self.assertIn("current profile → official", output)
        self.assertIn("current config → provider=openai model=gpt-5.4", output)
        self.assertIn("session state → shared", output)
        self.assertIn("threads provider/model distribution:", output)
        self.assertIn("provider=relay model=gpt-5.5", output)
        self.assertIn("recent threads:", output)
        self.assertIn("sqlite provider/model drift → yes", output)
        self.assertIn("planned history alignment →", output)
        self.assertIn("rollout metadata drift → no", output)
        self.assertIn("provider/model drift → yes", output)

    def test_use_warns_when_managed_standalone_codex_is_missing(self) -> None:
        self.env[cli.REMOTE_CONTROL_REPAIR_ENV] = "1"
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        (relay_dir / "provider.toml").write_text('model_provider = "relay"\n')
        missing = self.codex_home / "packages" / "standalone" / "current" / "codex"
        version = {
            "status": "running",
            "managedCodexPath": str(missing),
            "managedCodexVersion": None,
            "cliVersion": "0.134.0",
            "appServerVersion": "0.134.0",
        }

        with patch("codex_safe_switch.cli.shutil.which", return_value="/usr/local/bin/codex"), patch(
            "codex_safe_switch.cli.subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["codex", "app-server", "daemon", "version"],
                0,
                stdout=json.dumps(version),
                stderr="",
            ),
        ):
            _code, output = self.run_cli_output("use", "relay")

        self.assertIn("switched → relay", output)
        self.assertIn("remote-control warning → managed standalone Codex missing", output)
        self.assertIn("curl -fsSL https://chatgpt.com/codex/install.sh | sh", output)

    def test_use_repairs_unmanaged_app_server_and_retries_remote_control(self) -> None:
        self.env[cli.REMOTE_CONTROL_REPAIR_ENV] = "1"
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        (relay_dir / "provider.toml").write_text('model_provider = "relay"\n')
        managed = self.codex_home / "packages" / "standalone" / "current" / "codex"
        managed.parent.mkdir(parents=True)
        managed.write_text("#!/bin/sh\n")
        version = {
            "status": "running",
            "managedCodexPath": str(managed),
            "managedCodexVersion": "0.134.0",
            "cliVersion": "0.134.0",
            "appServerVersion": "0.134.0",
        }
        ps_output = "\n".join(
            [
                "201 /usr/local/bin/codex app-server --listen unix://",
                "202 /usr/local/bin/codex app-server proxy --listen unix://",
                "203 /Applications/Codex.app/Contents/MacOS/Codex",
                "",
            ]
        )
        remote_error = "Error: app server is running but is not managed by codex app-server daemon"
        calls = [
            subprocess.CompletedProcess(
                ["codex", "app-server", "daemon", "version"],
                0,
                stdout=json.dumps(version),
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["codex", "remote-control", "start", "--json"],
                1,
                stdout="",
                stderr=remote_error,
            ),
            subprocess.CompletedProcess(
                ["codex", "remote-control", "start", "--json"],
                0,
                stdout='{"status":"running"}',
                stderr="",
            ),
        ]

        with patch("codex_safe_switch.cli.shutil.which", return_value="/usr/local/bin/codex"), patch(
            "codex_safe_switch.cli.subprocess.run", side_effect=calls
        ) as run, patch("codex_safe_switch.cli._ps_output", return_value=ps_output), patch(
            "codex_safe_switch.cli._pid_exists", return_value=False
        ), patch("codex_safe_switch.cli.os.kill") as kill:
            _code, output = self.run_cli_output("use", "relay")

        self.assertIn("remote-control repaired → restarted managed app-server", output)
        self.assertEqual([call.args[0] for call in kill.call_args_list], [201, 202])
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["codex", "app-server", "daemon", "version"],
                ["codex", "remote-control", "start", "--json"],
                ["codex", "remote-control", "start", "--json"],
            ],
        )

    def test_use_clears_stale_remote_selection_state(self) -> None:
        self.env[cli.REMOTE_CONTROL_REPAIR_ENV] = "1"
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        (relay_dir / "provider.toml").write_text('model_provider = "relay"\n')
        stale_state = {
            "selected-remote-host-id": "remote-ssh-codex-managed:tailnet-mm",
            "electron-local-remote-control-environment-id": "env_old",
            "electron-local-remote-control-installation-id": "install_old",
            "codex-managed-remote-connections": [
                {"hostId": "remote-ssh-discovered:mm", "displayName": "frp-mm"},
                {"hostId": "remote-ssh-codex-managed:tailnet-mm", "displayName": "tailnet-mm"},
                {"hostId": "local-current", "displayName": "local"},
            ],
            "remote-connection-auto-connect-by-host-id": {
                "remote-ssh-discovered:mm": True,
                "remote-ssh-codex-managed:tailnet-mm": False,
                "local-current": True,
            },
            "remote-connection-analytics-id-by-host-id": {
                "remote-ssh-discovered:mm": "analytics-old",
                "remote-ssh-codex-managed:tailnet-mm": "analytics-tailnet",
                "local-current": "analytics-local",
            },
            "remote-projects": [
                {"hostId": "remote-ssh-discovered:mm", "remotePath": "/tmp/old"},
                {"hostId": "local-current", "remotePath": "/tmp/local"},
            ],
        }
        write_json(self.codex_home / ".codex-global-state.json", stale_state)
        write_json(self.codex_home / ".codex-global-state.json.bak", stale_state)

        with patch("codex_safe_switch.cli.shutil.which", return_value=None):
            _code, output = self.run_cli_output("use", "relay")

        self.assertIn("remote selection repaired → 2 files", output)
        for path in [
            self.codex_home / ".codex-global-state.json",
            self.codex_home / ".codex-global-state.json.bak",
        ]:
            repaired = json.loads(path.read_text())
            self.assertNotIn("selected-remote-host-id", repaired)
            self.assertNotIn("electron-local-remote-control-environment-id", repaired)
            self.assertNotIn("electron-local-remote-control-installation-id", repaired)
            self.assertEqual(
                repaired["remote-connection-auto-connect-by-host-id"],
                {"local-current": True},
            )
            self.assertEqual(
                repaired["remote-connection-analytics-id-by-host-id"],
                {"local-current": "analytics-local"},
            )
            self.assertEqual(
                repaired["codex-managed-remote-connections"],
                [{"hostId": "local-current", "displayName": "local"}],
            )
            self.assertEqual(
                repaired["remote-projects"],
                [{"hostId": "local-current", "remotePath": "/tmp/local"}],
            )

    def test_use_warns_when_remote_control_start_times_out(self) -> None:
        self.env[cli.REMOTE_CONTROL_REPAIR_ENV] = "1"
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        (relay_dir / "provider.toml").write_text('model_provider = "relay"\n')
        managed = self.codex_home / "packages" / "standalone" / "current" / "codex"
        managed.parent.mkdir(parents=True)
        managed.write_text("#!/bin/sh\n")
        version = {
            "managedCodexPath": str(managed),
            "managedCodexVersion": "0.134.0",
        }
        start_payload = {
            "mode": "daemon",
            "status": "connecting",
            "serverName": "mbp.local",
            "environmentId": "env_new",
            "timedOut": True,
        }

        with patch("codex_safe_switch.cli.shutil.which", return_value="/usr/local/bin/codex"), patch(
            "codex_safe_switch.cli.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(
                    ["codex", "app-server", "daemon", "version"],
                    0,
                    stdout=json.dumps(version),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    ["codex", "remote-control", "start", "--json"],
                    0,
                    stdout=json.dumps(start_payload),
                    stderr="",
                ),
            ],
        ), patch("codex_safe_switch.cli._ps_output", return_value=""):
            _code, output = self.run_cli_output("use", "relay")

        self.assertIn("remote-control warning → daemon still connecting", output)
        self.assertIn("environment=env_new", output)

    def test_use_warns_when_desktop_bundled_cli_version_differs(self) -> None:
        self.env[cli.REMOTE_CONTROL_REPAIR_ENV] = "1"
        self.env[cli.CLI_SURFACE_CHECK_ENV] = "1"
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        (relay_dir / "provider.toml").write_text('model_provider = "relay"\n')
        managed = self.codex_home / "packages" / "standalone" / "current" / "codex"
        managed.parent.mkdir(parents=True)
        managed.write_text("#!/bin/sh\n")
        desktop = self.tmp / "Codex.app" / "Contents" / "Resources" / "codex"
        desktop.parent.mkdir(parents=True)
        desktop.write_text("#!/bin/sh\n")
        self.env["CODEX_DESKTOP_CODEX_PATH"] = str(desktop)
        version = {
            "status": "running",
            "managedCodexPath": str(managed),
            "managedCodexVersion": "0.134.0",
            "cliVersion": "0.134.0",
            "appServerVersion": "0.134.0",
        }

        with patch("codex_safe_switch.cli.shutil.which", return_value="/usr/local/bin/codex"), patch(
            "codex_safe_switch.cli.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(
                    ["codex", "app-server", "daemon", "version"],
                    0,
                    stdout=json.dumps(version),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    [str(desktop), "--version"],
                    0,
                    stdout="codex-cli 0.133.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    ["codex", "remote-control", "start", "--json"],
                    0,
                    stdout='{"status":"connected"}',
                    stderr="",
                ),
            ],
        ), patch("codex_safe_switch.cli._ps_output", return_value=""):
            _code, output = self.run_cli_output("use", "relay")

        self.assertIn("codex cli warning → multiple Codex CLI versions are active", output)
        self.assertIn("shell codex: 0.134.0 (/usr/local/bin/codex)", output)
        self.assertIn(f"managed standalone: 0.134.0 ({managed})", output)
        self.assertIn(f"Desktop bundled: 0.133.0 ({desktop})", output)
        self.assertIn("Desktop app owns its bundled CLI", output)

    def test_use_stops_remote_proxy_processes_after_switch(self) -> None:
        self.env[cli.REMOTE_CONTROL_REPAIR_ENV] = "1"
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        (relay_dir / "provider.toml").write_text('model_provider = "relay"\n')
        managed = self.codex_home / "packages" / "standalone" / "current" / "codex"
        managed.parent.mkdir(parents=True)
        managed.write_text("#!/bin/sh\n")
        version = {
            "status": "running",
            "managedCodexPath": str(managed),
            "managedCodexVersion": "0.134.0",
        }
        ps_output = "\n".join(
            [
                "301 ssh -T mm sh -c 'codex app-server proxy'",
                "302 /bin/sh -c codex app-server proxy",
                "303 codex app-server proxy",
                "304 ssh -T other.example uptime",
                "",
            ]
        )

        with patch("codex_safe_switch.cli.shutil.which", return_value="/usr/local/bin/codex"), patch(
            "codex_safe_switch.cli.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(
                    ["codex", "app-server", "daemon", "version"],
                    0,
                    stdout=json.dumps(version),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    ["codex", "remote-control", "start", "--json"],
                    0,
                    stdout='{"status":"connected"}',
                    stderr="",
                ),
            ],
        ), patch("codex_safe_switch.cli._ps_output", return_value=ps_output), patch(
            "codex_safe_switch.cli._pid_exists", return_value=False
        ), patch("codex_safe_switch.cli.os.kill") as kill:
            _code, output = self.run_cli_output("use", "relay")

        self.assertIn("remote-control repaired → stopped stale remote proxy processes (stopped 3)", output)
        self.assertEqual([call.args[0] for call in kill.call_args_list], [301, 302, 303])

    def test_use_warns_when_desktop_respawns_remote_proxy_processes(self) -> None:
        self.env[cli.REMOTE_CONTROL_REPAIR_ENV] = "1"
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        (relay_dir / "provider.toml").write_text('model_provider = "relay"\n')
        managed = self.codex_home / "packages" / "standalone" / "current" / "codex"
        managed.parent.mkdir(parents=True)
        managed.write_text("#!/bin/sh\n")
        version = {
            "status": "running",
            "managedCodexPath": str(managed),
            "managedCodexVersion": "0.134.0",
        }
        ps_before = "\n".join([
            "301 ssh -T mm sh -c 'codex app-server proxy'",
            "302 codex app-server proxy",
            "",
        ])
        ps_after = "\n".join([
            "401 ssh -T mm sh -c 'codex app-server proxy'",
            "402 codex app-server proxy",
            "",
        ])

        with patch("codex_safe_switch.cli.shutil.which", return_value="/usr/local/bin/codex"), patch(
            "codex_safe_switch.cli.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(
                    ["codex", "app-server", "daemon", "version"],
                    0,
                    stdout=json.dumps(version),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    ["codex", "remote-control", "start", "--json"],
                    0,
                    stdout='{"status":"connected"}',
                    stderr="",
                ),
            ],
        ), patch("codex_safe_switch.cli._ps_output", side_effect=[ps_before, ps_after]), patch(
            "codex_safe_switch.cli._pid_exists", return_value=False
        ), patch("codex_safe_switch.cli.os.kill"):
            _code, output = self.run_cli_output("use", "relay")

        self.assertIn("remote-control repaired → stopped stale remote proxy processes (stopped 2)", output)
        self.assertIn("remote-control warning → Desktop respawned remote proxy processes", output)
        self.assertIn("restart Codex Desktop", output)

    def test_restart_codex_stops_remote_proxy_process_chain(self) -> None:
        ps_output = "\n".join(
            [
                "301 ssh -T mm sh -c 'codex app-server proxy'",
                "302 /bin/sh -c codex app-server proxy",
                "303 codex app-server proxy",
                "304 ssh -T other.example uptime",
                "",
            ]
        )

        with patch("codex_safe_switch.cli._ps_output", return_value=ps_output), patch(
            "codex_safe_switch.cli._pid_exists", return_value=False
        ), patch("codex_safe_switch.cli.os.kill") as kill:
            _code, output = self.run_cli_output("restart-codex")

        self.assertIn("restarted Codex processes → 3", output)
        self.assertEqual([call.args[0] for call in kill.call_args_list], [301, 302, 303])

    def test_use_repairs_stale_session_index_from_sqlite_threads(self) -> None:
        self.set_current_official()
        relay_dir = self.profile_root / "relay"
        relay_dir.mkdir()
        (relay_dir / "provider.toml").write_text(
            '\n'.join([
                'model = "gpt-5.5"',
                'model_provider = "relay"',
                '',
            ])
        )
        index = self.codex_home / "session_index.jsonl"
        index.write_text(
            json.dumps(
                {
                    "id": "thread-1",
                    "thread_name": "Old title",
                    "updated_at": "2026-05-28T08:00:00Z",
                }
            )
            + "\n"
        )
        write_thread_index_db(
            self.codex_home / "state_5.sqlite",
            thread_id="thread-1",
            title="New title",
            updated_at=1779964509,
            updated_at_ms=1779964509824,
            provider="relay",
            model="gpt-5.5",
        )

        _code, output = self.run_cli_output("use", "relay")

        self.assertIn("session index repaired → 1 entries", output)
        lines = [json.loads(line) for line in index.read_text().splitlines()]
        self.assertEqual(lines[-1]["id"], "thread-1")
        self.assertEqual(lines[-1]["thread_name"], "New title")
        self.assertEqual(lines[-1]["updated_at"], "2026-05-28T10:35:09.824000Z")


if __name__ == "__main__":
    unittest.main()
