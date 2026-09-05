import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description='Search X users via GraphQL TypeaheadXChatQuery; returns candidate users '
        'with DM permission info'
    )
    parser.add_argument('query', help='Search query (name, screen_name or partial)')
    args = parser.parse_args()

    js = f"""
    (async () => {{
      try {{
        const AUTH = 'Bearer ' +
          'AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8L' +
          'F81IUq16cHjhLTvJu4FA33AGWWjCpTnA';
        const csrf = document.cookie.split('; ').find(
          c => c.startsWith('ct0=')
        )?.split('=')[1];
        if (!csrf) {{
          return JSON.stringify({{
            error: true,
            message: 'ct0 cookie missing - not logged in'
          }});
        }}

        const url = 'https://api.x.com/graphql/mYbJ7Qq9UAtG-68VAiBpYA/'
          + 'TypeaheadXChatQuery?variables='
          + encodeURIComponent(JSON.stringify({{
            query: {repr(args.query)},
            include_group_check: true,
            conv_id: null,
            surface: 'NewDm'
          }}));

        const r = await fetch(url, {{
          credentials: 'include',
          headers: {{
            'authorization': AUTH,
            'x-csrf-token': csrf,
            'accept': 'application/graphql-response+json, application/json',
            'apollo-require-preflight': 'true'
          }}
        }});
        if (!r.ok) return JSON.stringify({{ error: true, message: 'HTTP ' + r.status }});
        const j = await r.json();
        const hits = j?.data?.search_by_raw_query?.x_chat_users_typeahead || [];

        const users = hits.map(h => {{
          const u = h.typeahead_user?.user_results?.result || {{}};
          return {{
            user_id: u.rest_id,
            name: u.core?.name,
            screen_name: u.core?.screen_name,
            avatar_url: u.avatar?.image_url,
            is_blue_verified: u.verification?.is_blue_verified,
            is_verified_organization: u.verification?.is_verified_organization,
            verified_type: u.verification?.verified_type,
            can_dm: u.chat_permissions?.can_dm,
            can_dm_on_xchat: u.chat_permissions?.can_dm_on_xchat,
            can_dm_reason: u.chat_permissions?.can_dm_reason,
            protected: u.privacy?.protected,
            suspended: u.privacy?.suspended
          }};
        }});

        return JSON.stringify({{
          query: {repr(args.query)},
          count: users.length,
          users
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
