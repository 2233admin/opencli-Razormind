import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description='Verify a message was sent by checking if a new self-direction '
        'message matching the given text appeared after the given previous '
        'last_message_id'
    )
    parser.add_argument(
        'expected_text',
        help='The message text that was supposed to be sent (exact match)'
    )
    parser.add_argument(
        '--prev-last-id',
        default='',
        help='The last_message_id before sending, used to detect the newly '
        'appeared message'
    )
    args = parser.parse_args()

    # Escape backticks and ${ for JS template literals
    expected = args.expected_text.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')

    js = f"""
    (() => {{
      try {{
        const expected = `{expected}`;
        const prevLastId = {repr(args.prev_last_id)};
        if (msgEls.length === 0) return JSON.stringify({{ sent: false,
          reason: 'no messages in conversation' }});

        let foundAfterPrev = false;
        let sawPrev = prevLastId === '';
        for (const el of msgEls) {{
          const id = el.getAttribute('data-testid').replace('message-', '');
          const cls = el.className || '';
          const isSelf = cls.includes('justify-end');
          const textEl = document.querySelector('[data-testid="message-text-' + id + '"]');
          let text = textEl?.textContent || '';
          text = text.replace(/(\\d{{1,2}}:\\d{{2}}\\s?(?:AM|PM)?)+\\s*$/, '').trimEnd();

          if (!sawPrev) {{
            if (id === prevLastId) sawPrev = true;
            continue;
          }}
          if (isSelf && text.includes(expected.trim())) {{
            foundAfterPrev = true;
            break;
          }}
        }}

        // Check for delivery failure: X renders a separate LI with "Failed, Try Again"
        // in dm-message-list when delivery fails (sibling of the message LI, no data-testid)
        const msgList = document.querySelector('[data-testid="dm-message-list"]');
        const hasDeliveryFailed = msgList
          ? [...msgList.querySelectorAll('li')].some(
              li => li.innerText?.trim() === 'Failed, Try Again'
            )
          : false;

        if (foundAfterPrev && hasDeliveryFailed) {{
          return JSON.stringify({{ sent: false, reason: 'delivery_failed',
            composer_cleared: document.querySelector(
              '[data-testid="dm-composer-textarea"]'
            )?.value === '',
            current_message_count: msgEls.length }});
        }}

        const textarea = document.querySelector('[data-testid="dm-composer-textarea"]');
        const composerEmpty = textarea?.value === '';

        return JSON.stringify({{
          sent: foundAfterPrev,
          composer_cleared: composerEmpty,
          current_message_count: msgEls.length
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
