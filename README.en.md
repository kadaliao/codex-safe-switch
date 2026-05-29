# codex-safe-switch

[![PyPI](https://img.shields.io/pypi/v/codex-safe-switch.svg)](https://pypi.org/project/codex-safe-switch/)
[![CI](https://github.com/kadaliao/codex-safe-switch/actions/workflows/ci.yml/badge.svg)](https://github.com/kadaliao/codex-safe-switch/actions/workflows/ci.yml)

[中文](README.md) | English

One command to switch [Codex CLI](https://github.com/openai/codex) provider configs — official ChatGPT login, third-party relays, multiple API keys. CLI + optional Alfred workflow.

## Install

```bash
uv tool install codex-safe-switch
```

Requires [`uv`](https://github.com/astral-sh/uv). Installs `codex-safe-switch` onto `$PATH` (default `~/.local/bin/`).

## Quick start

```bash
codex-safe-switch           # interactive picker (↑/↓, enter to switch)
codex-safe-switch ls        # list profiles, ★ marks the active one
codex-safe-switch save dev  # snapshot current ~/.codex state as `dev`
codex-safe-switch official  # switch back to the official OpenAI ChatGPT login
```

First run imports your existing `~/.codex/config.toml` so nothing is lost.

<details>
<summary><strong>All commands</strong></summary>

```text
codex-safe-switch              # interactive picker
codex-safe-switch ls           # list profiles, ★ marks the active one
codex-safe-switch current      # print the active profile
codex-safe-switch official     # switch back to official OpenAI ChatGPT login (alias: openai)
codex-safe-switch use [name]   # load <name>; omit for the picker
codex-safe-switch save <name>  # snapshot the current ~/.codex state as <name>
codex-safe-switch show <name>  # print <name>'s provider.toml + auth.json key names
codex-safe-switch state <name> # show/set the session-state scope for a profile
codex-safe-switch rm <name>    # delete profile (the active one is protected)
codex-safe-switch restart-codex
                               # terminate Codex app/server processes so a switch takes effect
codex-safe-switch merge-history --dry-run
                               # preview history metadata changes without writing files
codex-safe-switch doctor-history
                               # inspect current history provider/model state read-only
codex-safe-switch alfred-list  # JSON for Alfred Script Filter
```

`use` / `official` both accept `--restart-codex` to bounce the Codex app/server in one step.

The picker auto-falls back to a numeric menu when stdin/stdout aren't TTYs (pipes, scripts).

</details>

<details>
<summary><strong>Alfred workflow</strong></summary>

After `uv tool install`, double-click `alfred/codex-safe-switch.alfredworkflow`. Trigger with keyword `cx`.

The workflow calls `$HOME/.local/bin/codex-safe-switch`. If `uv tool install` put the binary elsewhere (`uv tool dir --bin` to check), edit the two `script` blocks in the workflow's plist accordingly.

</details>

<details>
<summary><strong>What makes it "safe"</strong></summary>

**Only the provider slice is swapped — local state is preserved.** A profile owns these keys/tables in `~/.codex/config.toml`; everything else (trusted projects, plugins, marketplaces, MCP servers, TUI prefs, etc.) is left untouched:

- `model`, `model_provider`, `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity`
- `wire_api`, `disable_response_storage`, `preferred_auth_method`
- `[model_providers.*]`

**`auth.json` is only copied when needed.** A profile stores or writes back `auth.json` only when the provider explicitly needs OpenAI/ChatGPT auth, or for legacy API-key configs that don't declare `requires_openai_auth = false`. Relays that use `env_key` won't clobber your official ChatGPT login cache — Codex remote connections can keep using the same ChatGPT account.

**History is aligned by default.** Every `use` / `official` aligns local Codex history metadata to the active provider and model, so session history stays visible across relays and the official OpenAI login:

- Rollout files and the `state_5.sqlite` threads table get fixed automatically.
- If `session_index.jsonl` has fallen behind the latest threads in SQLite, the switch appends repaired index entries so mobile history lists don't stay pinned to an older point.
- On hosts that have used Codex remote-control, the switch also checks the managed app-server path and clears stale unix sockets / stale SSH remote proxy processes.
- `merge-history --keep-models` does a provider-only repair; `--dry-run` previews; `doctor-history` is read-only diagnostics.

**One-step back to official.** `codex-safe-switch official` is the shortcut back to the official ChatGPT login. The tool keeps a hidden `~/.codex/profiles/.official/` snapshot, refreshed automatically the first time you switch away from official.

**Process isolation.** `restart-codex` (and `--restart-codex`) precisely skips the `codex-safe-switch` process itself so it never kills its own switch.

</details>

<details>
<summary><strong>Profile layout + adding a relay</strong></summary>

```text
~/.codex/profiles/
├── .active                       # plaintext: name of the active profile
├── .official/
│   ├── auth.json                 # copied into ~/.codex/auth.json on switch
│   └── provider.toml             # official login provider slice
└── myrelay/
    ├── auth.json                 # optional; only when the profile owns a key/token
    └── provider.toml             # only provider-related keys (see examples/)
```

**Add a relay profile**

1. Configure the relay normally in `~/.codex/config.toml` and verify `codex` works.
2. If the key comes from the environment, prefer `requires_openai_auth = false` plus `env_key = "..."`. That profile doesn't need `auth.json`; switching to it preserves the current official ChatGPT login cache.
3. `codex-safe-switch save <name>` — snapshots the provider slice into a new profile.
4. Use `cx` (Alfred) or `codex-safe-switch use <name>` to switch anytime.

You can also hand-author profile files — see `examples/relay-profile/`.

</details>

<details>
<summary><strong>Env vars / dev install / releasing</strong></summary>

**Env vars**

| Var                  | Default              | Purpose                          |
| -------------------- | -------------------- | -------------------------------- |
| `CODEX_PROFILE_ROOT` | `~/.codex/profiles`  | where profiles live              |
| `CODEX_HOME`         | `~/.codex`           | the codex config dir to write    |

**No-install one-off**

```bash
uvx --from codex-safe-switch codex-safe-switch ls
```

**Install the dev version**

```bash
uv tool install git+https://github.com/kadaliao/codex-safe-switch.git
```

**Releasing**

Pushing a `v*` tag triggers `Publish to PyPI`, which verifies the tag matches `pyproject.toml`, runs tests, builds, runs `twine check`, and uploads.

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

</details>

## License

MIT
