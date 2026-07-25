# umlib-mcp

Let your AI assistant read papers using your own University of Michigan library access. Ask it about a paper and it finds a free copy if one exists, or pulls the PDF through the U-M proxy with a session you signed into yourself. Your password never touches the assistant or this server. The only thing kept is the browser session cookie, on your own machine.

Works with Claude Code, Codex, Cursor, VS Code, Zed, Windsurf, Gemini CLI, and Claude Desktop.

## Tools

| Tool | What it does |
|---|---|
| `login` | Opens a browser window so you can sign in. Returns right away; the session is saved for later. |
| `auth_status` | Says whether a session is active. |
| `resolve` | Turns a DOI or title into metadata, a free-copy link, and the proxied publisher URL. |
| `fetch_pdf` | Fetches one PDF (free copy first, then the proxy) and saves it to `~/Downloads`. |
| `proxy_url` | Prefixes a URL for off-campus access. |
| `logout` | Deletes the saved session. |

## Install

One command installs everything and registers it with whichever agents you have:

```sh
curl -LsSf https://raw.githubusercontent.com/puppyum/umlib-mcp/main/install.sh | bash
```

It installs [uv](https://docs.astral.sh/uv/) if you're missing it, installs the server, and writes the config for each agent it finds, backing up every file it edits. To see what it would touch before it touches anything:

```sh
curl -LsSf https://raw.githubusercontent.com/puppyum/umlib-mcp/main/install.sh | bash -s -- --dry-run
```

macOS and Linux. On Windows, use WSL or register the server by hand (below).

Claude Code and Codex share the same plugin marketplace, so either of them can use that instead:

```
/plugin marketplace add puppyum/umlib-mcp
/plugin install umlib@umlib-lab
```

In Claude Code you can also just ask: *"Set me up with umlib-mcp: run `claude plugin marketplace add puppyum/umlib-mcp` and `claude plugin install umlib@umlib-lab`, then log me into the library proxy."*

Nothing needs configuring afterwards. Dependencies install on first launch, and the browser it drives (~150 MB, once per user account) downloads in the background while you get on with things.

To wire it up yourself instead, run `uv tool install git+https://github.com/puppyum/umlib-mcp` and point your client at the binary that lands in `uv tool dir --bin`. It takes no arguments. Only the surrounding key changes: `mcpServers` for Cursor, Windsurf, Gemini CLI and Claude Desktop, `servers` for VS Code (which also wants `"type": "stdio"`), `context_servers` for Zed. Claude Desktop needs the absolute path, since it doesn't expand `~`:

```json
{
  "mcpServers": {
    "umlib": { "command": "/Users/YOU/.local/bin/umlib-mcp" }
  }
}
```

To update later: `uv tool upgrade umlib-mcp --reinstall` (the `--reinstall` matters; a plain upgrade can reuse a cached build). Plugin installs track `@main` and refresh themselves when the server next starts.

## Use it

Ask your assistant to "log me into the library proxy", then finish the U-M Okta sign-in in the window that opens. It closes itself when you're done. You can ask for a paper straight away, because the fetch waits for your sign-in on its own; there's no need to report back.

- "Get the PDF for doi 10.1145/3411764.3445642 and summarize the methods."
- "Find 'Does Transparency in Moderation Really Matter' by Jhaver et al. and pull the full text."

PDFs save to `~/Downloads`. When a session expires you'll be asked to sign in again. When a publisher blocks the automated fetch, you get a link to open yourself.

## Making it reach for the tools on its own

Claude Code and Codex load the bundled skill, so they already try umlib when a paper looks out of reach. For other agents, paste this into your project instructions (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`):

> When searching for or downloading scholarly articles, papers, books or other publications, use the umlib tools. If content looks inaccessible (paywalled, login-walled, 403, or abstract-only) try umlib's `fetch_pdf` before concluding it is unavailable, since it may be accessible through the authenticated library session. Fetch one paper at a time.

## One rule

Fetch papers as you need them, not in bulk. The library's [appropriate-use policy](https://lib.umich.edu/about-us/policies/statement-appropriate-use-electronic-resources) forbids systematic downloading, and publishers respond by cutting off the whole campus rather than one account. The policy sets no number, so the 60-an-hour default here is our own courtesy limit: well above ordinary reading, well below anything that looks like a crawler. For text or data mining at scale, talk to library-ds@umich.edu.

## Configuration

Everything has a working default. To change something, write `~/.umlib/config.toml`:

```toml
max_fetches_per_hour = 60   # courtesy cap on licensed fetches
min_fetch_interval_s = 5    # seconds between licensed fetches
download_dir = "~/Papers"   # where PDFs land
email = "you@umich.edu"     # adds Unpaywall alongside OpenAlex
```

| Setting | Default | Purpose |
|---|---|---|
| `max_fetches_per_hour` | `60` | Courtesy cap on licensed fetches. `0` switches licensed fetching off entirely |
| `min_fetch_interval_s` | `5` | Seconds between licensed fetches |
| `email` | unset | Optional. Free-copy checks use OpenAlex and need no email; setting one adds Unpaywall as a second source |
| `download_dir` | `~/Downloads` | Where PDFs save |
| `profile_dir` | `~/.umlib/profile` | Where the session is stored |
| `proxy_base` | `https://proxy.lib.umich.edu/login?url=` | EZproxy prefix. Must be `https`: the session cookie would otherwise travel in the clear |
| `canary_url` | `https://www.jstor.org/` | The site used to tell "signed in" from "signed out". Point it at something your library definitely licenses |
| `resolver_base` | U-M's MGet It | Link resolver for the "how do I get this" link; set it to `""` to omit it |
| `rewrite_host` | derived from `proxy_base` | Only needed if your proxy signs in on one domain and rewrites onto another |
| `max_pdf_bytes` | `104857600` | Refuse anything larger before saving |
| `login_timeout_s` | `300` | How long the sign-in window waits for you |
| `login_wait_s` | `150` | How long a fetch waits for a sign-in already in progress |
| `lock_timeout_s` | `login_timeout_s + 60` | How long to wait for another agent's browser to finish |

Two more inputs come from the environment only: `UMLIB_CONFIG` moves the config file itself, and `XDG_DOWNLOAD_DIR` supplies the default download directory on Linux desktops that set it.

Anything umlib could not use, it says so in `auth_status` under `config_error` rather than falling back silently. Relative paths resolve against your home directory, not whatever directory your agent happened to start in.

It works at other EZproxy schools, but check three settings before trusting it: `proxy_base`, `canary_url` (the default is JSTOR, and a school that does not license JSTOR can never look signed in), and `resolver_base` (the default points at U-M's resolver). OCLC-hosted proxies are detected automatically. Every setting also reads from an environment variable (`UMLIB_MAX_FETCHES_PER_HOUR` and so on), which wins over the file; the file exists because environment variables are awkward to apply across agents. `auth_status` reports the limit in force and which file it read.

## Develop

```sh
uv sync
uv run pytest
uv run python scripts/smoke.py   # runs the server over stdio and exercises it
```

The plugin tracks `@main`, so everyone picks up changes the next time their server starts. A stdio server doesn't reload while it's running, so restart it (`/mcp` in Claude Code) after editing the source.

## License

MIT
