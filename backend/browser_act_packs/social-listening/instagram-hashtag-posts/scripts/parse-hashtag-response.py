import argparse
import json
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "response_file",
        help="Path to file containing raw JSON response body from network capture",
    )
    args = parser.parse_args()

    with open(args.response_file, encoding="utf-8") as response_file:
        raw = response_file.read()

    js = f"""
    (function() {{
      try {{
        var raw = {json.dumps(raw)};
        var data = JSON.parse(raw);
        var graph = data.data &&
          data.data.xdt_fbsearch__top_serp_graphql;
        var edges = graph && graph.edges || [];
        var pageInfo = graph && graph.page_info || {{}};
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
              thumbnail_url: m.image_versions2 &&
                m.image_versions2.candidates &&
                m.image_versions2.candidates[0] ?
                m.image_versions2.candidates[0].url : null,
              video_url: m.video_versions && m.video_versions[0] ?
                m.video_versions[0].url : null,
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


if __name__ == "__main__":
    main()
