# VENDOR.md — browser-act skills packs

- **Source**: https://github.com/browser-act/skills
- **Vendored commit**: `a23131e7bd7ea7e472e3a4882b9e22e34b3bd9cf`
- **Vendored on**: 2026-07-08
- **License**: MIT (see `LICENSE` in this directory) — attribution: BrowserAct
  (https://github.com/browser-act)

## What was copied

The upstream `solutions/` tree, verbatim, one directory per pack:

```
solutions/<category>/<pack-name>/SKILL.md
solutions/<category>/<pack-name>/scripts/*.py
```

became

```
backend/browser_act_packs/<category>/<pack-name>/SKILL.md
backend/browser_act_packs/<category>/<pack-name>/scripts/*.py
```

for the five upstream categories: `ecommerce`, `lead-generation`, `search-research`,
`social-listening`, `video-platforms` — 78 packs total (`SKILL.md` count at vendor
time; see `catalog.py`'s scan for the live count).

`solutions/README.md` was copied to `backend/browser_act_packs/SOLUTIONS-README.md`
(kept out of the category tree so it isn't mistaken for a pack). The upstream
`LICENSE` was copied unchanged to `backend/browser_act_packs/LICENSE`.

**Not vendored**:
- the `browser-act` CLI itself (external PyPI tool, `uv tool install browser-act-cli`)
- `browser-act-skill-forge` (pack generator, out of scope)

## What was NOT touched

Every `SKILL.md` and every file under `scripts/` is byte-for-byte identical to the
upstream commit above. Do not hand-edit files inside a `<category>/<pack-name>/`
directory — if a pack needs a fix, fix it upstream and re-vendor (re-clone +
re-copy `solutions/**`), or override in code outside this tree.

## What IS ours (added on top, not upstream)

- `catalog.py`, `manifest.py`, `__init__.py` — new code, not vendored.
- `channel.manifest.json` files (one per pack, machine-readable execution
  contract) are **our addition**, authored in PR-D of Browser Act integration — they do not
  exist upstream and are not part of the BrowserAct skills repo. PR-A (this
  vendor drop) defines only the `PackManifest` schema/loader; it does not
  write any `channel.manifest.json` content.

## Refreshing

To pull upstream updates:

1. `git clone --depth 1 https://github.com/browser-act/skills <tmp>`
2. Diff `<tmp>/solutions/**` against this tree; re-copy changed/added pack
   directories verbatim (do not touch our `channel.manifest.json` files when
   they exist — those are ours, not upstream's).
3. Update the commit hash and date at the top of this file.

## Seed manifests (PR-D)

Two of the 78 vendored packs got a hand-authored `channel.manifest.json`
(Browser Act integration decision #5), chosen as the pair that can be validated end-to-end
without a logged-in session: `search-research/google-search-serp` (a
login-free public search) and `ecommerce/taobao-keyword-search` (a
login-free public listing search page). Both translations below were
checked against the pack's real `scripts/*.py` before being written (field
names / arg shapes match what the script actually emits/accepts).

### `search-research/google-search-serp`

SKILL.md prose → manifest:

- **navigate**: SKILL.md's `navigate https://www.google.com/search?q=...`
  → `steps[0] = {"op": "navigate", "url_template":
  "https://www.google.com/search?q={query}&num={num}&hl={lang}&gl={country}"}`.
  `query`/`num`/`lang`/`country` map straight onto the four querystring
  params the script's JS itself reads back out of `window.location.search`
  (`q`, `num`, `hl`, `gl`) — `num`/`lang`/`country` default to `"10"`/`""`/`""`
  so a bare `{"query": "..."}` config still works.
- **wait**: SKILL.md's `wait stable` → `steps[1] = {"op": "wait",
  "wait_mode": "stable"}`.
- **eval_script**: SKILL.md's `eval "$(python scripts/serp-extract.py)"` →
  `steps[2] = {"op": "eval_script", "script": "scripts/serp-extract.py",
  "args": []}`. Confirmed against the real script: `serp-extract.py` takes
  **no argparse arguments at all** (it emits a fixed JS snippet that reads
  everything it needs, including `q`/`num`/`hl`/`gl`, straight from
  `window.location.search` at eval time) — so `args: []` is correct, not an
  oversight.
- **pagination**: `{"mode": "none"}` — single page only (see Known
  limitation #2 below).
- **success**: `{"min_count": 1, "required_field": "organicResults"}`. The
  script's JS always returns one JSON **object** (the whole SERP), never a
  list, so `collect()` wraps it as a single item; `required_field` checks
  that item has a (truthy) `organicResults` key. Confirmed against the real
  script: the emitted JS's success-path return value is `{searchQuery,
  resultsTotal, organicResults, paidResults, relatedQueries, peopleAlsoAsk,
  aiOverview}` — `organicResults` is present verbatim.

### `ecommerce/taobao-keyword-search`

SKILL.md prose → manifest:

- **navigate**: SKILL.md's `navigate https://s.taobao.com/search?q=...` →
  `steps[0] = {"op": "navigate", "url_template":
  "https://s.taobao.com/search?q={keyword}&page={page}&ie=utf8"}`. `{page}`
  comes from the channel's own per-page loop context (decision #5's
  `pagination`), not from `param_schema` — page 1 is implicit, page 2+ is
  driven by `pagination.url_template` below.
- **wait**: `steps[1] = {"op": "wait", "wait_mode": "stable"}`.
- **eval_script**: SKILL.md's `eval "$(python scripts/search-products.py
  {keyword} --page {page} --sort {sort})"` → `steps[2] = {"op":
  "eval_script", "script": "scripts/search-products.py", "args":
  ["{keyword}", "--page", "{page}", "--sort", "{sort}"]}`. Confirmed against
  the real script: `search-products.py`'s argparse takes a **positional**
  `keyword` (documentation-only per its own comment — the URL already
  carries `q=`) plus `--page` (default `"1"`) and `--sort` (default `""`,
  values `""`/`"sale-desc"`/`"price-asc"`/`"price-desc"`) among other
  optional flags (`--tab`, `--start-price`, `--end-price`) this seed
  manifest does not yet expose — arg names/order match exactly.
- **pagination**: `{"mode": "url_page", "url_template":
  "https://s.taobao.com/search?q={keyword}&page={page}&ie=utf8",
  "page_param": "page", "stop_when": "result_count < 10"}` — matches
  SKILL.md's documented "stop when a page returns fewer than 10 results"
  pagination note; the interpreter's `_stop_when_triggered` regex
  (`backend/channels/browser_act_channel.py`) parses `"result_count < N"`
  directly.
- **success**: `{"min_count": 1, "required_field": "itemId"}`. Confirmed
  against the real script: the emitted JS's success path returns a JSON
  **list** of product dicts, each shaped `{itemId, itemUrl, title,
  subTitle, priceYuan, priceDesc, imageUrl, salesCount, shopName, location,
  rating, tags}` — `itemId` is present verbatim on every item.

### Known limitations

1. **No URL-encoding of params into `url_template`.** The interpreter
   (`_run_page` in `backend/channels/browser_act_channel.py`) does
   `url_template.format(**ctx)` — a plain string substitution, not a
   URL-encoding one. This is fine for simple ASCII keywords/queries, but
   SKILL.md's own prose notes that `q` should be URL-encoded (spaces,
   non-ASCII, `&`/`#`/`?` in the search term would corrupt the querystring
   otherwise). A real-world caller must pre-encode `query`/`keyword` before
   passing them in `params`, or a future interpreter enhancement should
   URL-encode template substitutions itself — out of scope for this PR.
2. **`google-search-serp` is seeded single-page (`pagination.mode:
   "none"`).** Its real pagination is a `start=(page-1)*num` offset
   querystring param (see the script's own `start`/`num` parsing), which
   this interpreter's `{page}` context (a bare 1-based page counter, not an
   offset) can't compute without a per-pack formula the generic manifest
   schema doesn't currently express. Multi-page google is a future
   enhancement (e.g. a `pagination.mode` that lets a manifest declare an
   offset formula), not something this seed pretends to support.
3. **The other ~76 vendored packs have no `channel.manifest.json` yet.**
   Seeding is intentionally incremental (Browser Act integration PR-D scope: 2 packs to prove
   the pipeline, not full coverage) — `backend/browser_act_packs/manifest.py`
   (`PackManifest`) is the extension point; add a `channel.manifest.json`
   next to a pack's `SKILL.md` as each one is needed, translating its prose
   the same way as above. In particular, the `*-api-skill` packs (e.g.
   `search-research/web-search-scraper-api-skill`, confirmed by reading its
   script: it `requests.post`s straight to `https://api.browseract.com/v2/
   workflow` with a Bearer API key) use a **different** execution shape
   entirely — their scripts call the BrowserAct **API** directly (HTTP, no
   browser session at all) instead of navigate→wait→eval against a live
   browser-act session, so this channel's navigate/wait/eval_script
   interpreter does not — and structurally cannot — model them; they would
   need either a distinct `step.op` (e.g. `"api_call"`) or a separate
   channel entirely, a decision deferred rather than made silently here.
