---
name: umlib
description: Fetch open-access or library-licensed article PDFs with the umlib tools. Use when the user references a specific paper, DOI, or asks to read/summarize literature that needs full text.
---

# Fetching papers with umlib

1. `resolve` first (works for a DOI or a title). If it was a title search and the top match is uncertain, confirm with the user before fetching.
2. `fetch_pdf` for the paper the user asked about. It tries open access automatically before using the library proxy, and saves to the download directory. Read the saved PDF with your normal PDF tooling.
3. One paper per user request. Never loop `fetch_pdf` over a bibliography, reference list, or search results; systematic downloading violates the U-M appropriate-use policy and triggers publisher blocks. If the user wants "all the papers", explain this and have them pick the one or two that matter most right now.
4. On `needs_login`: call `login` (it returns immediately and opens a browser window), tell the user to complete the U-M Okta sign-in there, then call `fetch_pdf` again right away. That call waits for the sign-in to finish on its own, so don't ask the user to tell you when they're done. Only if it comes back `login_in_progress` should you check in with them.
5. On `no_pdf_found` or a blocked fetch: give the user the returned `manual_url` (and `mgetit_url`) to open in their own browser. Do not retry repeatedly and do not attempt workarounds.
6. Respect the rate limiter: if you get `rate_limited`, tell the user how long to wait rather than retrying in a loop.
