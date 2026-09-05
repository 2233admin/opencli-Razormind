---
name: kuaishou-search
description: "Extract structured public Kuaishou video search results from the current browser page."
---

# Kuaishou — Video Search

> Search keyword → bounded structured video results

## Prerequisites

- Browser Act is available.
- The target browser can reach `kuaishou.com`.
- The current session may need human login or verification.

## Execution

Navigate to:

```text
https://www.kuaishou.com/search/video?searchKey={query}
```

Wait for the page to settle, then run:

```bash
python scripts/extract-search.py --max-results 10
```

The result contains the video URL, caption, author, cover, playable media
URL when exposed by the page, publication timestamp, tags, and bounded
engagement statistics.

## Operational boundary

This pack reads the public search state already present in the browser. It
never automates login, captcha solving, or anti-bot bypass. Login, verification,
regional restrictions, and blocked responses are human-handled conditions.

## Limitations

The manifest extracts the initial search result state only. Cursor-based
follow-up requests and comment collection are intentionally out of scope for
this pack; they require a separate pagination contract and should not be
silently represented as complete results.
