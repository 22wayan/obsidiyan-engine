# Install obsidiyan: runbook for AI coding agents

This file is written for an AI coding agent (Claude Code, Codex, Gemini CLI or similar) that installs obsidiyan for a user. Work through the steps in order. Every step has a command, the expected result and what to do if it fails. Human readers can follow it too.

At the end of the runbook the user has:

- the engine in a local folder with its own virtual environment,
- a vault (a plain folder) that holds the searchable corpus and notes,
- the MCP server registered in one or more agent clients,
- optionally two Claude Code hooks: one that puts matching memories in front of a prompt, one that refreshes the vault when a session ends.

Last run end to end on 2026-10-21 on macOS with Python 3.12, uv 0.12.3, Claude Code 2.1.287, codex-cli 0.163.0 and gemini-cli 0.65.0, in a fresh clone with a throwaway vault. Not covered by that run: Linux and Windows (CI runs the install, `init --demo` and the step 5 checks on Ubuntu), a model answer through Gemini CLI (only registration and connection were checked), and uv downloading a missing Python.

## Rules for the agent

1. **Ask before you touch user data.** Ask the user where the vault should live and which sources to read. Ingesting Claude Code or Codex sessions copies their full text into the vault.
2. **Never point `OBSIDIYAN_HOME` at an existing vault for tests.** Use a throwaway folder from `mktemp -d` for every test run.
3. **Use absolute paths** in every config file and hook. MCP clients and hooks do not start in your shell's working directory.
4. **Run `python -m obsidiyan` from a neutral directory or from the engine folder.** Python imports packages from the current directory first. If the current directory contains another folder named `obsidiyan/` (a second checkout, for example), that copy runs instead of the installed one.
5. **Do not commit `private/`, `corpus/` or `chats/`** of a vault. `init` writes a `.gitignore` that excludes them.
6. Report each step to the user with the actual output, not with "done".

## Variables used below

Set these once in the shell you run the steps in. Replace the example paths with what the user chose.

```bash
ENGINE_DIR="$HOME/obsidiyan-engine"          # where the code lives
export OBSIDIYAN_HOME="$HOME/obsidiyan-vault" # the vault; must be an absolute path
PY="$ENGINE_DIR/.venv/bin/python"            # the interpreter every command uses
```

`OBSIDIYAN_HOME` is the only setting that decides which vault the engine reads and writes (`obsidiyan/paths.py`). If it is unset, the engine treats the folder above the installed package as the vault, which is almost never what you want.

## Step 0: check prerequisites

```bash
python3 --version        # need 3.12 or newer, or uv (below) to provide it
uv --version             # recommended; pip works too
git --version
rg --version             # optional, makes search faster
```

Expected: Python 3.12+ (from `requires-python = ">=3.12"` in `pyproject.toml`), any recent uv, any git. ripgrep is optional: without it, search prints a hint to stderr and falls back to a pure-Python scan with the same results.

Also check which agent clients exist, so you know which of steps 6a to 6c apply:

```bash
claude --version; codex --version; gemini --version
```

If it fails:

- Python older than 3.12 and no uv: install uv (<https://docs.astral.sh/uv/>) or Python 3.12+. With uv, `uv venv --python 3.12` in step 2 uses a uv-managed Python.
- ripgrep missing: `brew install ripgrep`, `apt install ripgrep` or `winget install BurntSushi.ripgrep.MSVC`. You can also skip it.

## Step 1: get the code

```bash
git clone https://github.com/22wayan/obsidiyan-engine "$ENGINE_DIR"
cd "$ENGINE_DIR"
```

Expected: a folder with `obsidiyan/`, `scripts/`, `bin/` and `pyproject.toml`.

Use a clone. The scripts in `scripts/` (verification, refresh) and the SessionEnd hook only exist in a clone, and they look for the interpreter at `.venv/bin/python` inside it.

Alternative without a clone, for the CLI and the MCP server only:

```bash
uv venv --python 3.12 /some/venv
VIRTUAL_ENV=/some/venv uv pip install "obsidiyan[mcp] @ git+https://github.com/22wayan/obsidiyan-engine"
```

With this variant `OBSIDIYAN_HOME` is mandatory, and steps 5 and 7b are not available.

If it fails: check network access to GitHub. If the folder already exists, ask the user whether to reuse it (`git -C "$ENGINE_DIR" pull`) instead of deleting it.

## Step 2: create the virtual environment and install

With uv:

```bash
cd "$ENGINE_DIR"
uv venv --python 3.12
uv pip install -e ".[mcp]"
```

With pip:

```bash
cd "$ENGINE_DIR"
python3.12 -m venv .venv
.venv/bin/pip install -e ".[mcp]"
```

The `mcp` extra is required for the MCP server. Other extras: `dev` (adds pytest, ruff, mypy; needed only to work on the engine) and `embeddings` (optional local embedding model, pulls PyTorch; see the README).

Check:

```bash
"$PY" -c "import obsidiyan, mcp; print('ok')"
```

Expected: `ok`.

If it fails:

- `No module named 'mcp'`: you installed without the extra. Run `uv pip install -e ".[mcp]"` again.
- pip says `File "setup.py" or "setup.cfg" not found. Directory cannot be installed in editable mode`: the venv uses an old Python or pip. Recreate it with Python 3.12+.

## Step 3: create the vault

Ask the user for the vault path. For a first test, use a throwaway folder and the demo data:

```bash
export OBSIDIYAN_HOME="$(mktemp -d)/vault"
cd "$ENGINE_DIR" && "$PY" -m obsidiyan init --demo
```

For the user's real vault, drop `--demo`:

```bash
export OBSIDIYAN_HOME="$HOME/obsidiyan-vault"
cd "$ENGINE_DIR" && "$PY" -m obsidiyan init
```

`init` creates the folder if needed and never overwrites an existing file, so it is safe on a folder that already is a vault.

Expected output (with `--demo`):

```
vault: /path/to/vault
  created BRAIN.md
  created notes/profile.md
  created notes/projects.md
  created notes/knowledge.md
  created private/INDEX.md
  created private/nda-source-overrides.json
  created .obsidian/app.json
  created .gitignore
  created distill/claims.json
  created distill/reviews.json
  initialised a git repository (private/ and corpus/ are ignored)
  demo: 4 conversations ingested

Next: export OBSIDIYAN_HOME=/path/to/vault
```

Without `--demo` the `demo:` line is missing. The git line only appears if git is installed and the folder is not already inside a repository.

If it fails:

- `PermissionError: [Errno 13] Permission denied`: the parent folder is not writable. Pick a path under the user's home.

## Step 4: fill the corpus (the search index)

The corpus is a folder of Markdown files under `$OBSIDIYAN_HOME/corpus/`, one per conversation or note. Search builds a BM25 cache from it under `corpus/.search-index/` by itself; there is no separate index command.

The demo vault from step 3 is already filled. Skip to step 5 for a test run.

**Before the first real ingest, set up confidentiality with the user.** The deny lists `DENY_SLUGS` and `DENY_TERMS` in `obsidiyan/nda.py` contain placeholder names (Acme, Globex). Ask the user which client names and project folders must stay confidential, put them in both lists, and keep `DENY` and `DENY_TERMS` in `bin/sync-memory.sh` in line (a test compares them). Do not commit these names to a public fork. Single conversations can be marked in `$OBSIDIYAN_HOME/private/nda-source-overrides.json`.

Then ingest the sources the user agreed to:

```bash
cd "$ENGINE_DIR"
"$PY" -m obsidiyan ingest --source claude-code   # reads ~/.claude/projects
"$PY" -m obsidiyan ingest --source codex         # reads ~/.codex/sessions
"$PY" -m obsidiyan ingest --source memory        # notes/, memory/ and private/ of the vault
"$PY" -m obsidiyan stats
```

Other sources: `chatgpt`, `gemini` and `claude-web` read exports the user placed under `$OBSIDIYAN_HOME/chats/` (`chats/chatgpt/conversations-*.json`, `chats/gemini/*activity*.json`, `chats/claude/conversations.json`); `course` reads course transcripts. Add `--dry-run` to count without writing, `--session-root <dir>` to read sessions from another folder.

Expected: one line per ingest. The labels are German: `dateien` is files seen, `unveraendert` unchanged, `docs` documents written.

```
claude-code: dateien=4 unveraendert=0 leer=0 docs=4 (clean=3 copyright=0 nda=1) veraltet_entfernt=0
```

A second run only reads new or changed files (`unveraendert` equals `dateien`). `stats` prints `docs=<n> clean=<n> nda=<n>` and a count per source.

`scripts/refresh.sh` runs all seven ingests plus the two checks from step 5 in one go. Note that it also copies Claude Code's auto-memory from `~/.claude/projects/*/memory/` into the vault (`bin/sync-memory.sh`).

If it fails:

- `dateien=0`: the source folder is empty or elsewhere. Pass `--session-root`.
- `PermissionError` while writing under `corpus/`: the vault is not writable by this user.

## Step 5: verify the engine without any agent

```bash
cd "$ENGINE_DIR"
"$PY" -m obsidiyan search Postgres
"$PY" scripts/verify-mcp.py
"$PY" scripts/verify-nda.py
"$PY" scripts/verify-graph.py
```

`verify-mcp.py` starts the MCP server over stdio, lists the tools, runs a search, reads a document and checks that a confidential document does not come back without `include_nda`. Its defaults (`Postgres`, `Acme`) fit the demo vault. On a real vault, pass a word from a clean document and a word that only appears in a confidential one: `"$PY" scripts/verify-mcp.py <clean-word> <confidential-word>`.

Expected on the demo vault:

```
2026-05-04  claude-code  Choose the database for the shop backend
    team only knows SQL, and the hosting provider offers managed Postgres with daily backups. ...
    /path/to/vault/corpus/claude-code/2026/2026-05-04--claude-code--choose-the-database-for-the-shop-backend--a4e416ef.md
...
tools: ['get_doc', 'remember', 'remember_private', 'search', 'timeline']
  2026-05-04  claude-code  Choose the database for the shop backend
  2026-06-20  claude-code  Revisit caching and backups
  get_doc ok, 729 characters
  NDA boundary holds: no confidential document without include_nda
  path traversal rejected
  timeline: [...]
PASS: MCP server works end to end
docs=8 clean=6 copyright=0 nda=2 private=1 leaks=0
PASS: no clean document violates the NDA/privacy policy
graph=PASS nodes=5 parent_edges=4 links=8 components=1
```

All three scripts exit 0 on success and non-zero on failure.

If it fails: see Troubleshooting, items 1 to 3.

## Step 6: register the MCP server

The server is a local stdio process: `"$PY" -m obsidiyan.mcp_server` with `OBSIDIYAN_HOME` in its environment. It offers five tools: `search`, `get_doc`, `timeline`, `remember` and `remember_private`.

Register it under the name `obsidiyan`. The recall hook in step 7a tells the agent to read details with `mcp__obsidiyan__get_doc`, which only matches that name.

### 6a. Claude Code (tested with Claude Code 2.1.287)

```bash
claude mcp add -s user obsidiyan -e OBSIDIYAN_HOME="$OBSIDIYAN_HOME" -- "$PY" -m obsidiyan.mcp_server
claude mcp get obsidiyan
```

`-s user` makes the memory available in every project. Without `-s`, Claude Code uses `local` scope (only the current folder). `-s project` writes `.mcp.json` into the current folder and needs approval in the next interactive `claude` session.

Expected from `claude mcp get obsidiyan`:

```
obsidiyan:
  Scope: User config (available in all your projects)
  Status: ✔ Connected
  Type: stdio
```

Equivalent JSON, for example for `.mcp.json`:

```json
{
  "mcpServers": {
    "obsidiyan": {
      "type": "stdio",
      "command": "/absolute/path/to/obsidiyan-engine/.venv/bin/python",
      "args": ["-m", "obsidiyan.mcp_server"],
      "env": {"OBSIDIYAN_HOME": "/absolute/path/to/vault"}
    }
  }
}
```

### 6b. Codex CLI (tested with codex-cli 0.163.0)

```bash
codex mcp add obsidiyan --env OBSIDIYAN_HOME="$OBSIDIYAN_HOME" -- "$PY" -m obsidiyan.mcp_server
codex mcp list
```

This writes to `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`):

```toml
[mcp_servers.obsidiyan]
command = "/absolute/path/to/obsidiyan-engine/.venv/bin/python"
args = ["-m", "obsidiyan.mcp_server"]

[mcp_servers.obsidiyan.env]
OBSIDIYAN_HOME = "/absolute/path/to/vault"
```

Expected from `codex mcp list`: a row `obsidiyan` with status `enabled`. Codex lists servers without starting them, so this does not prove the server runs; step 8 does.

In interactive Codex the user approves each tool call. Non-interactive `codex exec` refuses MCP calls that need approval (`MCP tool call requires approval, but approval policy is never`). To allow them for this server, add `default_tools_approval_mode = "approve"` under `[mcp_servers.obsidiyan]`, but only if the user agrees.

### 6c. Gemini CLI (tested with gemini-cli 0.65.0)

```bash
gemini mcp add -s user -e OBSIDIYAN_HOME="$OBSIDIYAN_HOME" obsidiyan "$PY" -m obsidiyan.mcp_server
gemini mcp list
```

This writes to `~/.gemini/settings.json` (`-s project` writes `.gemini/settings.json` in the current folder):

```json
{
  "mcpServers": {
    "obsidiyan": {
      "command": "/absolute/path/to/obsidiyan-engine/.venv/bin/python",
      "args": ["-m", "obsidiyan.mcp_server"],
      "env": {"OBSIDIYAN_HOME": "/absolute/path/to/vault"}
    }
  }
}
```

Expected from `gemini mcp list`:

```
✓ obsidiyan: /absolute/path/to/obsidiyan-engine/.venv/bin/python -m obsidiyan.mcp_server (stdio) - Connected
```

Gemini CLI hides all MCP servers, also user-level ones, in folders the user has not trusted, and refuses `-s project` there (`Cannot modify settings in an untrusted workspace`). The user trusts a folder in an interactive `gemini` session.

### 6d. Other MCP clients

Any client that can start a local stdio MCP server needs the same three values: command `"$PY"`, arguments `-m obsidiyan.mcp_server`, environment `OBSIDIYAN_HOME=<vault>`. Only the three clients above were tested.

## Step 7: optional Claude Code hooks

Both hooks go into `~/.claude/settings.json` (all projects) or `.claude/settings.json` of one project. Merge them into an existing `hooks` object; do not replace the user's other hooks. Replace both paths with absolute paths.

### 7a. Recall hook (UserPromptSubmit)

Agents rarely search their memory on their own. `python -m obsidiyan.hook` reads the prompt, and if it contains a recall cue ("do you remember", "last time", "did we", ...) or a project name taken from the vault's note file names, it searches the vault and adds the top three clean hits as context. It never blocks the prompt: every error ends silently with exit code 0. Confidential hits are only counted.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command",
                  "command": "OBSIDIYAN_HOME=/absolute/path/to/vault /absolute/path/to/obsidiyan-engine/.venv/bin/python -m obsidiyan.hook"}]}
    ]
  }
}
```

Check it without Claude Code:

```bash
echo '{"prompt":"do you remember why we chose Postgres for the shop backend?","cwd":"/tmp"}' \
  | "$PY" -m obsidiyan.hook
```

Expected on the demo vault: one JSON line starting with `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "Obsidiyan (Gedaechtnis aus frueheren Sessions), ...` that lists the Postgres session with its doc_id. The context text is German. A prompt without a cue, such as `fix the typo in the README please`, prints nothing.

The hook only speaks Claude Code's hook format. Codex and Gemini CLI are not supported.

### 7b. Refresh when a session ends (SessionEnd)

Needs the clone from step 1.

```json
{
  "hooks": {
    "SessionEnd": [
      {"hooks": [{"type": "command",
                  "command": "OBSIDIYAN_HOME=/absolute/path/to/vault bash /absolute/path/to/obsidiyan-engine/scripts/refresh.sh"}]}
    ]
  }
}
```

`refresh.sh` copies Claude Code's auto-memory into the vault, runs all seven ingests and then `verify-nda.py` and `verify-graph.py`. It exits non-zero if a check fails. To keep a log, append `> /absolute/path/to/refresh.log 2>&1` to the command.

Expected in the log: one ingest line per source, then

```
PASS: no clean document violates the NDA/privacy policy
graph=PASS ...
```

Only one refresh runs at a time. The lock is `${TMPDIR:-/tmp}/obsidiyan-refresh.lock` and is shared by all vaults of the same user, so a running refresh of another vault makes this one print `Refresh laeuft bereits, dieser Lauf wird uebersprungen` (already running, skipped) and exit 0. A lock older than 30 minutes is taken over.

## Step 8: verify through the agent

Do at least one of these. The question fits the demo vault; on a real vault, ask about something the user knows is in it. Name the tool in the prompt: without that, a model often looks at the working directory instead and answers that it has no record. The recall hook (step 7a) exists for exactly this.

Claude Code:

```bash
claude -p "Use the obsidiyan MCP search tool: which database did we choose for the shop backend, and why? Name the doc_id you used." \
  --allowedTools "mcp__obsidiyan__search,mcp__obsidiyan__get_doc"
```

Codex:

```bash
codex exec "Use the obsidiyan MCP search tool: which database did we choose for the shop backend? Give the doc_id."
```

(needs `default_tools_approval_mode = "approve"` from step 6b; otherwise ask the question in an interactive Codex session and approve the tool call)

Expected: the answer names Postgres and gives a reason from the session (the team knows SQL, checkout needs one transaction, managed Postgres with daily backups), plus a doc_id like `claude-code/2026/2026-05-04--claude-code--choose-the-database-for-the-shop-backend--a4e416ef.md`. Codex prints `mcp: obsidiyan/search (completed)` before the answer.

If the agent says it has no information, check that the tool was called at all. Claude Code: add `--output-format stream-json --verbose`; the `init` line lists `{"name": "obsidiyan", "status": "connected"}` and the tool calls appear as `tool_use` entries named `mcp__obsidiyan__search`. Codex: look for the `mcp:` lines. Then go through Troubleshooting.

## Troubleshooting

1. **Search finds nothing, everywhere (wrong vault root).** The CLI prints `no results` and exits 0 when it looks at a folder without a corpus, for example when `OBSIDIYAN_HOME` is unset or points at an empty folder. Run `"$PY" -m obsidiyan stats`: `docs=0` means the engine looks at the wrong place. Set `OBSIDIYAN_HOME` to the vault, the folder that contains `BRAIN.md` and `corpus/`, as an absolute path. In MCP and hook configs the variable must be set in the config itself, not only in your shell.

2. **`No corpus under <vault>/corpus` or `OBSIDIYAN_HOME=... is not a directory` (missing index).** The first message comes from the MCP server, the second from every command. Run `init` (step 3) and an ingest (step 4). The BM25 cache under `corpus/.search-index/` rebuilds itself; deleting it is always safe. A search tool call that fails this way reaches the client only as `Error executing tool search`; the full message is in the server's stderr.

3. **The MCP server does not start or shows as failed.** Run the configured command by hand: `OBSIDIYAN_HOME=<vault> "$PY" -m obsidiyan.mcp_server < /dev/null`. It should exit quietly. Typical errors:
   - `ModuleNotFoundError: No module named 'mcp'`: install the extra (step 2).
   - `VaultError: OBSIDIYAN_HOME=... is not a directory`: the server checks the vault at start-up and exits with code 1. Fix the path.
   - Claude Code shows a different command than you added: a server with the same name exists in another scope. `claude mcp list` warns `defined in multiple scopes`; remove the one you do not want with `claude mcp remove obsidiyan -s <scope>`.
   - Claude Code shows `Pending approval`: a project-scope server waits for approval in the next interactive `claude` session.
   - Gemini CLI lists nothing: the current folder is not trusted (step 6c).
   - `scripts/verify-mcp.py` crashes with `JSONDecodeError`: the server returned an error instead of results, usually item 2.

4. **Wrong interpreter or package copy (venv path).** `No module named obsidiyan` means the command used a Python outside the venv, such as a bare `python3`. Use the absolute path `"$ENGINE_DIR/.venv/bin/python"` in every config. If messages or behaviour differ from this runbook, another `obsidiyan/` folder in the current directory may have been imported instead (rule 4). Check with `"$PY" -c "import obsidiyan; print(obsidiyan.__file__)"` from the same directory.

5. **Permissions.** `PermissionError: [Errno 13]` during `init` or `ingest` means the vault folder, or `corpus/` inside it, is not writable by the user that runs the command. Hooks and MCP servers run as the logged-in user, so `chown` the vault to that user instead of using `sudo`. The SessionEnd hook also needs write access to `${TMPDIR:-/tmp}` for its lock.

6. **A result you know exists does not show up.** It may be confidential. Try `"$PY" -m obsidiyan search <term> --include-nda`, then check the deny lists in `obsidiyan/nda.py` and `private/nda-source-overrides.json`.

## Uninstall

Remove the registrations and hooks first, then the code. The vault holds the user's data: delete it only if the user says so explicitly.

```bash
claude mcp remove obsidiyan -s user     # use the scope you added it with
codex mcp remove obsidiyan
gemini mcp remove -s user obsidiyan
```

Expected: `Removed MCP server obsidiyan from user config`, `Removed global MCP server 'obsidiyan'.` and `Server "obsidiyan" removed from user settings.`

Then delete the `UserPromptSubmit` and `SessionEnd` entries that call `obsidiyan.hook` or `refresh.sh` from the Claude Code settings file you added them to, and remove the code:

```bash
rm -rf "$ENGINE_DIR"            # code and venv only
rm -rf "$OBSIDIYAN_HOME"        # the vault: only with explicit consent
```

If you installed from the git URL instead of a clone, delete that venv.
