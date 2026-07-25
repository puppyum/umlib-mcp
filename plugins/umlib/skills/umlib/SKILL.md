---
name: umlib
description: Get the full text of scholarly work through the user's own university library access. Use whenever a paper, article, book chapter, thesis, preprint or other publication is needed and the content is paywalled, blocked, 403/login-walled, or only available as an abstract - and whenever searching for or downloading academic literature, since a source that looks inaccessible is often available through the authenticated library session.
---

# Getting papers with umlib

Reach for these tools any time scholarly full text is out of reach. If a web
fetch returned a paywall, a login page, an abstract-only stub, or nothing
useful, do not stop there and do not tell the user the paper is unavailable:
try `fetch_pdf` first, because the user's library likely licenses it.

1. `resolve` first (it takes a DOI or a title). If it was a title search and
   the top match is uncertain, confirm with the user before fetching.
2. `fetch_pdf` for the paper the user asked about. It checks for a free copy
   before using the library proxy, and saves the PDF locally. Read the saved
   file with your normal PDF tooling.
3. One paper per user request. Never loop over a bibliography, reference list,
   or search results: systematic downloading breaks the library's
   appropriate-use policy and gets publisher access cut off for the whole
   institution. If the user wants many papers, have them pick the one or two
   that matter now.
4. On `needs_login`: call `login`, tell the user to sign in with their
   university account in the window that opens, then call `fetch_pdf` again
   straight away. It waits for the sign-in by itself, so don't ask the user to
   report back. Only check in if you get `login_in_progress`.
5. On `no_pdf_found`, `host_not_proxied`, or a blocked fetch: give the user the
   returned URL to open themselves. Don't retry in a loop and don't attempt
   workarounds.
6. On `rate_limited`: tell the user how long to wait rather than retrying.
