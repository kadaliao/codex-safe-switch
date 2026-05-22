from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from codex_profile_switcher import cli


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


if __name__ == "__main__":
    unittest.main()
