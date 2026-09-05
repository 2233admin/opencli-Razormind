import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(
        description=(
            "Check X DM page state: URL, login status, passcode requirement, "
            "conversation panel availability"
        )
    )
    parser.parse_args()

    js = """
    (() => {
      try {
        return JSON.stringify({
          url: location.href,
          logged_in: !!document.querySelector('[aria-label="Account menu"]') ||
            !!document.cookie.split('; ').find(c => c.startsWith('twid=')),
          need_passcode: !!document.querySelector('input[pattern="[0-9]*"][maxlength="1"]'),
          on_inbox: /\\/i\\/chat\\/?$/.test(location.pathname),
          on_conversation: /\\/i\\/chat\\/\\d+-\\d+/.test(location.pathname),
          has_panel: !!document.querySelector('[data-testid="dm-conversation-panel"]'),
          has_composer: !!document.querySelector('[data-testid="dm-composer-textarea"]'),
          inbox_count: document.querySelectorAll('[data-testid^="dm-conversation-item-"]').length
        });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)

if __name__ == '__main__':
    main()
