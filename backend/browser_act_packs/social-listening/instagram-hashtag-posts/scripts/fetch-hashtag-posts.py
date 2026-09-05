import argparse
import sys
import uuid
from urllib.parse import quote


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('hashtag')  # Hashtag name without # (e.g., travel)
    args = parser.parse_args()

    session_id = str(uuid.uuid4())
    query = '#' + args.hashtag

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

        var fbDtsg = '';
        var lsdVal = '';
        try {{
          var scripts = Array.from(document.querySelectorAll('script'));
          for (var s of scripts) {{
            var t = s.textContent;
            var m = t.match(/"LSD",\\[\\],\\{{"token":"([^"]+)"/);
            if (m) {{ lsdVal = m[1]; }}
            var m2 = t.match(/"DTSGInitialData".*?"token":"([^"]+)"/);
            if (m2) {{ fbDtsg = m2[1]; break; }}
          }}
        }} catch(e) {{}}

        var variables = JSON.stringify({{
          "query": "{query}",
          "search_session_id": "{session_id}",
          "serp_session_id": "{session_id}"
        }});

        var params = new URLSearchParams();
        params.append('__a', '1');
        params.append('__d', 'www');
        if (fbDtsg) params.append('fb_dtsg', fbDtsg);
        if (lsdVal) params.append('lsd', lsdVal);
        params.append('fb_api_caller_class', 'RelayModern');
        params.append('fb_api_req_friendly_name', 'PolarisKeywordSearchExplorePageRelayQuery');
        params.append('variables', variables);
        params.append('server_timestamps', 'true');
        params.append('doc_id', '36829937936605248');

        var r = await fetch('https://www.instagram.com/graphql/query', {{
          method: 'POST',
          headers: {{
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-IG-App-ID': '936619743392459',
            'X-CSRFToken': token,
            'X-FB-Friendly-Name': 'PolarisKeywordSearchExplorePageRelayQuery',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': (
              'https://www.instagram.com/explore/search/keyword/?q=%23'
              + '{quote(args.hashtag)}'
            )
          }},
          body: params.toString()
        }});

        if (!r.ok) return JSON.stringify({{ error: true, message: 'HTTP ' + r.status }});
        var data = await r.json();
        var edges = (data.data && data.data.xdt_fbsearch__top_serp_graphql
          && data.data.xdt_fbsearch__top_serp_graphql.edges) || [];
        var pageInfo = (data.data && data.data.xdt_fbsearch__top_serp_graphql
          && data.data.xdt_fbsearch__top_serp_graphql.page_info) || {{}};
        var items = [];
        edges.forEach(function(edge) {{
          var nodeItems = edge.node && edge.node.items || [];
          nodeItems.forEach(function(m) {{
            items.push({{
              pk: m.pk,
              code: m.code,
              media_type: m.media_type,
              taken_at: m.taken_at,
              like_count: m.like_count,
              comment_count: m.comment_count,
              caption: m.caption ? m.caption.text : null,
              thumbnail_url: m.image_versions2 && m.image_versions2.candidates
                && m.image_versions2.candidates[0] ? m.image_versions2.candidates[0].url : null,
              video_url: m.video_versions && m.video_versions[0] ? m.video_versions[0].url : null,
              username: m.user ? m.user.username : null,
              user_id: m.user ? m.user.pk : null
            }});
          }});
        }});
        return JSON.stringify({{
          items: items,
          has_next_page: pageInfo.has_next_page || false,
          end_cursor: pageInfo.end_cursor || null
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
