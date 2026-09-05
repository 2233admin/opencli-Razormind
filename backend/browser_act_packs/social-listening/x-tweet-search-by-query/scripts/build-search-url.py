import argparse
import sys
import urllib.parse


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description=(
            "Build X search URL from a raw query and optional advanced filters"
        )
    )
    parser.add_argument(
        "raw_query",
        help=(
            'Free-form X advanced search query (e.g. "AI min_faves:100 '
            'lang:en filter:media -filter:retweets")'
        ),
    )
    parser.add_argument(
        "--sort",
        choices=["Latest", "Top"],
        default="Latest",
        help="Sort mode",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="ISO 639-1 language code; appended as lang:<code> if not already in query",
    )
    parser.add_argument(
        "--min-retweets",
        type=int,
        default=None,
        help="Minimum retweet count; appended as min_retweets:N",
    )
    parser.add_argument(
        "--min-faves",
        type=int,
        default=None,
        help="Minimum favorite count; appended as min_faves:N",
    )
    parser.add_argument(
        "--min-replies",
        type=int,
        default=None,
        help="Minimum reply count; appended as min_replies:N",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Start date YYYY-MM-DD; appended as since:YYYY-MM-DD",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="End date YYYY-MM-DD; appended as until:YYYY-MM-DD",
    )
    parser.add_argument(
        "--author",
        default=None,
        help="Tweets only from this handle (without @); appended as from:HANDLE",
    )
    parser.add_argument(
        "--in-reply-to",
        default=None,
        help="Tweets replying to this handle; appended as to:HANDLE",
    )
    parser.add_argument(
        "--mentioning",
        default=None,
        help="Tweets mentioning this handle; appended as @HANDLE",
    )
    parser.add_argument(
        "--only-verified",
        action="store_true",
        help="Only verified users; appended as filter:verified",
    )
    parser.add_argument(
        "--only-blue-verified",
        action="store_true",
        help="Only X Premium / blue verified; appended as filter:blue_verified",
    )
    parser.add_argument(
        "--only-image",
        action="store_true",
        help="Only tweets with images; appended as filter:images",
    )
    parser.add_argument(
        "--only-video",
        action="store_true",
        help="Only tweets with native video; appended as filter:native_video",
    )
    parser.add_argument(
        "--only-quote",
        action="store_true",
        help="Only quote tweets; appended as filter:quote",
    )
    parser.add_argument(
        "--exclude-retweets",
        action="store_true",
        help="Exclude retweets; appended as -filter:retweets",
    )
    parser.add_argument(
        "--exclude-replies",
        action="store_true",
        help="Exclude replies; appended as -filter:replies",
    )
    parser.add_argument(
        "--geocode",
        default=None,
        help=(
            "Geocode filter LAT,LON,RADIUS (e.g. 37.7749,-122.4194,5mi); "
            "appended as geocode:..."
        ),
    )
    parser.add_argument(
        "--near",
        default=None,
        help='Place name near (appended as near:"NAME")',
    )
    parser.add_argument(
        "--within",
        default=None,
        help="Radius around near (e.g. 15mi or 25km); appended as within:VALUE",
    )
    args = parser.parse_args()

    q = args.raw_query.strip()

    def add_clause(clause):
        nonlocal q
        if clause and clause not in q:
            q = (q + ' ' + clause).strip()

    if args.language:
        add_clause(f'lang:{args.language}')
    if args.min_retweets is not None:
        add_clause(f'min_retweets:{args.min_retweets}')
    if args.min_faves is not None:
        add_clause(f'min_faves:{args.min_faves}')
    if args.min_replies is not None:
        add_clause(f'min_replies:{args.min_replies}')
    if args.since:
        add_clause(f'since:{args.since}')
    if args.until:
        add_clause(f'until:{args.until}')
    if args.author:
        add_clause(f'from:{args.author.lstrip("@")}')
    if args.in_reply_to:
        add_clause(f'to:{args.in_reply_to.lstrip("@")}')
    if args.mentioning:
        add_clause(f'@{args.mentioning.lstrip("@")}')
    if args.only_verified:
        add_clause('filter:verified')
    if args.only_blue_verified:
        add_clause('filter:blue_verified')
    if args.only_image:
        add_clause('filter:images')
    if args.only_video:
        add_clause('filter:native_video')
    if args.only_quote:
        add_clause('filter:quote')
    if args.exclude_retweets:
        add_clause('-filter:retweets')
    if args.exclude_replies:
        add_clause('-filter:replies')
    if args.geocode:
        add_clause(f'geocode:{args.geocode}')
    if args.near:
        add_clause(f'near:"{args.near}"')
    if args.within:
        add_clause(f'within:{args.within}')

    f_param = 'live' if args.sort == 'Latest' else 'top'
    encoded_q = urllib.parse.quote(q, safe='')
    url = f'https://x.com/search?q={encoded_q}&src=typed_query&f={f_param}'
    print(url)


if __name__ == '__main__':
    main()
