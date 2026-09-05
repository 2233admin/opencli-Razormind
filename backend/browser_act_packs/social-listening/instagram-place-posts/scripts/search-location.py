import argparse
import sys
import urllib.parse


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('keyword')  # Location name to search (e.g., "New York", "Eiffel Tower")
    args = parser.parse_args()

    # URL-encode the keyword
    encoded = urllib.parse.quote(args.keyword)

    js = f"""
    (async function() {{
      try {{
        var r = await fetch(
          'https://www.instagram.com/api/v1/location_search/?search_query={encoded}',
          {{
            headers: {{
              'X-IG-App-ID': '936619743392459',
              'X-Requested-With': 'XMLHttpRequest'
            }}
          }});
        if (!r.ok) {{
          var errText = await r.text();
          return JSON.stringify({{
            error: true, message: 'HTTP ' + r.status,
            detail: errText.slice(0, 200)
          }});
        }}
        var data = await r.json();
        var venues = (data.venues || []).map(function(v) {{
          return {{
            id: v.external_id,
            name: v.name,
            address: v.address || null,
            lat: v.lat || null,
            lng: v.lng || null
          }};
        }});
        return JSON.stringify({{ venues: venues }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
