import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description=(
            'Fetch X DM inbox via GraphQL API (metadata only, '
            'message bodies are E2E encrypted)'
        )
    )
    parser.add_argument(
        '--cursor-id',
        default='',
        help='Pagination cursor_id from previous response inboxCursor; empty for first page',
    )
    parser.add_argument(
        '--graph-snapshot-id',
        default='',
        help='Pagination graph_snapshot_id from previous response; empty for first page',
    )
    parser.add_argument('--limit', type=int, default=20, help='Conversations per page')
    args = parser.parse_args()

    js = f"""
    (async () => {{
      try {{
        const AUTH =
          'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7' +
          'tfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA';
        const csrf = document.cookie.split('; ').find(c => c.startsWith('ct0='))?.split('=')[1];
        if (!csrf) {{
          return JSON.stringify({{ error: true, message: 'ct0 cookie missing - not logged in' }});
        }}
        const headers = {{
          'authorization': AUTH,
          'x-csrf-token': csrf,
          'accept': 'application/graphql-response+json, application/json',
          'apollo-require-preflight': 'true'
        }};
        const cursorId = {repr(args.cursor_id)};
        const graphSnap = {repr(args.graph_snapshot_id)};
        const limit = {args.limit};

        let url;
        if (cursorId && graphSnap) {{
          url =
            'https://api.x.com/graphql/udvEZwRtFbZht-atludZcw/GetInboxPageRequestQuery?variables='
            +
            encodeURIComponent(JSON.stringify({{
              continue_cursor: {{ cursor_id: cursorId, graph_snapshot_id: graphSnap }},
              query_settings: {{
                conversation_event_limit: 200,
                inbox_conversation_event_limit: 5,
                inbox_conversation_limit: limit,
                user_event_limit: 500,
              }}
            }}));
        }} else {{
          url =
            'https://api.x.com/graphql/eovtSNDuKOzRLKXV4yWcow/GetInitialXChatPageQuery?variables='
            +
            encodeURIComponent(JSON.stringify({{
              max_local_sequence_id: null,
              query_settings: {{
                conversation_event_limit: 200,
                inbox_conversation_event_limit: 5,
                inbox_conversation_limit: limit,
                user_event_limit: 500,
              }},
              message_pull_version: null
            }}));
        }}

        const r = await fetch(url, {{ credentials: 'include', headers }});
        if (!r.ok) return JSON.stringify({{ error: true, message: 'HTTP ' + r.status }});
        const j = await r.json();
        const page = j?.data?.get_initial_chat_page || j?.data?.get_inbox_page;
        if (!page) {{
          return JSON.stringify({{
            error: true,
            message: 'unexpected response shape',
            sample: JSON.stringify(j).slice(0, 300)
          }});
        }}
        const myId = document.cookie.split('; ')
          .find(c => c.startsWith('twid='))?.split('=')[1]
          ?.replace(/^u%3D/, '').replace(/^u=/, '');
        const items = (page.items || []).map(it => {{
          const d = it.conversation_detail || {{}};
          const parts = (d.participants_results || []).map(p => {{
            const u = p.result || {{}};
            return {{
              user_id: u.rest_id,
              name: u.core?.name,
              screen_name: u.core?.screen_name,
              avatar_url: u.avatar?.image_url,
              is_blue_verified: u.verification?.is_blue_verified,
              is_verified_organization: u.verification?.is_verified_organization,
              can_dm: u.chat_permissions?.can_dm,
              can_dm_on_xchat: u.chat_permissions?.can_dm_on_xchat,
              can_dm_reason: u.chat_permissions?.can_dm_reason,
              is_trusted: u.chat_permissions?.is_trusted,
              protected: u.privacy?.protected,
              suspended: u.privacy?.suspended
            }};
          }});
          const peers = parts.filter(p => p.user_id !== myId);
          return {{
            conversation_id: d.conversation_id,
            is_muted: d.is_muted,
            is_deleted_by_viewer: it.is_deleted_by_viewer,
            has_more: it.has_more,
            participants: parts,
            peer_user_id: peers[0]?.user_id,
            peer_screen_name: peers[0]?.screen_name,
            peer_name: peers[0]?.name
          }};
        }});

        const cursor = page.inboxCursor || {{}};
        const isEnd = cursor.__typename === 'XChatGetInboxPageEndCursor';
        return JSON.stringify({{
          my_user_id: myId,
          count: items.length,
          items,
          next_cursor: isEnd
            ? null
            : {{ cursor_id: cursor.cursor_id, graph_snapshot_id: cursor.graph_snapshot_id }},
          message_requests_count: page.message_requests_count
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
