import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description=(
            'Read all currently-loaded messages from the active '
            'X DM conversation DOM'
        )
    )
    parser.parse_args()

    js = """
    (() => {
      try {
        const passcode = document.querySelector('input[pattern="[0-9]*"][maxlength="1"]');
        if (passcode) return JSON.stringify({ error: true, message: 'passcode_required' });

        const header = document.querySelector('[data-testid="dm-conversation-header"]');
        const peerName = header?.innerText?.trim();
        const panel = document.querySelector('[data-testid="dm-conversation-panel"]');
        if not panel:
          return JSON.stringify({
            error: true,
            message: 'no active conversation (dm-conversation-panel not found); '
              + 'navigate to /i/chat/{id1}-{id2} or click an inbox item'
          });

        const convIdMatch = location.pathname.match(/\\/i\\/chat\\/(\\d+)-(\\d+)/);
        let convId = null;
        if (convIdMatch) {
          const a = BigInt(convIdMatch[1]);
          const b = BigInt(convIdMatch[2]);
          convId = (a < b ? `${a}:${b}` : `${b}:${a}`);
        }

        const myId = document.cookie.split('; ')
          .find(c => c.startsWith('twid='))?.split('=')[1]
          ?.replace(/^u%3D/, '').replace(/^u=/, '');

        const msgEls = document.querySelectorAll(
          '[data-testid^="message-"]:not([data-testid^="message-text-"])'
        );
        const messages = [];
        msgEls.forEach(el => {
          const testid = el.getAttribute('data-testid');
          const msgId = testid.replace('message-', '');
          const textEl = document.querySelector(`[data-testid="message-text-${msgId}"]`);
          let text = textEl?.textContent || '';
          // Strip trailing timestamp duplicates like "6:25 PM6:25 PM" that get picked up
          // by textContent when the time element is a sibling inside message-text
          text = text.replace(/(\\d{1,2}:\\d{2}\\s?(?:AM|PM)?)+\\s*$/, '').trimEnd();

          const cls = el.className || '';
          const fromSelf = cls.includes('justify-end');
          const fromPeer = cls.includes('justify-start');
          const direction = fromSelf ? 'self' : (fromPeer ? 'peer' : 'unknown');

          // Time — X renders as HH:MM or similar; collect all text in element minus main text
          let timestamp = null;
          const allText = el.innerText || '';
          const timeMatch = allText.match(/\\b(\\d{1,2}:\\d{2}\\s?(?:AM|PM)?)\\b/);
          if (timeMatch) timestamp = timeMatch[1];

          // Extract attached URLs (rich links)
          const links = [...el.querySelectorAll('a[href^="http"]')]
            .map(a => a.getAttribute('href'))
            .filter((v, i, arr) => arr.indexOf(v) === i);

          // Attached images (avatars excluded)
          const images = [...el.querySelectorAll('img[src]')]
            .map(im => im.getAttribute('src'))
            .filter(s => !/profile_images/.test(s))
            .filter((v, i, arr) => arr.indexOf(v) === i);

          messages.push({
            message_id: msgId,
            direction,
            text,
            timestamp_text: timestamp,
            links,
            images
          });
        });

        return JSON.stringify({
          conversation_id: convId,
          url: location.href,
          peer_display_name: peerName,
          my_user_id: myId,
          message_count: messages.length,
          messages
        });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)

if __name__ == '__main__':
    main()
