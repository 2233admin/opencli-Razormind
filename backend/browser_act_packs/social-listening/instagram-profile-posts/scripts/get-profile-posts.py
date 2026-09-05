import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('user_id')            # Numeric user ID
    parser.add_argument('--count', default='12')   # Posts per page (max 12)
    parser.add_argument('--max-id', default='')    # Pagination cursor (empty for first page)
    args = parser.parse_args()

    max_id_param = f'&max_id={args.max_id}' if args.max_id else ''

    js = f"""
    (async function() {{
      try {{
        var url = 'https://www.instagram.com/api/v1/feed/user/{args.user_id}/'
          + '?count={args.count}{max_id_param}';
        var r = await fetch(url, {{
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
        if (data.require_login) return JSON.stringify({{ error: true, message: 'Login required' }});
        var items = (data.items || []).map(function(m) {{
          return {{
            pk: m.pk,
            code: m.code,
            taken_at: m.taken_at,
            media_type: m.media_type,
            like_count: m.like_count,
            comment_count: m.comment_count,
            caption: m.caption ? m.caption.text : null,
            thumbnail_url: m.image_versions2 && m.image_versions2.candidates
              && m.image_versions2.candidates[0]
              ? m.image_versions2.candidates[0].url : null,
            video_url: m.video_versions && m.video_versions[0] ? m.video_versions[0].url : null,
            location: m.location ? {{ id: m.location.pk, name: m.location.name }} : null,
            username: m.user ? m.user.username : null,
            user_id: m.user ? m.user.pk : null
          }};
        }});
        return JSON.stringify({{
          items: items,
          more_available: data.more_available,
          next_max_id: data.next_max_id || null
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
