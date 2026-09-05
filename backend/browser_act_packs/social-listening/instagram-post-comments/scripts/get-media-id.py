import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "shortcode",
        # Post shortcode from URL (e.g., BwrsO1Bho2N from instagram.com/p/BwrsO1Bho2N/)
    )
    args = parser.parse_args()

    js = f"""
    (async function() {{
      try {{
        var r = await fetch('https://www.instagram.com/api/v1/media/shortcode/{args.shortcode}/', {{
          headers: {{
            'X-IG-App-ID': '936619743392459',
            'X-Requested-With': 'XMLHttpRequest'
          }}
        }});
        if (!r.ok) {{
          var errText = await r.text();
          return JSON.stringify({{
            error: true,
            message: 'HTTP ' + r.status,
            detail: errText.slice(0, 200)
          }});
        }}
        var data = await r.json();
        if (!data.items || !data.items[0]) {{
          return JSON.stringify({{ error: true, message: 'Media not found' }});
        }}
        var m = data.items[0];
        return JSON.stringify({{
          media_id: m.pk,
          shortcode: m.code,
          media_type: m.media_type,
          username: m.user ? m.user.username : null,
          taken_at: m.taken_at
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
