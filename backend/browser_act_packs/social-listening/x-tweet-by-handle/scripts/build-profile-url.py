import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description='Build X user profile URL for the chosen timeline mode'
    )
    parser.add_argument('handle', help='X user handle without @ (case-insensitive)')
    parser.add_argument(
        '--mode',
        choices=['tweets', 'replies', 'media'],
        default='tweets',
        help='Which profile timeline to open: tweets (UserTweets), '
        'replies (UserTweetsAndReplies), media (UserMedia)',
    )
    args = parser.parse_args()

    h = args.handle.lstrip('@').strip()
    base = f'https://x.com/{h}'
    if args.mode == 'tweets':
        url = base
    elif args.mode == 'replies':
        url = f'{base}/with_replies'
    else:
        url = f'{base}/media'
    print(url)


if __name__ == '__main__':
    main()
