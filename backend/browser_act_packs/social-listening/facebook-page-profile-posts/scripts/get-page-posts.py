import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'page_id'
    )  # Numeric Facebook page/profile ID
    parser.add_argument(
        '--page-url', default=''
    )  # Original input URL (used as inputUrl / pageName)
    parser.add_argument(
        '--cursor', default='null'
    )  # Pagination cursor; null for first page
    parser.add_argument(
        '--after-time', default='null'
    )  # Unix timestamp: only posts after this
    parser.add_argument(
        '--before-time', default='null'
    )  # Unix timestamp: only posts before this
    parser.add_argument(
        '--count', default='5'
    )  # Posts per batch (1-10 recommended)
    args = parser.parse_args()
    feed_flag_key = (
        "'__relay_internal__pv__CometFeedStory"
        "DangerouslySetInnerFeedItemDisplayContent"
        "relayprovider'"
    )
    reels_flag_key = (
        "'__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_"
        "gkv2relayprovider'"
    )

    cursor_val = 'null' if args.cursor == 'null' else repr(args.cursor)
    after_val  = args.after_time  if args.after_time  == 'null' else args.after_time
    before_val = args.before_time if args.before_time == 'null' else args.before_time
    page_url   = args.page_url.replace("'", "\\'")

    # Surrogate sanitization regex — expressed as raw string to avoid Python interpreting \uD800
    surrogate_regex = r'/[\uD800-\uDFFF]/g'

    js = f"""
(async function() {{
  try {{
    const fbDtsg = require('DTSGInitData')?.token || '';
    let lsd = '';
    const allScripts = Array.from(
      document.querySelectorAll('script')
    ).map(s => s.textContent).join('');
    const lsdM = allScripts.match(/"LSD"[^}}]*"token":"([^"]+)"/);
    if (lsdM) lsd = lsdM[1];

    const variables = {{
      afterTime: {after_val},
      beforeTime: {before_val},
      count: {args.count},
      cursor: {cursor_val},
      feedLocation: 'TIMELINE',
      feedbackSource: 0,
      focusCommentID: null,
      memorializedSplitTimeFilter: null,
      omitPinnedPost: true,
      postedBy: {{ group: 'OWNER' }},
      privacy: null,
      privacySelectorRenderLocation: 'COMET_STREAM',
      referringStoryRenderLocation: null,
      renderLocation: 'timeline',
      scale: 1,
      stream_count: 1,
      taggedInOnly: null,
      trackingCode: null,
      useDefaultActor: false,
      id: '{args.page_id}',
      '__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider': true,
      '__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider': true,
      '__relay_internal__pv__IsWorkUserrelayprovider': false,
      '__relay_internal__pv__IsMergQAPollsrelayprovider': false,
      '__relay_internal__pv__FBReelsEnableVideoWindowedReplayrelayprovider': false,
      '__relay_internal__pv__StoriesArmadilloReplyEnabledrelayprovider': false,
      '__relay_internal__pv__EventCometCardImage_prefetchEventImagerelayprovider': false,
      '__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider': false,
      '__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider': false,
      '__relay_internal__pv__CometUFIShareActionMigrationrelayprovider': true,
      '__relay_internal__pv__IncludeCommentWithAttachmentrelayprovider': true,
      '__relay_internal__pv__GHLShouldUpdateVideoPreviewImagerelayprovider': false,
      '__relay_internal__pv__GHLVideoTimestampOnShortsrelayprovider': false,
      {feed_flag_key}: false,
      '__relay_internal__pv__StoriesRingrelayprovider': false,
      '__relay_internal__pv__UseCometRouter_cometRouterrelayprovider': false,
      '__relay_internal__pv__FBReelsFeedbackActionsrelayprovider': false,
      {reels_flag_key}: false,
      '__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNameForFeedV2relayprovider': true,
      '__relay_internal__pv__GHLShouldChangeAdIdFieldNameForFeedV2relayprovider': true
    }};

    const bodyParams = new URLSearchParams({{
      fb_dtsg: fbDtsg,
      lsd: lsd,
      variables: JSON.stringify(variables),
      doc_id: '27278869228466784',
      server_timestamps: 'true',
      fb_api_caller_class: 'RelayModern',
      fb_api_req_friendly_name: 'ProfileCometTimelineFeedRefetchQuery'
    }});

    const resp = await fetch('https://www.facebook.com/api/graphql/', {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-FB-LSD': lsd
      }},
      body: bodyParams.toString()
    }});

    if (!resp.ok) return JSON.stringify({{ error: true,
      message: 'HTTP ' + resp.status }});

    const text = await resp.text();
    const lines = text.split('\\n').filter(l => l.trim().startsWith('{{'));
    if (lines.length < 2) return JSON.stringify({{ error: true,
      message: 'Unexpected response format', raw: text.slice(0, 200) }});

    const inputUrl = '{page_url}';
    const pageId   = '{args.page_id}';

    // Derive pageName from inputUrl slug (last non-empty path segment)
    function getPageName(url) {{
      if (!url) return null;
      try {{
        const parts = new URL(url).pathname.split('/').filter(Boolean);
        return parts[0] || null;
      }} catch(e) {{ return null; }}
    }}
    const pageName = getPageName(inputUrl);

    // --- UFI extractor (likes / comments / shares / reactions) ---
    function getUFI(node) {{
      return node?.comet_sections?.feedback?.story?.story_ufi_container?.story
        ?.feedback_context?.feedback_target_with_context
        ?.comet_ufi_summary_and_actions_renderer?.feedback;
    }}

    // --- Reaction breakdown as flat counts ---
    function flatReactions(topReactions) {{
      const map = {{}};
      (topReactions || []).forEach(r => {{ map[r.name] = r.count; }});
      return {{
        reactionLikeCount:  map['Like']  ?? null,
        reactionLoveCount:  map['Love']  ?? null,
        reactionCareCount:  map['Care']  ?? null,
        reactionWowCount:   map['Wow']   ?? null,
        reactionHahaCount:  map['Haha']  ?? null,
        reactionSadCount:   map['Sad']   ?? null,
        reactionAngryCount: map['Angry'] ?? null
      }};
    }}

    // --- Media extractor ---
    function extractMedia(node) {{
      const atts = node?.attachments;
      if (!atts || atts.length === 0) return [];
      return atts.map(att => {{
        const m = att?.styles?.attachment?.media;
        if (!m) return null;
        const t = m.__typename;
        const base = {{ __typename: t, id: m.id || null }};

        if (t === 'Photo') {{
          const pi = m.photo_image;
          return Object.assign(base, {{
            thumbnail:  pi?.uri       || null,
            photo_image: pi ? {{ uri: pi.uri, width: pi.width, height: pi.height }} : null,
            url:         m.url        || null,
            ocrText:     m.accessibility_caption || null,
            feedback: m.feedback ? {{
              can_viewer_comment: m.feedback.can_viewer_comment ?? null,
              id: m.feedback.id || null
            }} : null
          }});
        }}

        if (t === 'Video' || t === 'UnifiedVideo') {{
          const legacy = m.videoDeliveryLegacyFields;
          const thumb  = m.preferred_thumbnail?.image?.uri || m.first_frame_thumbnail || null;
          return Object.assign(base, {{
            thumbnail:      thumb,
            url:            m.url || null,
            playableUrlSd:  legacy?.browser_native_sd_url || null,
            playableUrlHd:  legacy?.browser_native_hd_url || null,
            viewsCount:     null
          }});
        }}

        return Object.assign(base, {{ url: m.url || null }});
      }}).filter(Boolean);
    }}

    // --- Core post extractor ---
    function extractPost(node) {{
      if (!node || !node.post_id) return null;

      const ufi       = getUFI(node);
      const renderers = ufi?.adaptive_ufi_action_renderers;

      // Message & textReferences
      const msg = node?.comet_sections?.content?.story?.comet_sections?.message?.story?.message
               || node?.comet_sections?.content?.story?.message;
      const ranges = (msg?.ranges || []).map(r => ({{
        type:   r.entity?.__typename,
        url:    r.entity?.__typename === 'ExternalUrl' ? r.entity.external_url : r.entity?.url,
        offset: r.offset,
        length: r.length
      }}));
      const link = ranges.find(r => r.type === 'ExternalUrl')?.url || null;

      // Actors (from content story -- has profile_picture)
      const contentActors = node?.comet_sections?.content?.story?.actors;
      const actor0 = contentActors?.[0];

      // Collaborators
      const collabs = (
        node?.comet_sections?.context_layout?.story?.comet_sections?.title?.story?.collaborators
        || []
      ).map(c => ({{ id: c?.id || null, name: c?.name || null,
        profileUrl: c?.url || null }}));

      // Page ad library
      const delegatePage = node?.comet_sections?.context_layout?.story
        ?.comet_sections?.actor_photo?.story?.actors?.[0]?.delegate_page;
      const pageAdLibrary = delegatePage ? {{
        is_business_page_active: delegatePage.is_business_page_active ?? null,
        id: delegatePage.id || null
      }} : null;

      // Reaction counts
      const topReactions = (ufi?.top_reactions?.edges || []).map(e => ({{
        name:  e.node?.localized_name,
        count: e.reaction_count
      }}));
      const flat = flatReactions(topReactions);

      const likes    = renderers?.[0]?.feedback?.reaction_count?.count ?? null;
      const comments = renderers?.[1]?.feedback?.comment_rendering_instance
        ?.comments?.total_count ?? null;
      const shares   = renderers?.[2]?.feedback?.share_count?.count ?? null;

      const creationTime = node?.comet_sections?.timestamp?.story?.creation_time || null;
      const facebookId   = actor0?.id || null;
      const topLevelUrl  = facebookId && node.post_id
        ? `https://www.facebook.com/${{facebookId}}/posts/${{node.post_id}}`
        : null;

      return {{
        facebookUrl: inputUrl || null,
        postId:      node.post_id,
        pageName:    pageName,
        url:         node.permalink_url || null,
        time:        creationTime ? new Date(creationTime * 1000).toISOString() : null,
        timestamp:   creationTime,
        user: {{
          id:         actor0?.id || null,
          name:       actor0?.name || null,
          profileUrl: actor0?.url || null,
          profilePic: actor0?.profile_picture?.uri || null
        }},
        collaborators: collabs,
        text:          msg?.text || null,
        textReferences: ranges,
        link,
        likes,
        comments,
        shares,
        topReactions,
        topReactionsCount: likes,
        ...flat,
        media:         extractMedia(node),
        feedbackId:    ufi?.id || null,
        facebookId,
        topLevelUrl,
        pageAdLibrary,
        inputUrl:      inputUrl || null
      }};
    }}

    const posts = [];

    // Line 0: initial edges (often contains pinned post area)
    try {{
      const line0 = JSON.parse(lines[0]);
      const edges = line0?.data?.node?.timeline_list_feed_units?.edges || [];
      edges.forEach(e => {{
        const p = extractPost(e?.node);
        if (p) posts.push(p);
      }});
    }} catch(e) {{}}

    // Lines 1 to N-2: streaming post units
    for (let i = 1; i < lines.length - 1; i++) {{
      try {{
        const j = JSON.parse(lines[i]);
        const p = extractPost(j?.data?.node);
        if (p) posts.push(p);
      }} catch(e) {{}}
    }}

    // Last line: pagination info
    let endCursor   = null;
    let hasNextPage = false;
    try {{
      const last = JSON.parse(lines[lines.length - 1]);
      endCursor   = last?.data?.page_info?.end_cursor   || null;
      hasNextPage = last?.data?.page_info?.has_next_page || false;
    }} catch(e) {{}}

    // Deduplicate by postId
    const seen   = new Set();
    const unique = posts.filter(p => {{
      if (seen.has(p.postId)) return false;
      seen.add(p.postId);
      return true;
    }});

    const result = JSON.stringify({{
      posts: unique,
      pagination: {{ endCursor, hasNextPage }}
    }});
    // Sanitize lone surrogate characters so result is valid UTF-8
    return result.replace({surrogate_regex}, '?');

  }} catch(e) {{
    return JSON.stringify({{ error: true, message: e.message }});
  }}
}})()
"""
    print(js)

if __name__ == '__main__':
    main()
