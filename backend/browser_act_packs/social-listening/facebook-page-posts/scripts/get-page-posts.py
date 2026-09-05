import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('page_id')  # Numeric Facebook page ID
    parser.add_argument(
        '--cursor', default='null'
    )  # Pagination cursor (null for first page)
    parser.add_argument(
        '--after-time', default='null'
    )  # Unix timestamp: only posts after this time
    parser.add_argument(
        '--before-time', default='null'
    )  # Unix timestamp: only posts before this time
    parser.add_argument(
        '--count', default='5'
    )  # Number of posts per batch (1-10)
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
    after_val = args.after_time if args.after_time == 'null' else args.after_time
    before_val = args.before_time if args.before_time == 'null' else args.before_time

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

        const body = new URLSearchParams({{
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
          body: body.toString()
        }});

        if (!resp.ok) return JSON.stringify({{ error: true, message: 'HTTP ' + resp.status }});

        const text = await resp.text();
        const lines = text.split('\\n').filter(l => l.trim().startsWith('{{'));
        if (lines.length < 2) return JSON.stringify({{ error: true,
          message: 'Unexpected response format', raw: text.slice(0, 200) }});

        function getUFI(node) {{
          return node?.comet_sections?.feedback?.story?.story_ufi_container?.story
            ?.feedback_context?.feedback_target_with_context
            ?.comet_ufi_summary_and_actions_renderer?.feedback;
        }}

        function extractPost(node) {{
          if (!node || !node.post_id) return null;
          const ufi = getUFI(node);
          const renderers = ufi?.adaptive_ufi_action_renderers;
          const msg = node?.comet_sections?.content?.story?.comet_sections?.message?.story?.message
                   || node?.comet_sections?.content?.story?.message;
          const ranges = msg?.ranges?.map(r => ({{
            type: r.entity?.__typename,
            url: r.entity?.__typename === 'ExternalUrl' ? r.entity.external_url : r.entity?.url,
            offset: r.offset,
            length: r.length
          }})) || [];
          const att = node?.attachments?.[0]?.media;

          return {{
            postId: node.post_id,
            url: node.permalink_url,
            text: msg?.text || null,
            textReferences: ranges,
            creationTime: node?.comet_sections?.timestamp?.story?.creation_time || null,
            user: {{
              id: node?.actors?.[0]?.id || null,
              name: node?.actors?.[0]?.name || null,
              profileUrl: node?.actors?.[0]?.url || null
            }},
            likes: renderers?.[0]?.feedback?.reaction_count?.count ?? null,
            comments: renderers?.[1]?.feedback?.comment_rendering_instance
              ?.comments?.total_count ?? null,
            shares: renderers?.[2]?.feedback?.share_count?.count ?? null,
            topReactions: ufi?.top_reactions?.edges?.map(e => ({{
              name: e.node?.localized_name,
              count: e.reaction_count
            }})) || [],
            topReactionsCount: renderers?.[0]?.feedback?.reaction_count?.count ?? null,
            media: att ? {{
              type: att.__typename,
              id: att.id || null,
              viewsCount: att.video_view_count ?? null
            }} : null,
            feedbackId: ufi?.id || null
          }};
        }}

        const posts = [];

        // Line 0: initial edges (pinned post area)
        try {{
          const line0 = JSON.parse(lines[0]);
          const edges = line0?.data?.node?.timeline_list_feed_units?.edges || [];
          edges.forEach(e => {{
            const p = extractPost(e?.node);
            if (p) posts.push(p);
          }});
        }} catch(e) {{}}

        // Lines 1 to N-1: stream posts
        for (let i = 1; i < lines.length - 1; i++) {{
          try {{
            const j = JSON.parse(lines[i]);
            const p = extractPost(j?.data?.node);
            if (p) posts.push(p);
          }} catch(e) {{}}
        }}

        // Last line: page_info (pagination)
        let endCursor = null;
        let hasNextPage = false;
        try {{
          const last = JSON.parse(lines[lines.length - 1]);
          endCursor = last?.data?.page_info?.end_cursor || null;
          hasNextPage = last?.data?.page_info?.has_next_page || false;
        }} catch(e) {{}}

        // Deduplicate by postId (edges and stream may overlap)
        const seen = new Set();
        const uniquePosts = posts.filter(p => {{
          if (seen.has(p.postId)) return false;
          seen.add(p.postId);
          return true;
        }});

        return JSON.stringify({{
          posts: uniquePosts,
          pagination: {{
            endCursor: endCursor,
            hasNextPage: hasNextPage
          }}
        }});

      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
