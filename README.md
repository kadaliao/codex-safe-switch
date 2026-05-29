# codex-safe-switch

[![PyPI](https://img.shields.io/pypi/v/codex-safe-switch.svg)](https://pypi.org/project/codex-safe-switch/)
[![CI](https://github.com/kadaliao/codex-safe-switch/actions/workflows/ci.yml/badge.svg)](https://github.com/kadaliao/codex-safe-switch/actions/workflows/ci.yml)

中文 | [English](README.en.md)

一键切换 [OpenAI Codex CLI](https://github.com/openai/codex) 的 provider 配置：官方 ChatGPT 登录、第三方 relay、多组 API key，都可以放进 profile 里。提供命令行工具，并可选配 Alfred workflow。

每个 profile 只接管 `~/.codex/config.toml` 里的 provider 片段（model、`[model_providers.*]`、认证方式）。只有确实需要自己认证文件的 profile 才保存 `auth.json`；使用 `env_key` 的 relay 可以继续依赖环境变量，不覆盖官方 ChatGPT 登录缓存。你的本地状态（trusted projects、plugins、marketplaces、MCP servers、TUI 偏好等）会在切换时保留。

## 安装

需要先安装 [`uv`](https://github.com/astral-sh/uv)。

```bash
uv tool install codex-safe-switch
```

安装后会把 `codex-safe-switch` 放到 `$PATH`（默认是 `~/.local/bin/`）。如果 shell 找不到命令，运行一次：

```bash
uv tool update-shell
```

后续升级和卸载：

```bash
uv tool upgrade codex-safe-switch
uv tool uninstall codex-safe-switch
```

### 免安装试用

```bash
uvx --from codex-safe-switch codex-safe-switch ls
```

`uvx` 会为单次调用解析并缓存临时环境，适合快速试用；如果你要频繁使用，尤其是配合 Alfred，建议用 `uv tool install`。

### 安装开发版

想在 PyPI 发版前使用 GitHub 最新提交，可以直接安装仓库版本：

```bash
uv tool install git+https://github.com/kadaliao/codex-safe-switch.git
```

### Alfred（可选）

执行 `uv tool install` 后，双击 `alfred/codex-safe-switch.alfredworkflow` 导入 Alfred。触发关键词是 `cx`。

workflow 默认调用 `$HOME/.local/bin/codex-safe-switch`。如果你的 `uv tool install` 把命令装到了别处，可以用 `uv tool dir --bin` 查看路径，然后修改 workflow plist 里的两个 script block。

## 命令

```text
codex-safe-switch              # 交互式选择器（↑/↓，回车切换，q 取消）
codex-safe-switch ls           # 列出 profiles，★ 表示当前 active
codex-safe-switch current      # 打印当前 active profile
codex-safe-switch official     # 切回官方 OpenAI ChatGPT 登录
codex-safe-switch openai       # `official` 的别名
codex-safe-switch use [name]   # 加载 <name>；不传 name 时进入选择器
codex-safe-switch save <name>  # 把当前 ~/.codex 状态保存成 <name>
codex-safe-switch show <name>  # 打印 <name> 的 provider.toml 和 auth.json key 名
codex-safe-switch state <name> # 查看/设置 profile 的 session-state 作用域
codex-safe-switch restart-codex
                           # 终止 Codex app/server 进程，让配置立即生效
codex-safe-switch merge-history --dry-run
                           # 预览历史 metadata 修复，不写入文件
codex-safe-switch doctor-history
                           # 只读检查当前历史 provider/model 状态
codex-safe-switch rm <name>    # 删除 profile（不允许删除 active profile）
codex-safe-switch alfred-list  # 输出 Alfred Script Filter JSON
```

当 stdin/stdout 不是 TTY（管道、脚本等）时，选择器会自动降级成数字菜单。

## 首次使用

如果 `~/.codex/profiles/` 里还没有 profile，第一次运行 `codex-safe-switch`、`codex-safe-switch ls` 或 Alfred workflow 时，工具会自动导入当前 `~/.codex/config.toml` 的 provider 状态，并按 profile 是否需要自己认证来决定是否保存 `auth.json`。

- 官方 ChatGPT 登录会导入成隐藏的 `official` profile。
- relay/API-key 配置会导入成普通 profile，名字来自 `model_provider`，例如 `relay`。
- 如果 Codex 还没有配置过，CLI 会提示你先正常配置一次 Codex，或者在手动配置 provider 后运行 `codex-safe-switch save <name>`。

如果需要让 Codex 桌面 app 或 app server 立刻读到新配置，可以使用：

```bash
codex-safe-switch use <name> --restart-codex
codex-safe-switch official --restart-codex
codex-safe-switch restart-codex
```

`restart-codex` 会终止匹配到的 Codex app/server 进程，并避开 `codex-safe-switch` 自己。

## 官方 OpenAI 快捷入口

`codex-safe-switch official` 可以一键切回官方 OpenAI ChatGPT 登录。

- 工具会维护隐藏快照：`~/.codex/profiles/.official/`。
- 第一次从官方登录切换到其他 profile 时，会自动刷新这个官方快照。
- 如果你更喜欢按 provider 名称输入，也可以用 `codex-safe-switch openai`。

## 默认共享历史

每次执行 `use` / `official` 后，`codex-safe-switch` 都会自动把本地 Codex 历史 metadata 对齐到当前 provider 和 model identity。

- 正常切换 profile 时不需要再手动记 `merge-history`。
- 在 relay profile 和官方 OpenAI 登录之间切换时，历史会继续可见，包括会按 model id 过滤的界面。
- 如果 `session_index.jsonl` 落后于 SQLite 里的最新 thread，切换时会从 `state_5.sqlite` 补追加索引项，避免移动端历史列表停在旧时间点。
- 如果本机已经开启过 Codex remote-control，切换时还会顺手检查移动端/桌面远程连接依赖的 managed app-server。遇到旧的 unmanaged unix app-server 占住 socket 时会自动结束旧进程并重试；如果 Desktop 还留着旧 SSH remote proxy，会只停止这些 proxy 进程，让下次连接回到当前 MBP；如果缺少官方 standalone 安装，会打印 `curl -fsSL https://chatgpt.com/codex/install.sh | sh` 这种可执行修复命令。
- 如果你只想修复 provider，不想覆盖历史里的 model 值，可以继续用 `merge-history --keep-models`。
- `merge-history --dry-run` 会报告将要更新的 rollout 文件数、行数、SQLite rows，以及会创建的备份路径，但不会写入文件。
- `doctor-history` 是只读诊断命令，会汇总 active profile、当前 provider/model、session-state 模式、SQLite `threads` 分布、最近线程、计划对齐数量和漂移状态。

## Profile 格式

```text
~/.codex/profiles/
├── .active                       # 明文：当前 active profile 名
├── .official/
│   ├── auth.json                 # 切换时完整复制到 ~/.codex/auth.json
│   └── provider.toml             # 官方登录的 provider 片段
└── myrelay/
    ├── auth.json                 # 可选；只有该 profile 自带 API key 或 ChatGPT token 时需要
    └── provider.toml             # 只包含 provider 相关字段（见 examples/）
```

profile 会接管下列顶层 key 和 table；`~/.codex/config.toml` 中其他内容会保留：

- `model`, `model_provider`, `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity`
- `wire_api`, `disable_response_storage`, `preferred_auth_method`
- `[model_providers.*]`

## 添加 relay profile

1. 先按正常方式在 `~/.codex/config.toml` 里配置 relay，并确认 `codex` 可以工作。
2. 如果 relay 用环境变量提供 key，推荐在 provider 里写 `requires_openai_auth = false` 和 `env_key = "..."`。这种 profile 不需要 `auth.json`，切换时会保留当前的官方 ChatGPT 登录缓存，方便 Codex 远程连接继续使用同一个 ChatGPT 账号。
3. 运行 `codex-safe-switch save <name>`，把 provider 片段保存成新 profile。只有 provider 明确需要 OpenAI/ChatGPT auth，或没有声明 `requires_openai_auth = false` 的旧式 API-key 配置，才会保存 `auth.json`。
4. 后续用 Alfred 的 `cx` 或 `codex-safe-switch use <name>` 随时切换。

`requires_openai_auth = false` 只说明这个 relay profile 不需要接管 `auth.json`。移动端能不能持续同步历史，取决于本机的官方 Codex remote-control/app-server 链路是否健康；这条链路需要官方 standalone install，而不是把 token 复制进每个 profile。

也可以手写 profile 文件，参考 `examples/relay-profile/`。

## 环境变量

| 变量                 | 默认值             | 用途                       |
| -------------------- | ------------------ | -------------------------- |
| `CODEX_PROFILE_ROOT` | `~/.codex/profiles` | profiles 存放位置          |
| `CODEX_HOME`         | `~/.codex`          | 要写入的 Codex 配置目录    |

## 发布

PyPI 包发布在 [`codex-safe-switch`](https://pypi.org/project/codex-safe-switch/)。

当前 GitHub Actions 触发规则：

- push 到 `main` 或提交 PR：运行 CI，包含 Python 单测、`uv build`、`twine check`。
- 推送 `v*` tag：运行 `Publish to PyPI` workflow，校验 tag 版本和 `pyproject.toml` 版本一致后，构建并发布到 PyPI。
- Alfred workflow：目前没有 GitHub Actions 自动构建；仓库里提交的是现成的 `alfred/codex-safe-switch.alfredworkflow`。

发版步骤：

1. 更新 `pyproject.toml` 里的 `version`。
2. 运行 `uv run python -m unittest tests.test_cli` 和 `uv build`。
3. 提交版本变更并推送 `main`。
4. 创建并推送匹配的 tag：

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

`Publish to PyPI` workflow 会验证 tag 版本、运行测试、构建 wheel/sdist、检查分发包并发布到 PyPI。需要手动发布时，也可以使用 `uvx twine upload dist/*`。

## License

MIT
