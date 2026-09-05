import argparse
import json
import re
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description=(
            'Parse a single GraphQL response body captured via `browser-act network request <id>` '
            'into a normalized tweet list with cursor. Pass --source to indicate which endpoint '
            'produced it.'
        )
    )
    parser.add_argument(
        '--json-file',
        required=True,
        help=(
            'Path to a file containing the raw output of `network request <id>` '
            '(may include header lines)'
        ),
    )
    parser.add_argument(
        '--source',
        required=True,
        choices=['search', 'user_tweets', 'user_replies', 'user_media', 'list', 'tweet_detail'],
        help='Which GraphQL endpoint the response came from',
    )
    args = parser.parse_args()

    try:
        with open(args.json_file, encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        print(json.dumps({"error": True, "message": f"open failed: {e}"}))
        return

    body_str = _locate_body(text)
    if not body_str:
        print(json.dumps({"error": True, "message": "No JSON body found in input"}))
        return

    try:
        data = json.loads(body_str)
    except Exception as e:
        # Try trimming trailing noise
        last = body_str.rfind('}')
        if last > 0:
            try:
                data = json.loads(body_str[:last + 1])
            except Exception as e2:
                print(json.dumps({"error": True, "message": f"JSON parse failed: {e2}"}))
                return
        else:
            print(json.dumps({"error": True, "message": f"JSON parse failed: {e}"}))
            return

    try:
        result = _extract(data, args.source)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": True, "message": f"extract failed: {e}"}))


def _locate_body(text):
    # The `network request <id>` output starts with key=value lines
    # (request_id=, url=, status, etc.) followed by an empty line then the body.
    # Body always starts with {"data":...} for these GraphQL endpoints.
    idx = text.find('{"data":')
    if idx >= 0:
        return text[idx:]
    # Fallback: any line that begins with '{'
    for m in re.finditer(r'\n(\{.+?)$', text, flags=re.DOTALL):
        return m.group(1)
    return None


def _extract(data, source):
    instructions = []
    timeline_root = None
    try:
        d = data.get('data') or {}
        if source == 'search':
            timeline_root = ((d.get('search_by_raw_query') or {})
                             .get('search_timeline') or {}).get('timeline') or {}
        elif source in ('user_tweets', 'user_replies', 'user_media'):
            user = (d.get('user') or {}).get('result') or {}
            tl = user.get('timeline') or {}
            # Some payloads nest twice: timeline.timeline
            if isinstance(tl.get('timeline'), dict):
                timeline_root = tl['timeline']
            else:
                timeline_root = tl
        elif source == 'list':
            timeline_root = (
                ((d.get('list') or {}).get('tweets_timeline') or {}).get('timeline') or {}
            )
        elif source == 'tweet_detail':
            timeline_root = d.get('threaded_conversation_with_injections_v2') or {}
        instructions = (timeline_root or {}).get('instructions') or []
    except Exception:
        instructions = []

    entries = []
    for inst in instructions:
        if inst.get('type') == 'TimelineAddEntries':
            entries.extend(inst.get('entries') or [])
        elif inst.get('entry'):
            entries.append(inst['entry'])

    tweets = []
    cursors = {"top": None, "bottom": None}
    for entry in entries:
        eid = entry.get('entryId', '')
        content = entry.get('content') or {}
        ctype = content.get('entryType') or ''
        item_type = (content.get('itemContent') or {}).get('itemType') or ''

        if eid.startswith('tweet-') or item_type == 'TimelineTweet':
            tr = (content.get('itemContent') or {}).get('tweet_results') or {}
            t = _normalize_tweet(tr.get('result'))
            if t:
                tweets.append(t)

        elif item_type == 'TimelineTimelineCursor' or 'cursor-' in eid:
            ic = content.get('itemContent') or content
            ct = (ic.get('cursorType') or '').lower()
            val = ic.get('value')
            if ct == 'top':
                cursors['top'] = val
            elif ct == 'bottom':
                cursors['bottom'] = val

        elif (
            ctype == 'TimelineTimelineModule'
            or eid.startswith('conversationthread-')
            or eid.startswith('homeConversation-')
        ):
            for item in (content.get('items') or []):
                ic = (item.get('item') or {}).get('itemContent') or {}
                if ic.get('itemType') == 'TimelineTweet':
                    res = ((ic.get('tweet_results') or {}).get('result'))
                    t = _normalize_tweet(res)
                    if t:
                        tweets.append(t)

    return {
        "tweets": tweets,
        "count": len(tweets),
        "cursor_top": cursors['top'],
        "cursor_bottom": cursors['bottom'],
    }


def _normalize_tweet(res):
    if not res:
        return None
    if res.get('__typename') == 'TweetTombstone':
        return None
    if res.get('__typename') == 'TweetWithVisibilityResults' and res.get('tweet'):
        res = res['tweet']
    legacy = res.get('legacy') or {}
    if not legacy:
        return None

    note = res.get('note_tweet') or {}
    note_result = (note.get('note_tweet_results') or {}).get('result') or {}
    full_text = note_result.get('text') or legacy.get('full_text')

    user_res = (((res.get('core') or {}).get('user_results') or {}).get('result')) or {}
    u_core = user_res.get('core') or {}
    u_legacy = user_res.get('legacy') or {}
    u_avatar = user_res.get('avatar') or {}
    u_loc = user_res.get('location') or {}
    u_bio = user_res.get('profile_bio') or {}
    u_ver = user_res.get('verification') or {}
    screen_name = u_core.get('screen_name') or u_legacy.get('screen_name')
    name = u_core.get('name') or u_legacy.get('name')

    media = []
    ext_media = ((legacy.get('extended_entities') or {}).get('media') or [])
    for m in ext_media:
        item = {
            "type": m.get('type'),
            "url": m.get('media_url_https'),
            "expanded_url": m.get('expanded_url'),
            "alt_text": m.get('ext_alt_text'),
        }
        if m.get('video_info'):
            variants = m['video_info'].get('variants') or []
            item['video_variants'] = [
                {
                    "bitrate": v.get('bitrate'),
                    "url": v.get('url'),
                    "content_type": v.get('content_type'),
                }
                for v in variants
            ]
        media.append(item)

    entities = legacy.get('entities') or {}
    hashtags = [h.get('text') for h in (entities.get('hashtags') or []) if h.get('text')]
    mentions = [
        u.get('screen_name')
        for u in (entities.get('user_mentions') or [])
        if u.get('screen_name')
    ]
    urls = [u.get('expanded_url') for u in (entities.get('urls') or []) if u.get('expanded_url')]

    views = (res.get('views') or {}).get('count')
    if views is not None:
        try:
            views = int(views)
        except Exception:
            pass

    quoted_id = legacy.get('quoted_status_id_str')
    quoted_url = (res.get('quoted_status_permalink') or {}).get('expanded')
    retweeted = bool(legacy.get('retweeted_status_result'))

    card_info = None
    card = res.get('card')
    if card:
        legacy_card = card.get('legacy') or {}
        binding_values = legacy_card.get('binding_values') or []
        bv = {}
        for kv in binding_values:
            k = kv.get('key')
            v = kv.get('value') or {}
            bv[k] = v.get('string_value') or v.get('image_value') or v.get('user_value')
        card_info = {
            "name": legacy_card.get('name'),
            "url": legacy_card.get('url'),
            "binding_values": bv,
        }

    place = legacy.get('place')
    tweet_id = legacy.get('id_str') or res.get('rest_id')
    return {
        "type": "tweet",
        "id": tweet_id,
        "url": (
            f"https://x.com/{screen_name}/status/{tweet_id}"
            if screen_name and tweet_id
            else None
        ),
        "twitter_url": (
            f"https://twitter.com/{screen_name}/status/{tweet_id}"
            if screen_name and tweet_id
            else None
        ),
        "text": full_text,
        "created_at": legacy.get('created_at'),
        "lang": legacy.get('lang'),
        "source": _strip_html_tag(res.get('source')),
        "retweet_count": legacy.get('retweet_count'),
        "reply_count": legacy.get('reply_count'),
        "like_count": legacy.get('favorite_count'),
        "quote_count": legacy.get('quote_count'),
        "bookmark_count": legacy.get('bookmark_count'),
        "view_count": views,
        "is_reply": bool(legacy.get('in_reply_to_status_id_str')),
        "is_retweet": retweeted,
        "is_quote": bool(legacy.get('is_quote_status')),
        "quote_id": quoted_id,
        "quote_url": quoted_url,
        "in_reply_to_id": legacy.get('in_reply_to_status_id_str'),
        "in_reply_to_user": legacy.get('in_reply_to_screen_name'),
        "in_reply_to_user_id": legacy.get('in_reply_to_user_id_str'),
        "conversation_id": legacy.get('conversation_id_str'),
        "hashtags": hashtags,
        "mentions": mentions,
        "urls": urls,
        "media": media,
        "card": card_info,
        "place": place,
        "author": {
            "id": user_res.get('rest_id'),
            "user_name": screen_name,
            "name": name,
            "url": f"https://x.com/{screen_name}" if screen_name else None,
            "is_verified": u_ver.get('verified', u_legacy.get('verified')),
            "is_blue_verified": user_res.get('is_blue_verified'),
            "verified_type": u_ver.get('verified_type'),
            "profile_picture": u_avatar.get('image_url') or u_legacy.get('profile_image_url_https'),
            "description": (u_bio.get('description') or u_legacy.get('description')),
            "location": (u_loc.get('location') or u_legacy.get('location')),
            "followers": u_legacy.get('followers_count'),
            "following": u_legacy.get('friends_count'),
            "created_at": u_core.get('created_at') or u_legacy.get('created_at'),
        },
    }


def _strip_html_tag(s):
    if not s:
        return s
    m = re.search(r'>([^<]+)<', s)
    return m.group(1) if m else s


if __name__ == '__main__':
    main()
