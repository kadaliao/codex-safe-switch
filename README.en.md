# codex-profile-switcher

[![PyPI](https://img.shields.io/pypi/v/codex-profile-switcher.svg)](https://pypi.org/project/codex-profile-switcher/)
[![CI](https://github.com/kadaliao/codex-profile-switcher/actions/workflows/ci.yml/badge.svg)](https://github.com/kadaliao/codex-profile-switcher/actions/workflows/ci.yml)

[中文](README.md) | English

One-key switch between [OpenAI Codex CLI](https://github.com/openai/codex) configurations — official ChatGPT login, third-party relays, multiple API keys, whatever. CLI + optional Alfred workflow.

Each profile owns the *provider* slice of `~/.codex/config.toml` (model, `[model_providers.*]`, auth method). A profile stores `auth.json` only when it really owns an auth file; relays that use `env_key` can rely on environment variables and leave the official ChatGPT login cache in place. Your local state (trusted projects, plugins, marketplaces, MCP servers, TUI prefs) is left untouched on every switch.

## Install

Requires [`uv`](https://github.com/astral-sh/uv).

```bash
uv tool install codex-profile-switcher
```

This puts `codex-switch` on `$PATH` (default `~/.local/bin/`). Run `uv tool update-shell` once if your shell can't find it.

Upgrade later with `uv tool upgrade codex-profile-switcher`; uninstall with `uv tool uninstall codex-profile-switcher`.

### No-install (one-off)

```bash
uvx --from codex-profile-switcher codex-switch ls
```

`uvx` resolves and caches an ephemeral environment per invocation. Convenient for trying it out, slower for hot paths like Alfred — use `uv tool install` if you want the workflow to feel snappy.

### Development version

Install directly from GitHub when you want the latest commit before it is released to PyPI:

```bash
uv tool install git+https://github.com/kadaliao/codex-profile-switcher.git
```

### Alfred (optional)

After `uv tool install`, double-click `alfred/codex-profile-switcher.alfredworkflow`. Trigger with keyword `cx`.

The workflow calls `$HOME/.local/bin/codex-switch`; if `uv tool install` put the binary elsewhere (`uv tool dir --bin` to check), edit the two `script` blocks in the workflow's plist accordingly.

## CLI

```text
codex-switch              # interactive picker (↑/↓, enter to switch, q to cancel)
codex-switch ls           # list profiles, ★ marks the active one
codex-switch current      # print the active profile
codex-switch official     # switch back to official OpenAI ChatGPT login
codex-switch openai       # alias of `official`
codex-switch use [name]   # load <name>; omit for the picker
codex-switch save <name>  # snapshot the current ~/.codex state as <name>
codex-switch show <name>  # print <name>'s provider.toml + auth.json key names
codex-switch state <name> # show/set the session-state scope for a profile
codex-switch restart-codex
                           # terminate Codex app/server processes so config changes take effect
codex-switch merge-history --dry-run
                           # preview history metadata changes without writing files
codex-switch doctor-history
                           # inspect current history provider/model state read-only
codex-switch rm <name>    # delete profile (the active one is protected)
codex-switch alfred-list  # JSON for Alfred Script Filter
```

The picker auto-falls back to a numeric menu when stdin/stdout aren't TTYs (pipes, scripts).

## First run

If `~/.codex/profiles/` has no profiles yet, `codex-switch` automatically imports the current
`~/.codex/config.toml` provider state the first time you run `codex-switch`, `codex-switch ls`,
or the Alfred workflow, and stores `auth.json` only when that profile owns its own auth.

- Official ChatGPT login is imported as the hidden `official` profile.
- Relay/API-key configs are imported as a regular profile named from `model_provider` (for example `relay`).
- If Codex has not been configured yet, the CLI explains that you need to configure Codex once or run
  `codex-switch save <name>` after setting up the provider manually.

When you need the Codex desktop app or app server to pick up a switch immediately, use:

```bash
codex-switch use <name> --restart-codex
codex-switch official --restart-codex
codex-switch restart-codex
```

The restart command terminates matching Codex app/server processes while avoiding `codex-switch` itself.

## Official OpenAI shortcut

`codex-switch official` is the one-step way back to the official OpenAI ChatGPT login.

- The tool keeps a hidden `~/.codex/profiles/.official/` snapshot for the official config/auth.
- The first time you switch away from an official OpenAI session, that snapshot is refreshed automatically.
- `codex-switch openai` is an alias if you prefer typing the provider name directly.

## Shared history by default

After every `use` / `official` switch, `codex-switch` automatically aligns local Codex history metadata to the active provider and model identity.

- You no longer need to remember `merge-history` during normal profile switching.
- This keeps session history visible when moving between relay profiles and the official OpenAI login, including surfaces that filter by model id.
- `merge-history --keep-models` still exists if you want a provider-only repair and need to preserve historical per-thread model ids.
- `merge-history --dry-run` reports rollout files/lines, SQLite rows, and the backup path it would create without writing anything.
- `doctor-history` is read-only and summarizes the active profile, current provider/model, session-state mode, SQLite `threads` distribution, recent threads, planned alignment counts, and provider/model drift.

## Profile format

```text
~/.codex/profiles/
├── .active                       # plaintext: name of the active profile
├── chatgpt-official/
│   ├── auth.json                 # full file copied into ~/.codex/auth.json
│   └── provider.toml             # empty = use ChatGPT login
└── myrelay/
    ├── auth.json                 # optional; only needed when the profile owns a key/token
    └── provider.toml             # only provider-related keys (see examples/)
```

The following top-level keys + tables are owned by a profile (swapped on `use`); everything else in `~/.codex/config.toml` is preserved:

- `model`, `model_provider`, `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity`
- `wire_api`, `disable_response_storage`, `preferred_auth_method`
- `[model_providers.*]`

## Adding a relay profile

1. Configure the relay normally in `~/.codex/config.toml` and verify `codex` works.
2. For a relay whose key comes from the environment, prefer `requires_openai_auth = false` plus `env_key = "..."`. That profile does not need `auth.json`; switching to it preserves the current official ChatGPT login cache so Codex remote connections can keep using the same ChatGPT account.
3. `codex-switch save <name>` — snapshots the provider slice into a new profile. It only stores `auth.json` when the provider explicitly needs OpenAI/ChatGPT auth, or for legacy API-key configs that do not declare `requires_openai_auth = false`.
4. `cx` in Alfred (or `codex-switch use <name>`) to switch anytime.

Or build the files by hand — see `examples/relay-profile/`.

## Env overrides

| Var                  | Default              | Purpose                           |
| -------------------- | -------------------- | --------------------------------- |
| `CODEX_PROFILE_ROOT` | `~/.codex/profiles`  | where profiles live               |
| `CODEX_HOME`         | `~/.codex`           | the codex config dir to write     |

## Releasing

Packages are published on PyPI as [`codex-profile-switcher`](https://pypi.org/project/codex-profile-switcher/).

Current GitHub Actions triggers:

- Pushes to `main` and pull requests run CI: Python unit tests, `uv build`, and `twine check`.
- Pushing a `v*` tag runs the `Publish to PyPI` workflow. It verifies the tag matches `pyproject.toml`, builds the package, and publishes to PyPI.
- The Alfred workflow is not built by GitHub Actions today; the repository commits the ready-to-import `alfred/codex-profile-switcher.alfredworkflow` file.

1. Update `version` in `pyproject.toml`.
2. Run `uv run python -m unittest tests.test_cli` and `uv build`.
3. Commit the version bump and push `main`.
4. Create and push a matching tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

The `Publish to PyPI` workflow verifies that the tag version matches `pyproject.toml`, runs tests, builds the wheel and sdist, checks the distributions, and publishes them to PyPI. Manual local publishing is still possible with `uvx twine upload dist/*` when needed.

## License

MIT
