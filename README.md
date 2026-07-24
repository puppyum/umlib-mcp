# umlib-mcp

Let Claude read papers using your own University of Michigan library access. When you ask about a paper, it finds a free copy if one exists, and otherwise pulls the PDF through the U-M proxy using a session you signed into yourself. Your password never touches Claude or this server; only the browser session cookie is kept, on your own machine.

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

Install [uv](https://docs.astral.sh/uv/) first (`curl -LsSf https://astral.sh/uv/install.sh | sh`, or `brew install uv`). Everything else is automatic: dependencies install on first launch, and the server fetches its browser (~150 MB, once per machine) in the background as soon as it starts.

**Claude Code** (recommended):

```
/plugin marketplace add puppyum/umlib-mcp
/plugin install umlib@umlib-lab
```

To turn on the free-copy check, export your email in the shell that starts Claude Code: `export UMLIB_EMAIL=you@umich.edu`.

**Claude Desktop:** Settings → Developer → Edit Config. Use an absolute path to `uvx` (find it with `which uvx`):

```json
{
  "mcpServers": {
    "umlib": {
      "command": "/Users/YOU/.local/bin/uvx",
      "args": ["--from", "git+https://github.com/puppyum/umlib-mcp@main", "umlib-mcp"],
      "env": { "UMLIB_EMAIL": "you@umich.edu" }
    }
  }
}
```

## Use it

Ask Claude to "log me into the library proxy", then finish the U-M Okta sign-in in the window that opens (it closes itself when done). You can ask for a paper straight away — the fetch waits for your sign-in on its own, so there's no need to report back that you've finished.

- "Get the PDF for doi 10.1145/3411764.3445642 and summarize the methods."
- "Find 'Does Transparency in Moderation Really Matter' by Jhaver et al. and pull the full text."

PDFs save to `~/Downloads`. If the session expires you'll be asked to sign in again. If a publisher blocks the automated fetch, Claude hands you a link to open yourself.

## One rule

Fetch papers one at a time, as you need them (it limits itself to 8 an hour). Don't bulk-download reading lists: the library's [appropriate-use policy](https://lib.umich.edu/about-us/policies/statement-appropriate-use-electronic-resources) forbids systematic downloading, and publishers respond by cutting off the whole campus. For text or data mining at scale, contact library-ds@umich.edu.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `UMLIB_EMAIL` | unset | Enables the free-copy check |
| `UMLIB_DOWNLOAD_DIR` | `~/Downloads` | Where PDFs save |
| `UMLIB_PROFILE_DIR` | `~/.umlib/profile` | Where the session is stored |
| `UMLIB_PROXY_BASE` | `https://proxy.lib.umich.edu/login?url=` | EZproxy prefix (change for other schools) |
| `UMLIB_MAX_FETCHES_PER_HOUR` | `8` | Fetch cap |

## Develop

```sh
uv sync
uv run pytest
uv run python scripts/smoke.py   # runs the server over stdio and exercises it
```

The plugin tracks `@main`, so lab members pick up fixes whenever their server next starts. If you edit the source while a server is running, restart it (`/mcp` in Claude Code) — a stdio server doesn't reload on its own.

## License

MIT
