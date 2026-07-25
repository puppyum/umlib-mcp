#!/usr/bin/env bash
# Remove umlib-mcp and every registration the installer made.
#
#   curl -LsSf https://raw.githubusercontent.com/puppyum/umlib-mcp/main/uninstall.sh | bash
#
# Flags:
#   --keep-session  leave ~/.umlib in place (your saved library sign-in)
#   --dry-run       show what would change, touch nothing
#   --help          this message
set -euo pipefail

NAME="umlib"
DRY_RUN=0
KEEP_SESSION=0
REMOVED=()
MISSING=()

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --keep-session) KEEP_SESSION=1 ;;
    --help|-h)
      # $0 is the shell itself when piped from curl, so print rather than read
      cat <<'USAGE'
Remove umlib-mcp and every registration the installer made.

  curl -LsSf https://raw.githubusercontent.com/puppyum/umlib-mcp/main/uninstall.sh | bash

Flags:
  --keep-session  leave ~/.umlib in place (your saved library sign-in)
  --dry-run       show what would change, touch nothing
  --help          this message

When piping, pass flags after -s --, e.g. ... | bash -s -- --dry-run
USAGE
      exit 0 ;;
    *) echo "unknown flag: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ------------------------------------------------------------------ json edit
# Only ever removes the "umlib" key. Other servers in the same file are left
# alone, and the file is deleted only when umlib was the single thing in it.
unregister_json() {
  local label="$1" file="$2" key="$3"
  [ -f "$file" ] || { MISSING+=("$label"); return; }
  grep -q '"umlib"' "$file" 2>/dev/null || { MISSING+=("$label"); return; }

  if [ "$DRY_RUN" = 1 ]; then
    say "would remove the umlib entry from $file"
    REMOVED+=("$label")
    return
  fi

  local out
  if ! out="$(FILE="$file" KEY="$key" NAME="$NAME" python3 - 2>/dev/null <<'PY'
import json, os, pathlib
path = pathlib.Path(os.environ["FILE"])
key, name = os.environ["KEY"], os.environ["NAME"]
try:
    data = json.loads(path.read_text())
except (json.JSONDecodeError, OSError):
    # a config with // comments is not JSON; rewriting it would throw the
    # comments away, so say so and let the user take the one line out.
    # the marker goes to stdout, not into SystemExit, which prints to stderr
    print("manual")
    raise SystemExit(1)
if not isinstance(data, dict) or name not in data.get(key, {}):
    print("absent")
    raise SystemExit(1)
del data[key][name]
# if umlib was the only thing in the file, the installer created it: take the
# whole file rather than leaving an empty shell behind. Otherwise keep every
# other setting exactly where it was.
if not data[key] and list(data) == [key]:
    path.unlink()
    print("file")
else:
    path.write_text(json.dumps(data, indent=2) + "\n")
    print("entry")
PY
  )"; then
    case "$out" in
      manual) say "! $file is JSON with comments; remove the \"umlib\" block by hand"
              REMOVED+=("$label (needs a manual edit)") ;;
      *)      MISSING+=("$label") ;;
    esac
    return
  fi
  [ "$out" = "file" ] && say "removed $file (it held nothing else)" || say "removed the umlib entry from $file"
  REMOVED+=("$label")
}

head_ "Removing registrations"

# Claude Code: plugin first, then a plain MCP registration, then the marketplace
if command -v claude >/dev/null 2>&1; then
  if [ "$DRY_RUN" = 1 ]; then
    say "would run: claude plugin uninstall umlib@umlib-lab; claude mcp remove $NAME -s user"
    REMOVED+=("Claude Code")
  else
    claude plugin uninstall "umlib@umlib-lab" >/dev/null 2>&1 || true
    claude plugin marketplace remove "umlib-lab" >/dev/null 2>&1 || true
    if claude mcp remove "$NAME" -s user >/dev/null 2>&1; then
      say "removed from Claude Code"; REMOVED+=("Claude Code")
    else
      MISSING+=("Claude Code")
    fi
  fi
else
  MISSING+=("Claude Code (not installed)")
fi

# Codex reads the same plugin marketplace format as Claude Code
if command -v codex >/dev/null 2>&1; then
  if [ "$DRY_RUN" = 1 ]; then
    say "would run: codex plugin uninstall umlib@umlib-lab; codex mcp remove $NAME"
    REMOVED+=("Codex")
  else
    codex plugin uninstall "umlib@umlib-lab" >/dev/null 2>&1 || true
    codex plugin marketplace remove "umlib-lab" >/dev/null 2>&1 || true
    if codex mcp remove "$NAME" >/dev/null 2>&1; then
      say "removed from Codex"; REMOVED+=("Codex")
    else
      MISSING+=("Codex")
    fi
  fi
else
  MISSING+=("Codex (not installed)")
fi

if command -v gemini >/dev/null 2>&1 && [ "$DRY_RUN" = 0 ]; then
  gemini mcp remove "$NAME" >/dev/null 2>&1 && { say "removed from Gemini CLI"; REMOVED+=("Gemini CLI"); }
fi

case "$(uname -s)" in
  Darwin) APP_SUPPORT="$HOME/Library/Application Support" ;;
  *)      APP_SUPPORT="${XDG_CONFIG_HOME:-$HOME/.config}" ;;
esac

unregister_json "Gemini CLI"     "$HOME/.gemini/settings.json"                         "mcpServers"
unregister_json "Cursor"         "$HOME/.cursor/mcp.json"                              "mcpServers"
unregister_json "Windsurf"       "$HOME/.codeium/windsurf/mcp_config.json"             "mcpServers"
unregister_json "Zed"            "${XDG_CONFIG_HOME:-$HOME/.config}/zed/settings.json" "context_servers"
unregister_json "VS Code"        "$APP_SUPPORT/Code/User/mcp.json"                     "servers"
unregister_json "Claude Desktop" "$APP_SUPPORT/Claude/claude_desktop_config.json"      "mcpServers"

# ------------------------------------------------------------------ the server
head_ "Removing the server"
if command -v uv >/dev/null 2>&1; then
  if [ "$DRY_RUN" = 1 ]; then
    say "would run: uv tool uninstall umlib-mcp"
  elif uv tool uninstall umlib-mcp >/dev/null 2>&1; then
    say "uninstalled umlib-mcp"
  else
    say "umlib-mcp was not installed as a uv tool"
  fi
else
  say "uv not found, so there is no uv-installed binary to remove"
fi

# ----------------------------------------------------------------- the session
head_ "Your saved library session"
PROFILE="${UMLIB_PROFILE_DIR:-$HOME/.umlib/profile}"
if [ "$KEEP_SESSION" = 1 ]; then
  say "kept: $PROFILE (--keep-session)"
elif [ ! -e "$HOME/.umlib" ]; then
  say "nothing saved"
elif [ "$DRY_RUN" = 1 ]; then
  say "would remove $HOME/.umlib (your saved sign-in; --keep-session to keep it)"
else
  rm -rf "$HOME/.umlib"
  say "removed $HOME/.umlib, so the next install starts with a fresh sign-in"
fi

# ------------------------------------------------------------------------ done
head_ "Done"
[ ${#REMOVED[@]} -gt 0 ] && say "removed from: $(printf '%s, ' "${REMOVED[@]}" | sed 's/, $//')"
[ ${#MISSING[@]} -gt 0 ] && say "not registered with: $(printf '%s, ' "${MISSING[@]}" | sed 's/, $//')"

cat <<'EOF'

Restart your assistant to drop the running server process.

Two things are deliberately left alone: any .bak- files the installer made
(they are your configs, not ours), and the shared Playwright browser cache in
~/Library/Caches/ms-playwright or ~/.cache/ms-playwright, which other tools
use. To reclaim that space:  rm -rf ~/Library/Caches/ms-playwright/chromium-*
EOF
