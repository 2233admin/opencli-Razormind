import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('location_id')              # Numeric location ID from search-location
    parser.add_argument('--tab', default='ranked')   # Feed tab: ranked or recent
    parser.add_argument('--max-id', default='')      # Pagination cursor (empty for first page)
    parser.add_argument('--session-id', default='ig-place-session-001')  # Session ID for dedup
    args = parser.parse_args()

    max_id_val = args.max_id if args.max_id else ''

    js = f"""
    (async function() {{
      try {{
        var csrfToken = document.cookie.split('; ').find(function(c) {{
          return c.startsWith('csrftoken=');
        }});
        var token = csrfToken ? csrfToken.split('=')[1] : '';
        if (!token) return JSON.stringify({{
          error: true,
          message: 'CSRF token not found; navigate to instagram.com first'
        }});
        var body = 'max_id={max_id_val}&tab={args.tab}&session_id={args.session_id}';
        var r = await fetch(
          'https://www.instagram.com/api/v1/locations/{args.location_id}/sections/',
          {{
          method: 'POST',
          headers: {{
            'X-IG-App-ID': '936619743392459',
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': token,
            'Content-Type': 'application/x-www-form-urlencoded'
          }},
          body: body
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
        var items = [];
        (data.sections || []).forEach(function(s) {{
          var lc = s.layout_content;
          if (!lc) return;
          var medias = [];
          if (lc.medias) medias = lc.medias;
          else if (
            lc.one_by_two_item && lc.one_by_two_item.clips
          ) medias = lc.one_by_two_item.clips.items;
          medias.forEach(function(item) {{
            var m = item.media;
            if (!m) return;
            items.push({{
              pk: m.pk,
              code: m.code,
              media_type: m.media_type,
              taken_at: m.taken_at,
              like_count: m.like_count,
              comment_count: m.comment_count,
              caption: m.caption ? m.caption.text : null,
              thumbnail_url: m.image_versions2
                && m.image_versions2.candidates
                && m.image_versions2.candidates[0]
                ? m.image_versions2.candidates[0].url
                : null,
              video_url: m.video_versions && m.video_versions[0]
                ? m.video_versions[0].url
                : null,
              username: m.user ? m.user.username : null,
              user_id: m.user ? m.user.pk : null,
              location_name: m.location ? m.location.name : null
            }});
          }});
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
