# termlog

**Let your AI agent read what happened in your terminal.**

Run `term backend`, work as usual, then tell your agent: `termlog backend`.
Commands and output are recorded locally, including output from SSH sessions.
Each terminal gets a name, so you can keep backend, frontend, and remote work separate.

## Install

Requires Python 3.10+ and Git. Works on macOS, Linux, and Windows 10/11.
Windows uses ConPTY through pywinpty; macOS and Linux use a POSIX pseudo-terminal.

With [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install git+https://github.com/owenlsx/termlog.git
```

Or with [pipx](https://pipx.pypa.io/stable/installation/):

```sh
pipx install git+https://github.com/owenlsx/termlog.git
```

Both install `term` and `termlog` into your user command directory. If the commands
are not found, run `uv tool update-shell` or `pipx ensurepath`, then reopen your terminal.
The distribution is named `termlog-cli`; it is installed from this repository, not PyPI.

## Use

```sh
term backend                 # record your normal shell
term                         # name defaults to current directory
termlog                      # list recordings and live status
termlog backend              # last 200 lines, terminal escapes removed
termlog backend 50           # last 50 lines
termlog --all 60             # last 60 lines from each live session
termlog --grep 'error|fail'   # case-insensitive regex across all recordings
term --stop backend          # end the recorded shell from another terminal
termlog --clean 7            # remove ended recordings idle for over 7 days
term --command python       # record a specific program
```

Type `exit` to end the recorded shell and return to your original terminal.
On macOS/Linux you can also press Ctrl-D. `term --stop` terminates the recorded
program; save your work first.

The default shell is `$SHELL` on macOS/Linux and PowerShell 7 (`pwsh`) when available,
otherwise `%COMSPEC%` (normally cmd.exe), on Windows. Use `--command` last to choose
another shell, for example `term work --command powershell.exe -NoLogo`.

## Storage and behaviour

- Logs live in `~/.termlog`. Set `TERMLOG_DIR` to choose another local directory.
- Restarting a name appends to its history. Logs over 50 MiB move to
  `archive/<name>.prev.log` on restart, replacing that name's previous archive.
- Concurrent use of the same name and nested recordings are refused.
- OS file locks identify live recorders. Stop requests carry a session token,
  avoiding signals to stale or reused process IDs.
- Starting a recording warns if other ended sessions have logs idle for over seven
  days, suggesting `termlog --clean 7`. The session being resumed is excluded.
- Cleanup is explicit and skips live recordings. Lock files are intentionally kept.
- `TERMLOG_SESSION` is available inside the recorded shell for custom prompts.
- Tail reads are bounded to the final 400 KB. Search streams the complete log.
- Logs contain displayed terminal output, not a structured command history or a
  full terminal-screen replay. Cursor movement is stripped, not reconstructed.

Anything displayed can be recorded, including secrets printed by commands. Logs
stay on your computer; share them with an agent only when appropriate. Hidden
password input is not independently logged.

## Migrating the original shell scripts

End sessions started by the original scripts first. Install this package, then
remove or rename the old `term` and `termlog` executables if they shadow the new
commands (`which -a term termlog` on macOS/Linux; `where.exe term` on Windows).
Existing `.log` files remain readable and resumable. Old `.meta` files are not used
for live status. No shell startup files are changed by installation.

## Update / uninstall

```sh
uv tool upgrade termlog-cli
uv tool uninstall termlog-cli
```

For pipx, use `pipx upgrade termlog-cli` / `pipx uninstall termlog-cli`.
Uninstalling keeps your logs.

## Development

```sh
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

GitHub Actions runs recording, append, search, exit-status, duplicate-session,
stop, cleanup, and validation tests on macOS, Ubuntu, and Windows with Python
3.10 and 3.13. Interactive terminal behaviour should also be checked manually
when changing the terminal backends.

Originally built from Owen's personal `term` / `termlog` shell commands.

No licence is granted for this repository. All rights reserved.
