import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')

    js = r"""
    (function() {
      try {
        var articles = document.querySelectorAll('article[data-testid="tweet"]');
        if (!articles.length) {
          return JSON.stringify([]);
        }

        var tweets = [];

        for (var i = 0; i < articles.length; i++) {
          var article = articles[i];
          var fiberKey = null;
          var keys = Object.keys(article);
          for (var ki = 0; ki < keys.length; ki++) {
            if (keys[ki].startsWith('__reactFiber')) { fiberKey = keys[ki]; break; }
          }
          if (!fiberKey) continue;

          var fiber = article[fiberKey];
          var depth = 0;
          var found = false;

          while (fiber && depth < 80 && !found) {
            var props = fiber.memoizedProps;
            if (props && props.tweet) {
              var t = props.tweet;
              var u = t.user || {};

              var media = [];
              var rawMedia = (t.extended_entities &&
                t.extended_entities.media) ? t.extended_entities.media : [];
              for (var mi = 0; mi < rawMedia.length; mi++) {
                var m = rawMedia[mi];
                var variants = [];
                if (m.video_info && m.video_info.variants) {
                  var vs = v.video_info.variants.filter(function(v) {
                    return v.content_type === 'video/mp4';
                  });
                  vs.sort(function(a, b) { return (b.bitrate || 0) - (a.bitrate || 0); });
                  variants = vs.map(function(v) { return { bitrate: v.bitrate, url: v.url }; });
                }
                media.push({
                  type: m.type,
                  url: m.media_url_https,
                  alt_text: m.ext_alt_text || null,
                  video_variants: variants.length ? variants : null
                });
              }

              tweets.push({
                id: t.id_str,
                url: 'https://x.com/' + (u.screen_name || '') + '/status/' + t.id_str,
                text: t.full_text || t.text || '',
                created_at: t.created_at || null,
                lang: t.lang || null,
                author_id: u.id_str || null,
                author_name: u.name || null,
                author_screen_name: u.screen_name || null,
                author_profile_image: u.profile_image_url_https || null,
                author_followers: typeof u.followers_count === 'number' ? u.followers_count : null,
                author_following: typeof u.friends_count === 'number' ? u.friends_count : null,
                author_verified: u.verified || false,
                author_blue_verified: u.is_blue_verified || false,
                author_location: u.location || null,
                author_description: u.description || null,
                like_count: t.favorite_count || 0,
                retweet_count: t.retweet_count || 0,
                reply_count: t.reply_count || 0,
                quote_count: t.quote_count || 0,
                bookmark_count: t.bookmark_count || 0,
                view_count: (t.views && t.views.count) ? parseInt(t.views.count) : null,
                is_retweet: !!(t.retweeted_status_result),
                is_quote: t.is_quote_status || false,
                is_reply: !!(t.in_reply_to_status_id_str),
                in_reply_to_tweet_id: t.in_reply_to_status_id_str || null,
                in_reply_to_user: t.in_reply_to_screen_name || null,
                conversation_id: t.conversation_id_str || null,
                hashtags: (
                  t.entities && t.entities.hashtags || []
                ).map(function(h) { return h.text; }),
                urls: (
                  t.entities && t.entities.urls || []
                ).map(function(eu) { return eu.expanded_url; }),
                mentions: (
                  t.entities && t.entities.user_mentions || []
                ).map(function(mn) { return mn.screen_name; }),
                media: media,
                source_name: t.source_name || null,
                source_url: t.source_url || null
              });
              found = true;
            }
            fiber = fiber.return;
            depth++;
          }
        }

        return JSON.stringify(tweets);
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
