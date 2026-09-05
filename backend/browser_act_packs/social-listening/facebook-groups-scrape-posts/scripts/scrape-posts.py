import argparse
import json
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description='Scrape posts from a Facebook group (must be navigated to first)'
    )
    parser.add_argument(
        '--sort',
        default='CHRONOLOGICAL',
        choices=['TOP_POSTS', 'CHRONOLOGICAL', 'RECENT_ACTIVITY'],
        help=(
            'Sort order: TOP_POSTS (most relevant), CHRONOLOGICAL (newest posts), '
            'RECENT_ACTIVITY (latest activity)'
        ),
    )
    parser.add_argument(
        '--count',
        type=int,
        default=20,
        help='Desired number of posts (actual may be less if feed exhausts)',
    )
    parser.add_argument(
        '--max-pages',
        type=int,
        default=100,
        help='Safety cap on pagination requests',
    )
    parser.add_argument(
        '--doc-id',
        default='26577462205242925',
        help=(
            'GraphQL persisted query doc_id for GroupsCometFeedRegularStoriesPaginationQuery. '
            'Update if FB rotates.'
        ),
    )
    args = parser.parse_args()

    sort_js = json.dumps(args.sort)
    count_js = int(args.count)
    max_pages_js = int(args.max_pages)
    doc_id_js = json.dumps(args.doc_id)

    js = f"""(async () => {{
  try {{
    const NL = String.fromCharCode(10);
    const dt = require('DTSGInitialData').token;
    const lsd = require('LSD').token;
    const sd = require('SiteData');
    const uid = require('CurrentUserInitialData').USER_ID;
    const SORT = {sort_js};
    const TARGET_COUNT = {count_js};
    const MAX_PAGES = {max_pages_js};
    const DOC_ID = {doc_id_js};

    // Resolve group numeric ID from current page HTML
    const html = document.body.innerHTML;
    let groupId = null;
    {{
      const m1 = html.match(/\"groupID\":\"(\\d+)\"/);
      const m2 = html.match(/\"group_id\":\"(\\d+)\"/);
      groupId = m1 ? m1[1] : (m2 ? m2[1] : null);
    }}
    if (!groupId) {{
      return JSON.stringify({{ ok: false, error:
        'Could not resolve groupID from current page. Navigate to a Facebook group URL first.' }});
    }}

    // Resolve groupName heuristically from embedded group object in HTML
    const groupName = (html.match(/"groupID":"\\d+","name":"([^"]{{1,120}})"/) || [])[1] || null;

    function buildVars(cursor) {{
      return {{
        count: 3,
        cursor: cursor,
        feedLocation: 'GROUP',
        feedType: 'DISCUSSION',
        feedbackSource: 0,
        filterTopicId: null,
        focusCommentID: null,
        privacySelectorRenderLocation: 'COMET_STREAM',
        referringStoryRenderLocation: null,
        renderLocation: 'group',
        scale: 1,
        sortingSetting: SORT,
        stream_initial_count: 1,
        useDefaultActor: false,
        id: String(groupId),
        '__relay_internal__pv__CometFeedStory_enable_post_permalink_white_space_clickrelayprovider':
          false,
        '__relay_internal__pv__CometImmersivePhotoCanUserDisable3DMotionrelayprovider': false,
        '__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider': false,
        '__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider': 'ORIGINAL',
        '__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider': false,
        '__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider': false,
        '__relay_internal__pv__CometUFIShareActionMigrationrelayprovider': true,
        '__relay_internal__pv__CometUFISingleLineUFIrelayprovider': true,
        '__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider': true,
        '__relay_internal__pv__FBReelsIFUTileContent_reelsIFUPlayOnHoverrelayprovider': true,
        '__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider': true,
        '__relay_internal__pv__FBReels_deprecate_short_form_video_context_gkrelayprovider': true,
        '__relay_internal__pv__FBReels_enable_view_dubbed_audio_type_gkrelayprovider': true,
        '__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider': true,
        '__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider': true,
        '__relay_internal__pv__GroupsCometGYSJFeedItemHeightrelayprovider': 206,
        '__relay_internal__pv__IsMergQAPollsrelayprovider': false,
        '__relay_internal__pv__IsWorkUserrelayprovider': false,
        '__relay_internal__pv__ShouldEnableBakedInTextStoriesrelayprovider': true,
        '__relay_internal__pv__StoriesShouldIncludeFbNotesrelayprovider': true,
        '__relay_internal__pv__TestPilotShouldIncludeDemoAdUseCaserelayprovider': false,
        '__relay_internal__pv__WorkCometIsEmployeeGKProviderrelayprovider': false
      }};
    }}

    async function fetchPage(cursor) {{
      const variables = buildVars(cursor);
      const b = new URLSearchParams();
      b.set('av', '0'); b.set('__aaid', '0'); b.set('__user', uid);
      b.set('__a', '1'); b.set('__req', 'a');
      b.set('__hs', sd.haste_session); b.set('dpr', '1'); b.set('__ccg', 'UNKNOWN');
      b.set('__rev', String(sd.__spin_r)); b.set('__spin_r', String(sd.__spin_r));
      b.set('__spin_b', sd.__spin_b); b.set('__spin_t', String(sd.__spin_t));
      b.set('__hsi', String(sd.hsi)); b.set('__comet_req', '15');
      b.set('fb_dtsg', dt); b.set('jazoest', '22166'); b.set('lsd', lsd);
      b.set('__crn', 'comet.fbweb.CometGroupDiscussionRoute');
      b.set('fb_api_caller_class', 'RelayModern');
      b.set('fb_api_req_friendly_name', 'GroupsCometFeedRegularStoriesPaginationQuery');
      b.set('variables', JSON.stringify(variables));
      b.set('server_timestamps', 'true');
      b.set('doc_id', DOC_ID);
      const r = await fetch('/api/graphql/', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/x-www-form-urlencoded', 'X-FB-LSD': lsd }},
        body: b.toString(),
        credentials: 'include'
      }});
      const t = await r.text();
      const lines = t.split(NL).filter(l => l.trim());
      const edgeMap = {{}};
      let pageInfo = null, err = null;
      for (const l of lines) {{
        try {{
          const j = JSON.parse(l);
          if (j.errors) err = j.errors[0] && j.errors[0].message;
          if (j.data && j.data.node && j.data.node.group_feed && j.data.node.group_feed.edges) {{
            j.data.node.group_feed.edges.forEach((e, i) => {{ edgeMap[i] = e; }});
          }}
          if (j.label && j.label.indexOf(
            'GroupsCometFeedRegularStories_paginationGroup$stream'
          ) === 0) {{
            const idx = j.path[j.path.length - 1];
            edgeMap[idx] = j.data;
          }}
          if (j.data && j.data.page_info) pageInfo = j.data.page_info;
          if (j.data && j.data.node && j.data.node.group_feed
              && j.data.node.group_feed.page_info) {{
            pageInfo = j.data.node.group_feed.page_info;
          }}
        }} catch (e) {{ }}
      }}
      const edges = Object.keys(edgeMap).sort((a, b) => Number(a) - Number(b)).map(k => edgeMap[k]);
      return {{ edges, pageInfo, err, httpStatus: r.status }};
    }}

    function findFirst(obj, key, predicate) {{
      if (!obj || typeof obj !== 'object') return null;
      if (Array.isArray(obj)) {{
        for (const x of obj) {{
          const r = findFirst(x, key, predicate);
          if (r !== null && r !== undefined) return r;
        }}
        return null;
      }}
      for (const [k, v] of Object.entries(obj)) {{
        if (k === key) {{ if (!predicate || predicate(v)) return v; }}
        if (v && typeof v === 'object') {{
          const r = findFirst(v, key, predicate);
          if (r !== null && r !== undefined) return r;
        }}
      }}
      return null;
    }}
    function findAll(obj, key, out) {{
      out = out || [];
      if (!obj || typeof obj !== 'object') return out;
      if (Array.isArray(obj)) {{ for (const x of obj) findAll(x, key, out); return out; }}
      for (const [k, v] of Object.entries(obj)) {{
        if (k === key) out.push(v);
        if (v && typeof v === 'object') findAll(v, key, out);
      }}
      return out;
    }}

    function extractPost(edge) {{
      if (!edge || !edge.node) return null;
      const n = edge.node;
      if (!n.post_id) return null;

      let messageText = null;
      const msg = findFirst(
        n, 'message', v => v && typeof v === 'object' && typeof v.text === 'string'
      );
      if (msg) messageText = msg.text;

      let createdAt = null;
      try {{
        createdAt = n.comet_sections && n.comet_sections.timestamp
          && n.comet_sections.timestamp.story
          && n.comet_sections.timestamp.story.creation_time || null;
      }} catch (e) {{ }}
      if (!createdAt) {{
        const times = findAll(n, 'creation_time').filter(t => typeof t === 'number');
        if (times.length) createdAt = times[0];
      }}

      let author = null;
      try {{
        const a = n.actors && n.actors[0];
        if (a) {{
          const pic = findFirst(a, 'profile_picture');
          author = {{
            id: a.id, name: a.name, profile_picture: pic && pic.uri || null,
            url: a.url || null
          }};
        }}
      }} catch (e) {{ }}

      let group = null;
      try {{
        group = n.to ? {{
          id: n.to.id, name: n.to.name, url: n.to.url
        }} : null;
      }} catch (e) {{ }}

      let reactionCount = 0, reactionI18n = null, shareCount = 0;
      let shareI18n = null, commentCount = 0;
      let topReactions = [];
      try {{
        const renderers = findFirst(n, 'adaptive_ufi_action_renderers');
        if (Array.isArray(renderers)) {{
          for (const r of renderers) {{
            const fb = r && r.feedback || {{}};
            if (fb.reaction_count && typeof fb.reaction_count.count === 'number') {{
              reactionCount = Math.max(reactionCount, fb.reaction_count.count);
            }}
            if (fb.share_count && typeof fb.share_count.count === 'number') {{
              shareCount = Math.max(shareCount, fb.share_count.count);
            }}
            if (fb.i18n_share_count != null) shareI18n = String(fb.i18n_share_count);
          }}
        }}
      }} catch (e) {{ }}

      try {{
        const tr = findFirst(n, 'top_reactions');
        if (tr && Array.isArray(tr.edges)) {{
          topReactions = tr.edges.map(e => ({{
            name: e && e.node && e.node.localized_name || null,
            reaction_id: e && e.node && e.node.id || null,
            count: e && (e.reaction_count != null ? e.reaction_count : null)
          }}));
        }}
      }} catch (e) {{ }}

      try {{
        const i18n = findFirst(n, 'i18n_reaction_count', v => v != null);
        if (i18n != null) reactionI18n = String(i18n);
      }} catch (e) {{ }}

      try {{
        const counts = findAll(n, 'comments')
          .map(c => c && typeof c === 'object' && typeof c.total_count === 'number'
            ? c.total_count : null)
          .filter(v => v != null);
        if (counts.length) commentCount = Math.max.apply(null, counts);
      }} catch (e) {{ }}

      const mediaItems = [];
      try {{
        const atts = n.attachments || [];
        for (const att of atts) {{
          const media = att && att.styles && att.styles.attachment
            && att.styles.attachment.media || (att && att.media) || null;
          if (!media) continue;
          const item = {{ __typename: media.__typename || null, id: media.id || null }};
          const photoImage = findFirst(media, 'photo_image');
          if (photoImage && photoImage.uri) item.photo_image = photoImage.uri;
          const image = findFirst(media, 'image');
          if (image && typeof image === 'object' && image.uri) item.image = image.uri;
          if (media.playable_url) item.playable_url = media.playable_url;
          if (media.playable_url_quality_hd) item.playable_url_hd = media.playable_url_quality_hd;
          const thumb = findFirst(media, 'preferred_thumbnail');
          if (thumb) {{ const tu = findFirst(thumb, 'uri'); if (tu) item.thumbnail = tu; }}
          mediaItems.push(item);
        }}
      }} catch (e) {{ }}

      return {{
        post_id: n.post_id || null,
        cache_id: n.cache_id || null,
        id: n.id || null,
        permalink_url: n.permalink_url || null,
        creation_time: createdAt,
        message: messageText,
        author: author,
        group: group,
        reactions: {{
          total: reactionCount, total_formatted: reactionI18n, breakdown: topReactions
        }},
        share_count: shareCount,
        share_count_formatted: shareI18n,
        comment_count: commentCount,
        media: mediaItems
      }};
    }}

    const posts = [];
    let cursor = null;
    let pageIdx = 0;
    const diagnostics = {{ pages: [] }};
    while (posts.length < TARGET_COUNT && pageIdx < MAX_PAGES) {{
      const res = await fetchPage(cursor);
      diagnostics.pages.push({{
        pageIdx, httpStatus: res.httpStatus, edgeCount: res.edges.length,
        err: res.err, hasNext: res.pageInfo && res.pageInfo.has_next_page
      }});
      if (res.err) {{ diagnostics.errorAt = pageIdx; break; }}
      for (const e of res.edges) {{
        const p = extractPost(e);
        if (p && p.post_id) posts.push(p);
        if (posts.length >= TARGET_COUNT) break;
      }}
      if (!res.pageInfo || !res.pageInfo.has_next_page) break;
      cursor = res.pageInfo.end_cursor;
      pageIdx++;
    }}

    return JSON.stringify({{
      ok: true,
      group_id: groupId,
      group_name: groupName,
      sort: SORT,
      total: posts.length,
      posts: posts,
      diagnostics: diagnostics
    }});
  }} catch (e) {{
    return JSON.stringify({{ ok: false, error: e.message,
      stack: e.stack ? e.stack.slice(0, 500) : null }});
  }}
}})()
"""

    print(js)


if __name__ == '__main__':
    main()
