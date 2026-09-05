import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('note_id', help='note ID to extract comments for')
    args = parser.parse_args()

    note_id = args.note_id

    js = f"""
(function() {{
  try {{
    const unwrap = v => v?.value !== undefined ? v.value : v?._value !== undefined ? v._value : v;
    const mapRaw = unwrap(window.__INITIAL_STATE__?.note?.noteDetailMap);
    if (!mapRaw) {{
      return JSON.stringify({{
        error: true,
        message: 'noteDetailMap not found — verify page is a note detail page'
      }});
    }}
    const detail = unwrap(mapRaw?.['{note_id}']);
    if (!detail) {{
      return JSON.stringify({{ error: true, message: 'note not found in map: {note_id}' }});
    }}
    const comments = unwrap(detail?.comments);
    if (!comments) {{
      return JSON.stringify({{ error: true, message: 'comments not found for note: {note_id}' }});
    }}
    const list = unwrap(comments?.list) ?? [];
    const items = list.map(c => {{
      const cu = unwrap(c);
      const user = unwrap(cu?.userInfo);
      return {{
        id: unwrap(cu?.id),
        content: unwrap(cu?.content),
        likeCount: unwrap(cu?.likeCount),
        createTime: unwrap(cu?.createTime),
        ipLocation: unwrap(cu?.ipLocation),
        userId: user?.userId,
        nickname: user?.nickname,
        avatar: user?.image
      }};
    }});
    return JSON.stringify({{
      count: items.length,
      cursor: unwrap(comments?.cursor),
      hasMore: unwrap(comments?.hasMore),
      comments: items
    }});
  }} catch(e) {{
    return JSON.stringify({{ error: true, message: e.message }});
  }}
}})()
"""
    print(js)


if __name__ == '__main__':
    main()
