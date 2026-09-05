import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(
        description=(
            "Combined X DM inbox scan: merge API (metadata, screen_name) + "
            "DOM (preview, unread, timestamp)"
        )
    )
    parser.parse_args()

    js = """
    (async () => {
      try {
        const passcode = document.querySelector(
          'input[pattern="[0-9]*"][maxlength="1"]'
        );
        if (passcode) return JSON.stringify({ error: true,
          message: 'passcode_required', hint: 'Page is on DM passcode screen; unlock first' });

        const myId = document.cookie.split('; ')
          .find(c => c.startsWith('twid='))?.split('=')[1]
          ?.replace(/^u%3D/, '').replace(/^u=/, '');
        if (!myId) return JSON.stringify({ error: true,
          message: 'twid cookie missing - not logged in' });

        const AUTH = 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D' +
          '1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA';
        const csrf = document.cookie.split('; ')
          .find(c => c.startsWith('ct0='))?.split('=')[1];
        const headers = {
          'authorization': AUTH,
          'x-csrf-token': csrf,
          'accept': 'application/graphql-response+json, application/json',
          'apollo-require-preflight': 'true'
        };

        const apiUrl =
          'https://api.x.com/graphql/eovtSNDuKOzRLKXV4yWcow/' +
          'GetInitialXChatPageQuery?variables=' +
          encodeURIComponent(JSON.stringify({
            max_local_sequence_id: null,
            query_settings: {
              conversation_event_limit: 200,
              inbox_conversation_event_limit: 5,
              inbox_conversation_limit: 20,
              user_event_limit: 500
            },
            message_pull_version: null
          }));
        const r = await fetch(apiUrl, { credentials: 'include', headers });
        if (!r.ok) return JSON.stringify({ error: true,
          message: 'inbox API HTTP ' + r.status });
        const j = await r.json();
        const page = j?.data?.get_initial_chat_page;
        if (!page) return JSON.stringify({ error: true,
          message: 'unexpected API response shape' });

        const apiByConvId = {};
        (page.items || []).forEach(it => {
          const d = it.conversation_detail || {};
          const parts = (d.participants_results || []).map(p => {
            const u = p.result || {};
            return {
              user_id: u.rest_id,
              name: u.core?.name,
              screen_name: u.core?.screen_name,
              avatar_url: u.avatar?.image_url,
              is_blue_verified: u.verification?.is_blue_verified,
              is_verified_organization: u.verification?.is_verified_organization,
              can_dm: u.chat_permissions?.can_dm,
              can_dm_reason: u.chat_permissions?.can_dm_reason
            };
          });
          const peers = parts.filter(p => p.user_id !== myId);
          apiByConvId[d.conversation_id] = {
            is_muted: d.is_muted,
            is_deleted_by_viewer: it.is_deleted_by_viewer,
            peer: peers[0] || null,
            participants: parts
          };
        });

        const items = document.querySelectorAll(
          '[data-testid^="dm-conversation-item-"]'
        );
        const merged = [];
        items.forEach(el => {
          const testid = el.getAttribute('data-testid');
          const convId = testid.replace('dm-conversation-item-', '');
          const apiData = apiByConvId[convId] || {};

          const anchor = el.querySelector('a[href*="/chat/"]');
          const href = anchor?.getAttribute('href');

          const texts = [];
          const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
          let n;
          while (n = walker.nextNode()) {
            const t = n.textContent.trim();
            if (t) texts.push(t);
          }
          const peerName = texts[0];
          const timestamp = texts[1];
          let youPrefix = false;
          let preview = texts[2] || '';
          if (preview === 'You:' && texts.length >= 4) {
            youPrefix = true;
            preview = texts[3];
          }

          const unread = !!el.querySelector(
            'svg[data-icon="icon-circle-fill"].text-primary'
          );

          merged.push({
            conversation_id: convId,
            conversation_url: href,
            peer_user_id: apiData.peer?.user_id,
            peer_screen_name: apiData.peer?.screen_name,
            peer_display_name: apiData.peer?.name || peerName,
            peer_avatar_url: apiData.peer?.avatar_url,
            peer_is_blue_verified: apiData.peer?.is_blue_verified,
            peer_can_dm: apiData.peer?.can_dm,
            peer_can_dm_reason: apiData.peer?.can_dm_reason,
            is_muted: apiData.is_muted,
            is_deleted_by_viewer: apiData.is_deleted_by_viewer,
            latest_message_timestamp: timestamp,
            latest_message_preview: preview,
            latest_message_from_self: youPrefix,
            unread
          });
        });

        const cursor = page.inboxCursor || {};
        const isEnd = cursor.__typename === 'XChatGetInboxPageEndCursor';

        return JSON.stringify({
          my_user_id: myId,
          count: merged.length,
          unread_count: merged.filter(m => m.unread).length,
          items: merged,
          next_cursor: isEnd ? null : {
            cursor_id: cursor.cursor_id,
            graph_snapshot_id: cursor.graph_snapshot_id
          },
          message_requests_count: page.message_requests_count
        });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message,
          stack: (e.stack||'').slice(0, 300) });
      }
    })()
    """
    print(js)


if __name__ == "__main__":
    main()
