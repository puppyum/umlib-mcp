#!/usr/bin/env bash
# Install umlib-mcp and register it with whichever AI coding agents you have.
#
#   curl -LsSf https://raw.githubusercontent.com/puppyum/umlib-mcp/main/install.sh | bash
#
# Flags:
#   --dry-run   show what would change, touch nothing
#   --help      this message
set -euo pipefail

REPO="https://github.com/puppyum/umlib-mcp"
REF="main"
NAME="umlib"
DRY_RUN=0
STAMP="$(date +%Y%m%d%H%M%S)"
REGISTERED=()
SKIPPED=()

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --help|-h) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }
run()  { if [ "$DRY_RUN" = 1 ]; then say "would run: $*"; else "$@"; fi; }

# ---------------------------------------------------------------- prerequisites
head_ "Checking prerequisites"

if command -v uv >/dev/null 2>&1; then
  say "uv found: $(command -v uv)"
else
  say "uv not found, installing from astral.sh"
  if [ "$DRY_RUN" = 1 ]; then
    say "would run: curl -LsSf https://astral.sh/uv/install.sh | sh"
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # the installer drops uv in one of these; make it visible to this script
    for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
      [ -x "$d/uv" ] && export PATH="$d:$PATH"
    done
  fi
fi

if ! command -v uv >/dev/null 2>&1 && [ "$DRY_RUN" = 0 ]; then
  echo "uv install failed; see https://docs.astral.sh/uv/ and re-run" >&2
  exit 1
fi

# ---------------------------------------------------------------- install server
head_ "Installing umlib-mcp"
# A real installed binary (not `uvx --from git+...`) is what makes this work
# everywhere: one fixed absolute path, no dependency resolution at launch, so
# agents with short startup timeouts and GUI apps with a narrow PATH both work.
run uv tool install --force "git+${REPO}@${REF}"

if [ "$DRY_RUN" = 1 ]; then
  BIN="$HOME/.local/bin/umlib-mcp"
  say "would install to: $BIN"
else
  BIN_DIR="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
  BIN="$BIN_DIR/umlib-mcp"
  if [ ! -x "$BIN" ]; then
    echo "expected the umlib-mcp binary at $BIN but it is not there" >&2
    exit 1
  fi
  say "installed: $BIN"
fi

# ------------------------------------------------------------- json registration
# Agents disagree on the top-level key: VS Code uses "servers" and wants an
# explicit type; Zed uses "context_servers"; everyone else uses "mcpServers".
register_json() {
  local label="$1" file="$2" key="$3" want_type="$4"
  local dir; dir="$(dirname "$file")"
  [ -d "$dir" ] || { SKIPPED+=("$label (not installed)"); return; }

  if [ "$DRY_RUN" = 1 ]; then
    say "would register in $file (key: $key)"
    REGISTERED+=("$label")
    return
  fi

  [ -f "$file" ] && cp "$file" "$file.bak-$STAMP"
  # a broken config for one agent must not abort the whole install
  if ! BIN="$BIN" FILE="$file" KEY="$key" WANT_TYPE="$want_type" NAME="$NAME" \
    uv run --no-project python - <<'PY'
import json, os, pathlib
path = pathlib.Path(os.environ["FILE"])
key, name = os.environ["KEY"], os.environ["NAME"]
data = {}
if path.exists() and path.stat().st_size:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        raise SystemExit(f"! {path} is not valid JSON; leaving it alone")
entry = {"command": os.environ["BIN"], "args": []}
if os.environ["WANT_TYPE"] == "1":
    entry["type"] = "stdio"
data.setdefault(key, {})[name] = entry          # idempotent: replaces any old entry
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2) + "\n")
PY
  then
    SKIPPED+=("$label (config unreadable, left untouched)")
    return
  fi
  say "registered in $file"
  REGISTERED+=("$label")
}

head_ "Registering with your agents"

# Claude Code — its own CLI keeps the config in the right scope
if command -v claude >/dev/null 2>&1; then
  if [ "$DRY_RUN" = 1 ]; then
    say "would run: claude mcp add $NAME -s user -- $BIN"
  else
    claude mcp remove "$NAME" -s user >/dev/null 2>&1 || true
    claude mcp add "$NAME" -s user -- "$BIN" >/dev/null && say "registered with Claude Code"
  fi
  REGISTERED+=("Claude Code")
else
  SKIPPED+=("Claude Code (not installed)")
fi

# Codex — `codex mcp add` needs a bare -- before the command, and overwrites cleanly
if command -v codex >/dev/null 2>&1; then
  if [ "$DRY_RUN" = 1 ]; then
    say "would run: codex mcp add $NAME -- $BIN"
  else
    codex mcp add "$NAME" -- "$BIN" >/dev/null && say "registered with Codex"
  fi
  REGISTERED+=("Codex")
else
  SKIPPED+=("Codex (not installed)")
fi

# Gemini CLI — prefer its CLI, fall back to settings.json
if command -v gemini >/dev/null 2>&1; then
  if [ "$DRY_RUN" = 1 ]; then
    say "would run: gemini mcp add -s user $NAME $BIN"
  else
    gemini mcp add -s user "$NAME" "$BIN" >/dev/null 2>&1 && say "registered with Gemini CLI"
  fi
  REGISTERED+=("Gemini CLI")
elif [ -f "$HOME/.gemini/settings.json" ]; then
  register_json "Gemini CLI" "$HOME/.gemini/settings.json" "mcpServers" 0
else
  SKIPPED+=("Gemini CLI (not installed)")
fi

register_json "Cursor"         "$HOME/.cursor/mcp.json"                                  "mcpServers"      0
register_json "Windsurf"       "$HOME/.codeium/windsurf/mcp_config.json"                 "mcpServers"      0
register_json "Zed"            "$HOME/.config/zed/settings.json"                         "context_servers" 0
register_json "VS Code"        "$HOME/Library/Application Support/Code/User/mcp.json"    "servers"         1
register_json "Claude Desktop" "$HOME/Library/Application Support/Claude/claude_desktop_config.json" "mcpServers" 0

# ------------------------------------------------------------------------ report
head_ "Done"
if [ ${#REGISTERED[@]} -gt 0 ]; then
  say "registered with: $(printf '%s, ' "${REGISTERED[@]}" | sed 's/, $//')"
else
  say "no agents detected; add this command to any MCP client by hand:"
  say "  $BIN"
fi
[ ${#SKIPPED[@]} -gt 0 ] && say "skipped: $(printf '%s, ' "${SKIPPED[@]}" | sed 's/, $//')"

cat <<EOF

Restart your agent, then ask it:

    log me into the library proxy

Sign in with your university account in the window that opens, then ask for a
paper by DOI or title. Upgrade later with:  uv tool upgrade umlib-mcp --reinstall

Claude Code and Codex pick up the usage rules automatically from the bundled
skill. For other agents, paste this into your project instructions
(CLAUDE.md, AGENTS.md, .cursorrules, or the equivalent) so the agent reaches
for it on its own:

    When searching for or downloading scholarly articles, papers, books or
    other publications, use the umlib tools. If content looks inaccessible -
    paywalled, login-walled, 403, or abstract-only - try umlib's fetch_pdf
    before concluding it is unavailable, since it may be accessible through
    the authenticated library session. Fetch one paper at a time.
EOF
