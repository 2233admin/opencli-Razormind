import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(
        description=(
            "Check X DM composer state: confirm textarea available, return "
            "current value and current message count"
        )
    )
    parser.parse_args()

    js = """
    (() => {
      try {
        const passcode = document.querySelector(
          'input[pattern="[0-9]*"][maxlength="1"]'
        );
        if (passcode) return JSON.stringify({ error: true, message: 'passcode_required' });

        const panel = document.querySelector('[data-testid="dm-conversation-panel"]');
        if (!panel) {
          return JSON.stringify({
            error: true,
            message: 'no active conversation (open a conversation first)'
          });
        }

        const textarea = document.querySelector('[data-testid="dm-composer-textarea"]');
        if (!textarea) {
          return JSON.stringify({
            error: true,
            message: 'dm-composer-textarea not found (composer may be disabled)'
          });
        }

        const sendBtn = document.querySelector('[data-testid="dm-composer-send-button"]');
        const voiceBtn = document.querySelector('[data-testid="dm-composer-voice-button"]');

        const msgCount = document.querySelectorAll(
          '[data-testid^="message-"]:not([data-testid^="message-text-"])'
        ).length;
        const lastMsgEl = document.querySelectorAll(
          '[data-testid^="message-"]:not([data-testid^="message-text-"])'
        );
        const lastId = lastMsgEl.length
          ? lastMsgEl[lastMsgEl.length - 1]
              .getAttribute('data-testid').replace('message-', '')
          : null;

        const convIdMatch = location.pathname.match(/\\/i\\/chat\\/(\\d+)-(\\d+)/);
        let convId = null;
        if (convIdMatch) {
          const a = BigInt(convIdMatch[1]);
          const b = BigInt(convIdMatch[2]);
          convId = (a < b ? `${a}:${b}` : `${b}:${a}`);
        }

        return JSON.stringify({
          conversation_id: convId,
          url: location.href,
          composer_ready: true,
          current_value: textarea.value,
          has_send_button: !!sendBtn,
          has_voice_button: !!voiceBtn,
          message_count: msgCount,
          last_message_id: lastId
        });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
