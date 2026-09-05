import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--limit',
        type=int,
        default=20,
        help='max items to return from current feeds',
    )
    args = parser.parse_args()

    js = f"""
(function() {{
  try {{
    const unwrap = v => v?.value !== undefined ? v.value : v?._value !== undefined ? v._value : v;
    const feeds = unwrap(window.__INITIAL_STATE__?.search?.feeds);
    if (!feeds || !feeds.length) {{
      return JSON.stringify({{ error: true, message: 'feeds not loaded or empty — '
        + 'verify page is a search result page and wait stable has completed' }});
    }}
    const s = window.__INITIAL_STATE__?.search;
    const items = feeds.slice(0, {args.limit}).map(item => {{
      const nc = unwrap(item?.noteCard);
      const user = unwrap(nc?.user);
      const interact = unwrap(nc?.interactInfo);
      const cover = unwrap(nc?.cover);
      return {{
        id: unwrap(item?.id),
        xsecToken: unwrap(item?.xsecToken),
        type: unwrap(nc?.type),
        title: unwrap(nc?.displayTitle),
        userId: user?.userId,
        nickname: user?.nickname,
        likedCount: interact?.likedCount,
        collectedCount: interact?.collectedCount,
        commentCount: interact?.commentCount,
        coverUrl: cover?.url_default
      }};
    }});
    return JSON.stringify({{
      total: feeds.length,
      hasMore: unwrap(s?.hasMore),
      page: unwrap(s?.searchContext)?.page,
      items
    }});
  }} catch(e) {{
    return JSON.stringify({{ error: true, message: e.message }});
  }}
}})()
"""
    print(js)


if __name__ == '__main__':
    main()
