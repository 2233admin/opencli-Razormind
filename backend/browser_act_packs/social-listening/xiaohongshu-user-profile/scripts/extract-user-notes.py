import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--limit',
        type=int,
        default=30,
        help='max notes to return from current state',
    )
    args = parser.parse_args()

    js = f"""
(function() {{
  try {{
    const unwrap = v => v?.value !== undefined ? v.value : v?._value !== undefined ? v._value : v;
    const notesRaw = unwrap(window.__INITIAL_STATE__?.user?.notes);
    if (!notesRaw) {{
      return JSON.stringify({{ error: true, message: 'user.notes not found — '
        + 'verify page is a user profile page' }});
    }}
    // notes is an array; notes[0] is a numeric-keyed object map of loaded note items
    const notesMap = Array.isArray(notesRaw) ? notesRaw[0] : notesRaw;
    if (!notesMap) {{
      return JSON.stringify({{ error: true, message: 'notes map is empty' }});
    }}
    const items = Object.values(notesMap)
      .filter(n => n && typeof n === 'object' && unwrap(unwrap(n?.noteCard)?.displayTitle))
      .slice(0, {args.limit})
      .map(n => {{
        const nc = unwrap(n?.noteCard);
        const cover = unwrap(nc?.cover);
        const interact = unwrap(nc?.interactInfo);
        return {{
          id: null,
          type: unwrap(nc?.type),
          title: unwrap(nc?.displayTitle),
          likedCount: unwrap(interact?.likedCount),
          collectedCount: unwrap(interact?.collectedCount),
          commentCount: unwrap(interact?.commentCount),
          coverUrl: unwrap(cover?.url) || unwrap(cover?.url_default)
        }};
      }});
    const hasMore = unwrap(window.__INITIAL_STATE__?.user?.hasMore);
    return JSON.stringify({{
      total: items.length,
      hasMore: hasMore,
      notes: items
    }});
  }} catch(e) {{
    return JSON.stringify({{ error: true, message: e.message }});
  }}
}})()
"""
    print(js)


if __name__ == '__main__':
    main()
