import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('page_url')  # Facebook page URL, e.g. https://www.facebook.com/cern
    args = parser.parse_args()

    js = f"""
    (async function() {{
      try {{
        const pageUrl = {repr(args.page_url)};
        const r = await fetch(pageUrl);
        const html = await r.text();
        const m = html.match(/userID[^0-9]*(\\d{{12,18}})/);
        if (!m) return JSON.stringify({{
          error: true,
          message: 'Could not find page numeric ID in HTML. ' +
            'The page may require login or the URL may be invalid.'
        }});
        return JSON.stringify({{ pageId: m[1], pageUrl: pageUrl }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
