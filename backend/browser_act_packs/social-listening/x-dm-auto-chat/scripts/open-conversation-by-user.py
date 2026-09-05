import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(
        description=(
            "Navigate to (or create) a 1-on-1 DM conversation "
            "with a given user by user_id"
        )
    )
    parser.add_argument('user_id', help='Target user rest_id (numeric string)')
    args = parser.parse_args()

    js = f"""
    (async () => {{
      try {{
        const myId = document.cookie
          .split('; ')
          .find(c => c.startsWith('twid='))
          ?.split('=')[1]
          ?.replace(/^u%3D/, '')
          ?.replace(/^u=/, '');
        const peerId = {repr(args.user_id)};

        const a = BigInt(myId);
        const b = BigInt(peerId);
        const urlPath = a < b ? `/i/chat/${{a}}-${{b}}` : `/i/chat/${{b}}-${{a}}`;

        return JSON.stringify({{
          my_user_id: myId,
          peer_user_id: peerId,
          conversation_url: urlPath,
          conversation_id: a < b ? `${{a}}:${{b}}` : `${{b}}:${{a}}`,
          next_step: 'navigate to conversation_url then '
            + 'verify composer_ready'
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
