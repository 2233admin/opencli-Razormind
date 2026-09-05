import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('media_id')              # Numeric media ID (pk)
    parser.add_argument('--min-id', default='')  # Pagination cursor (empty for first page)
    args = parser.parse_args()

    min_id_param = f'&min_id={args.min_id}' if args.min_id else ''

    js = f"""
    (async function() {{
      try {{
        var url = 'https://www.instagram.com/api/v1/media/{args.media_id}/comments/?can_support_threading=true&permalink_enabled=false{min_id_param}';
        var r = await fetch(url, {{
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
        if (data.require_login) return JSON.stringify({{ error: true, message: 'Login required' }});
        var comments = (data.comments || []).map(function(c) {{
          return {{
            pk: c.pk,
            text: c.text,
            username: c.user ? c.user.username : null,
            user_id: c.user ? c.user.pk : null,
            created_at: c.created_at,
            like_count: c.comment_like_count,
            reply_count: c.child_comment_count || 0
          }};
        }});
        return JSON.stringify({{
          comments: comments,
          has_more_comments: data.has_more_comments || false,
          next_min_id: data.next_min_id || null
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
