import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description="Discover URLs from /sitemap.xml (recursively expanding sitemap indexes)"
    )
    parser.add_argument(
        "origin", help="Site origin, e.g. https://example.com"
    )
    parser.add_argument(
        "--max-urls",
        type=int,
        default=5000,
        help="Stop after collecting this many URLs",
    )
    args = parser.parse_args()

    js = f"""
    (async function() {{
      try {{
        const origin = {args.origin!r}.replace(/\\/+$/, '');
        const maxUrls = {args.max_urls};
        const candidates = [origin + '/sitemap.xml', origin + '/sitemap_index.xml'];
        const seen = new Set();
        const urls = [];
        let lastError = null;

        async function fetchXml(u) {{
          try {{
            const r = await fetch(u, {{ credentials: 'include' }});
            if (!r.ok) return null;
            const t = await r.text();
            return t;
          }} catch(e) {{
            lastError = e.message;
            return null;
          }}
        }}

        function extractTags(xml, tag) {{
          const re = new RegExp('<' + tag + '>([\\\\s\\\\S]*?)<\\\\/' + tag + '>', 'g');
          const out = [];
          let m;
          while ((m = re.exec(xml)) !== null) out.push(m[1].trim());
          return out;
        }}

        const queue = [...candidates];
        const visitedSitemaps = new Set();
        let foundAny = false;

        while (queue.length > 0 && urls.length < maxUrls) {{
          const sm = queue.shift();
          if (visitedSitemaps.has(sm)) continue;
          visitedSitemaps.add(sm);
          const xml = await fetchXml(sm);
          if (xml === null) continue;
          foundAny = true;

          const nested = extractTags(xml, 'sitemap')
            .map(s => {{
              const lm = s.match(/<loc>([\\s\\S]*?)<\\/loc>/);
              return lm ? lm[1].trim() : null;
            }})
            .filter(Boolean);
          for (const n of nested) queue.push(n);

          const locs = extractTags(xml, 'url')
            .map(u => {{
              const lm = u.match(/<loc>([\\s\\S]*?)<\\/loc>/);
              return lm ? lm[1].trim() : null;
            }})
            .filter(Boolean);
          for (const loc of locs) {{
            if (urls.length >= maxUrls) break;
            if (!seen.has(loc)) {{ seen.add(loc); urls.push(loc); }}
          }}
        }}

        if (!foundAny) {{
          return JSON.stringify({{
            error: true, message: 'No sitemap found at standard paths', urls: []
          }});
        }}
        return JSON.stringify({{
          error: false, source: 'sitemap.xml', count: urls.length, urls: urls
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message, urls: [] }});
      }}
    }})()
    """
    print(js)


if __name__ == '__main__':
    main()
