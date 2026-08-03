# FMHY OpenCLI adapter

Public, browserless adapters for the live `https://fmhy.net/` sitemap. The adapter reads only URLs currently published by FMHY, checks `robots.txt`, limits concurrency, and returns structured page text and resource links.

## Install

```powershell
& .\integrations\opencli\fmhy\install.ps1
```

Use `-Force` only to replace an existing managed FMHY adapter.

## Commands

```powershell
opencli fmhy pages -f json
opencli fmhy page ai -f json
opencli fmhy crawl --group other -f json
opencli fmhy search "open source" --limit 25 -f json
```

`crawl` discovers the current sitemap on each run. It therefore covers new or removed FMHY subpages without regenerating one command per URL.
