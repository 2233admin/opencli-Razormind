import argparse
import json
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(
        description=(
            "Extract outbound links from current page DOM, "
            "scoped to start URL prefix"
        )
    )
    parser.add_argument(
        "start_url", help="Crawl scope: only links under this URL prefix are kept"
    )
    parser.add_argument(
        "--include-globs",
        default="[]",
        help="JSON array of glob patterns; if provided, link must match one to be kept",
    )
    parser.add_argument(
        "--exclude-globs",
        default="[]",
        help="JSON array of glob patterns; matching links are dropped",
    )
    args = parser.parse_args()

    include_globs = json.loads(args.include_globs)
    exclude_globs = json.loads(args.exclude_globs)

    js = f"""
    (function() {{
      try {{
        const startUrl = {args.start_url!r};
        const includeGlobs = {json.dumps(include_globs)};
        const excludeGlobs = {json.dumps(exclude_globs)};

        // Glob-to-regex: ** = anything, * = anything except '/', ? = single char
        function globToRe(g) {{
          let r = '';
          let i = 0;
          while (i < g.length) {{
            const c = g[i];
            if (c === '*' && g[i+1] === '*') {{ r += '.*'; i += 2; }}
            else if (c === '*') {{ r += '[^/]*'; i += 1; }}
            else if (c === '?') {{ r += '.'; i += 1; }}
            else if ('.+^$()|[]{{}}\\\\'.includes(c)) {{ r += '\\\\' + c; i += 1; }}
            else {{ r += c; i += 1; }}
          }}
          return new RegExp('^' + r + '$');
        }}

        const includeRes = includeGlobs.map(globToRe);
        const excludeRes = excludeGlobs.map(globToRe);

        // Normalize start URL: drop fragment, ensure scope prefix
        let scopeUrl;
        try {{ scopeUrl = new URL(startUrl); }} catch(e) {{
          return JSON.stringify({{
            error: true,
            message: 'Invalid start_url: ' + e.message,
            links: []
          }});
        }}
        const scopeBase = scopeUrl.origin + scopeUrl.pathname.replace(/\\/[^\\/]*$/, '/');

        const anchors = document.querySelectorAll('a[href]');
        const seen = new Set();
        const links = [];
        for (const a of anchors) {{
          let href;
          try {{ href = new URL(a.getAttribute('href'), document.baseURI).toString(); }}
          catch(e) {{ continue; }}
          // Drop fragments and tracking
          href = href.split('#')[0];
          if (!href || seen.has(href)) continue;
          // Scope check: must be under the start URL's directory or origin
          // (depending on the user's intent).
          if (!href.startsWith(scopeUrl.origin)) continue;
          const isStartPage = href === scopeUrl.origin + scopeUrl.pathname;
          if (!href.startsWith(scopeBase) && !isStartPage) continue;
          // Drop binary / asset extensions
          const assetPattern = new RegExp(
            '\\\\.(png|jpg|jpeg|gif|svg|webp|ico|mp4|mp3|webm|woff2?|ttf|'
            + 'css|js|json|xml|zip|tar\\\\.gz)$',
            'i'
          );
          if (assetPattern.test(href)) continue;
          // Exclude globs
          if (excludeRes.some(re => re.test(href))) continue;
          // Include globs (if specified)
          if (includeRes.length > 0 && !includeRes.some(re => re.test(href))) continue;
          seen.add(href);
          links.push(href);
        }}

        return JSON.stringify({{
          error: false,
          source: 'dom',
          page: location.href,
          scope_base: scopeBase,
          count: links.length,
          links: links
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message, links: [] }});
      }}
    }})()
    """
    print(js)


if __name__ == "__main__":
    main()
