# jusi-opencode

`jusi-opencode` brings the OpenCode CLI into a Jusi/Jusivim notebook as a
structured coding workflow.

It provides a `%%opencode` cell magic and a Jusi display handler. The handler
starts a VisiData runtime, routes follow-up cells to `opencode run --format
json`, stores raw JSON events, captures git diffs and touched-file artifacts,
and exposes completed turns as navigable sheets.

## Installation

```bash
pip install -e ".[dev]"
```

You also need:

- `jusi`
- `opencode`, or an API-compatible fork, available on `PATH`
- Git in project directories where you want diff and touched-file views

## Configuration

Add OpenCode targets to the active Jusi session config:

```toml
[opencode.my_app]
path = "/path/to/my-app"
executable = "opencode"
model = "anthropic/claude-sonnet-4"
variant = "high"
agent = "build"
auto = false
```

For an API-compatible fork that uses stdin plus explicit input/output format
flags:

```toml
[opencode.my_org_app]
path = "/path/to/my-app"
executable = "orgcode"
input_format_arg = "--input-format"
input_format = "text"
output_format_arg = "--output-format"
output_format = "stream-json"
prompt_transport = "stdin"
auto_arg = ""
approval_arg = "--approval-mode"
approval_mode = "auto-edit"
model = "provider/model"
```

Run a cell:

```python
%%opencode my_app
Inspect the failing test and make the smallest fix.
```

You can also use a direct path:

```python
%%opencode /path/to/my-app
Summarize the project.
```

## Magic Arguments

```text
%%opencode TARGET [options]
```

- `TARGET`: named target from `[opencode.TARGET]`, or a filesystem path.
- `-e EXECUTABLE`, `--executable EXECUTABLE`: use an API-compatible OpenCode fork, such as `myorgcode`.
- `-s SESSION`, `--session SESSION`: continue a specific OpenCode session.
- `-c`, `--continue`: continue the latest OpenCode session.
- `--model MODEL`: override the configured model.
- `--variant VARIANT`: set OpenCode model variant/reasoning effort.
- `--agent AGENT`: set OpenCode agent.
- `--auto`: pass OpenCode's auto-approval flag.

Follow-up cells send their text as the next OpenCode prompt. The `/resume`
command opens stored sessions captured by this plugin. Blank cells bootstrap
the runtime without sending work to OpenCode.

The plugin still calls the selected executable with the OpenCode-compatible
noninteractive API:

```bash
EXECUTABLE run --format json ...
```

Targets can override the format flags when a fork uses a different compatible
CLI shape, for example:

```bash
EXECUTABLE run --input-format text --output-format stream-json
```

## State

State is stored under:

```text
$JUSI_STATE_HOME/plugins/opencode/<project-key>/
$XDG_STATE_HOME/jusi/plugins/opencode/<project-key>/
~/.local/state/jusi/plugins/opencode/<project-key>/
```

Each turn stores `prompt.md`, `final.md`, `opencode-events.jsonl`,
`diff.patch`, before/after git snapshots, touched-file artifacts, and metadata.
