import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('note_id', help='note ID from search result or URL')
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
    const n = unwrap(detail?.note);
    if (!n) {{
      return JSON.stringify({{ error: true, message: 'note data is null for: {note_id}' }});
    }}
    const user = unwrap(n?.user);
    const interact = unwrap(n?.interactInfo);
    const tags = (unwrap(n?.tagList) ?? []).map(t => {{
      const ut = unwrap(t);
      return {{ name: unwrap(ut?.name), type: unwrap(ut?.type) }};
    }});
    return JSON.stringify({{
      noteId: unwrap(n?.noteId),
      title: unwrap(n?.title),
      desc: unwrap(n?.desc),
      type: unwrap(n?.type),
      time: unwrap(n?.time),
      ipLocation: unwrap(n?.ipLocation),
      userId: user?.userId,
      nickname: user?.nickname,
      avatar: user?.avatar,
      likedCount: interact?.likedCount,
      collectedCount: interact?.collectedCount,
      commentCount: interact?.commentCount,
      shareCount: interact?.shareCount,
      tagList: tags,
      imageCount: (unwrap(n?.imageList) ?? []).length
    }});
  }} catch(e) {{
    return JSON.stringify({{ error: true, message: e.message }});
  }}
}})()
"""
    print(js)


if __name__ == '__main__':
    main()
