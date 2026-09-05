import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description=(
            'Build the X tweet detail URL for a given conversation root tweet id. '
            'X uses the root tweet id as the conversation_id; opening this URL '
            'triggers the TweetDetail GraphQL query.'
        )
    )
    parser.add_argument(
        'conversation_id',
        help=(
            'Root tweet id (same as conversation_id in X). '
            'The author handle is optional.'
        ),
    )
    parser.add_argument(
        '--handle',
        default='i',
        help=(
            'Tweet author handle (without @). If unknown, leave default "i" — '
            'x.com/i/status/<id> still resolves.'
        ),
    )
    args = parser.parse_args()

    handle = args.handle.lstrip('@').strip() or 'i'
    print(f'https://x.com/{handle}/status/{args.conversation_id}')


if __name__ == '__main__':
    main()
