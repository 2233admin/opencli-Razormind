import argparse
import json
import re
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description=(
            "Classify an X / Twitter URL into one of the supported timeline types "
            "and report which GraphQL endpoint will be triggered when it is opened."
        )
    )
    parser.add_argument(
        "url",
        help="Full URL pointing to an X resource (search, profile, single tweet, or list)",
    )
    args = parser.parse_args()

    u = args.url.strip()
    u = re.sub(r'^https?://(www\.)?twitter\.com', 'https://x.com', u)
    if not u.startswith('http'):
        u = 'https://' + u

    info = {
        "normalized_url": u,
        "kind": "unknown",
        "endpoint": None,
        "extract_source": None,
        "params": {},
    }

    m = re.match(r'https://x\.com/search\b', u)
    if m:
        info.update(kind='search', endpoint='SearchTimeline', extract_source='search')

    m = re.match(r'https://x\.com/i/lists/(\d+)', u)
    if m:
        info.update(kind='list', endpoint='ListLatestTweetsTimeline',
                    extract_source='list', params={'list_id': m.group(1)})

    m = re.match(r'https://x\.com/([^/]+)/status/(\d+)', u)
    if m:
        info.update(
            kind='tweet_detail',
            endpoint='TweetDetail',
            extract_source='tweet_detail',
            params={'handle': m.group(1), 'tweet_id': m.group(2)},
        )

    m = re.match(r'https://x\.com/([^/]+)/with_replies/?$', u)
    if m:
        info.update(kind='user_replies', endpoint='UserTweetsAndReplies',
                    extract_source='user_replies', params={'handle': m.group(1)})

    m = re.match(r'https://x\.com/([^/]+)/media/?$', u)
    if m:
        info.update(kind='user_media', endpoint='UserMedia',
                    extract_source='user_media', params={'handle': m.group(1)})

    m = re.match(r'https://x\.com/([^/]+)/?$', u)
    if m and info['kind'] == 'unknown':
        h = m.group(1)
        if h not in (
            'home', 'explore', 'notifications', 'messages',
            'i', 'search', 'compose', 'settings',
        ):
            info.update(
                kind='user_tweets',
                endpoint='UserTweets',
                extract_source='user_tweets',
                params={'handle': h},
            )

    print(json.dumps(info, ensure_ascii=False))


if __name__ == '__main__':
    main()
