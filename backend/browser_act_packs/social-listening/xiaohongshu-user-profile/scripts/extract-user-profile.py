import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('user_id', help='user ID from note detail or profile URL')
    args = parser.parse_args()

    js = f"""
(function() {{
  try {{
    const unwrap = v => v?.value !== undefined ? v.value : v?._value !== undefined ? v._value : v;
    const pageData = unwrap(window.__INITIAL_STATE__?.user?.userPageData);
    if (!pageData) {{
      return JSON.stringify({{ error: true, message: 'userPageData not found — '
        + 'verify page is a user profile page' }});
    }}
    const basic = unwrap(pageData?.basicInfo);
    const interactions = unwrap(pageData?.interactions);
    const tags = (unwrap(pageData?.tags) ?? []).map(t => {{
      const ut = unwrap(t);
      return unwrap(ut?.name);
    }}).filter(Boolean);
    if (!basic) {{
      return JSON.stringify({{ error: true, message: 'basicInfo not found for user: '
        + '{args.user_id}' }});
    }}
    const interMap = {{}};
    (interactions ?? []).forEach(i => {{
      const ui = unwrap(i);
      interMap[unwrap(ui?.type)] = unwrap(ui?.count);
    }});
    return JSON.stringify({{
      userId: '{args.user_id}',
      nickname: unwrap(basic?.nickname),
      desc: unwrap(basic?.desc),
      gender: unwrap(basic?.gender),
      ipLocation: unwrap(basic?.ipLocation),
      avatar: unwrap(basic?.imageb) || unwrap(basic?.images),
      follows: interMap['follows'],
      fans: interMap['fans'],
      interaction: interMap['interaction'],
      tags: tags
    }});
  }} catch(e) {{
    return JSON.stringify({{ error: true, message: e.message }});
  }}
}})()
"""
    print(js)


if __name__ == '__main__':
    main()
