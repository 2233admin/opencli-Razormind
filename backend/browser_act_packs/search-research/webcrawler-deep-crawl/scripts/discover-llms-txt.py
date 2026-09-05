import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description="Discover URLs from /llms.txt (LLM-friendly site index)"
    )
    parser.add_argument('origin', help="Site origin, e.g. https://example.com")
    args = parser.parse_args()

    js = f"""
    (async function() {{
      try {{
        const origin = {args.origin!r}.replace(/\\/+$/, '');
        const url = origin + '/llms.txt';
        const resp = await fetch(url, {{ credentials: 'include' }});
        if (!resp.ok) {{
          return JSON.stringify({{
            error: true,
            message: 'llms.txt not available (HTTP ' + resp.status + ')',
            urls: []
          }});
        }}
        const text = await resp.text();
        const urlRegex = /https?:\\/\\/[^\\s<>"'\\)]+/g;
        const matches = text.match(urlRegex) || [];
        const seen = new Set();
        const urls = [];
        for (let raw of matches) {{
          let u = raw.replace(/[\\.,;:!\\?]+$/, '');
          if (u.endsWith('.md')) u = u.slice(0, -3);
          if (!seen.has(u)) {{ seen.add(u); urls.push(u); }}
        }}
        return JSON.stringify({{
          error: false, source: 'llms.txt', count: urls.length, urls: urls
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message, urls: [] }});
      }}
    }})()
    """
    print(js)


if __name__ == '__main__':
    main()
