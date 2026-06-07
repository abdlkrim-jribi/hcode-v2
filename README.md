# HCode v2

**An autonomous AI coding agent for the terminal, built on [DeepAgents](https://github.com/langchain-ai/deepagents) + [LangGraph](https://github.com/langchain-ai/langgraph) + [LangChain](https://github.com/langchain-ai/langchain).**

HCode v2 is a thin, opinionated CLI shell over the DeepAgents harness. DeepAgents
supplies the agent loop, sub-agents, context management, persistence, and tooling
primitives; HCode adds a Plan‑Execute‑Verify (PEV) workflow, a safety guard,
a skills library, workflow files, an MCP client, and 31 coding tools wired in as
LangChain tools.

It is model-agnostic: it talks to any OpenAI-compatible endpoint (including
self-hosted `gpt-oss` models) or to Anthropic.

---

## Install

HCode v2 uses [`uv`](https://docs.astral.sh/uv/) for environment and dependency
management. The DeepAgents library is **vendored** under `libs/deepagents/` and
installed as an editable path dependency (see [Vendored dependency](#vendored-dependency)).

```bash
# clone, then from the repo root:
uv sync                     # create .venv and install everything (incl. vendored deepagents)
uv run hcode version        # smoke-check the install
```

The console scripts `hcode` and `hcode_v2` are equivalent entry points.

---

## Quick start

`uv sync` installs the `hcode` console script into the repo-root `.venv`, but that
venv is not on your `PATH`, so a bare `hcode` won't be found from a fresh clone.
Pick whichever of these is most convenient — simplest first:

**1. Activate the venv once (then just `hcode`):**

```powershell
.venv\Scripts\Activate.ps1   # PowerShell; for cmd use .venv\Scripts\activate.bat
hcode version
hcode chat
hcode run "create hello.py that prints 'hello from hcode v2'"
```

**2. No activation — use the bundled shim:**

```powershell
.\scripts\hcode.cmd version            # cmd / PowerShell
.\scripts\hcode.ps1 chat               # PowerShell
.\scripts\hcode.cmd run "create hello.py that prints 'hello from hcode v2'"
```

The shims (`scripts/hcode.cmd`, `scripts/hcode.ps1`) just forward all arguments to
`.venv\Scripts\hcode.exe`, so they work from a fresh clone as soon as the venv is
built.

**3. Last resort — the long, fully-qualified form:**

```powershell
.venv\Scripts\python.exe -m hcode_v2.cli.main version
```

All three reach the same CLI: `hcode version`, `hcode chat`, and
`hcode run "<task>"` behave identically.

---

## Configuration

Configuration is read from environment variables, loaded from a `.env` file at the
repo root (never commit secrets — `.env` is git-ignored; use `.env.example` as a
template).

| Variable            | Purpose                                                        | Default        |
| ------------------- | -------------------------------------------------------------- | -------------- |
| `HCODE_MODEL`       | Model name/string (e.g. `gpt-4o-mini`, `gpt-oss-120b`)         | `gpt-4o-mini`  |
| `OPENAI_API_KEY`    | API key for the OpenAI-compatible endpoint                     | —              |
| `OPENAI_BASE_URL`   | Base URL of the endpoint (set this for self-hosted / `gpt-oss`)| OpenAI default |
| `ANTHROPIC_API_KEY` | Anthropic key — used instead of OpenAI when set and no `OPENAI_API_KEY` | — |
| `HCODE_MAX_TOKENS`  | Max output tokens per call                                     | `2000`         |

> The model layer prefers OpenAI-compatible config. If only `ANTHROPIC_API_KEY` is
> set, HCode uses `ChatAnthropic`; otherwise it uses `ChatOpenAI` (honouring
> `OPENAI_BASE_URL` so any compatible endpoint works).

Example `.env`:

```dotenv
HCODE_MODEL=gpt-oss-120b
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://your-endpoint.example.com/v1
```

---

## Commands

```text
hcode run <task>        Run a single task and print the result
hcode chat              Interactive multi-turn session (persists across turns)
hcode mcp <action>      Manage MCP servers: list | known | connect <name>
hcode skill             List available skills from .hcode/skills/
hcode workflow          List available workflows from .hcode/workflows/
hcode version           Print the version string
```

Common options:

| Command | Option | Meaning |
| ------- | ------ | ------- |
| `run`   | `--workdir, -C <dir>` | Change into `<dir>` before running (point at any project) |
| `run`   | `--fast`              | Skip planning — execute in one shot |
| `run`   | `--no-pev`            | Disable the Plan‑Execute‑Verify loop |
| `chat`  | `--workdir, -C <dir>` | Change into `<dir>` before starting the session |
| `chat`  | `--session, -s <id>`  | Resume a previous session by id |
| `skill` / `workflow` | `--dir <path>` | Override the lookup directory |

Because the agent's shell, session store (`.hcode/sessions/`), and skill/workflow
lookups are all relative to the working directory, `--workdir` is enough to aim
HCode at any project without `cd`-ing first.

---

## Demo sequence

Point HCode at a project, run a task, and confirm the file lands:

```bash
# 1. create or pick a project directory
mkdir -p /tmp/demo && cd /tmp/demo

# 2. ask HCode to create a file (equivalently: hcode run -C /tmp/demo "...")
uv run hcode run "create hello.py that prints 'hello from hcode v2'"

# 3. verify the file is really there
cat hello.py
python hello.py
```

For an interactive session against the same project:

```bash
uv run hcode chat -C /tmp/demo
you> add a function add(a, b) to hello.py and call it
you> /exit
```

---

## Architecture (one paragraph)

`hcode_v2.cli.main` (Click) is the entry point. `hcode_v2.agent.factory.create_hcode_agent`
assembles a DeepAgents agent via `create_deep_agent(...)` with: the model from
`_build_model` (in `src/hcode_v2/agent/factory.py`), the 31 tools from
`hcode_v2.tools.registry.get_all_tools`, a middleware stack
(`PEVMiddleware`, `SafetyGuardMiddleware`, `HCodeSkillsMiddleware`, `WorkflowMiddleware`),
a `LocalShellBackend`, and an `HCodeSQLiteCheckpointer` for session persistence.
Streaming, sub-agents, context summarization, and human-in-the-loop come from
DeepAgents itself.

---

## Vendored dependency

DeepAgents is **not** pulled from PyPI or a git ref — it is vendored as committed
files under `libs/deepagents/` and installed editable via
`[tool.uv.sources]` in `pyproject.toml`. Because the source is committed into this
repository, upstream cannot silently shift under us.

| | |
| --- | --- |
| Package | `deepagents` |
| Version | `0.6.3` |
| Upstream | [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) |
| Frozen at (hcode-v2 commit) | `b57a6d2` — last commit to touch `libs/deepagents/` |

To intentionally update the vendored copy, re-vendor the upstream tree, bump the
version here, and record the new freeze commit.

---

## Development

```bash
uv sync                              # install runtime deps + vendored deepagents
uv run --group dev pytest            # run the HCode v2 shell test suite
```

- Tests live in `tests/` and must not make network calls.
- `asyncio_mode = "auto"` is set, so async tests need no `@pytest.mark.asyncio`.
- Commits follow Conventional Commits with a required scope (see `AGENTS.md`).

---

## License

MIT — see [LICENSE](LICENSE).
