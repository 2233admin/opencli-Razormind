import argparse
import json
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('question')  # question text to submit
    args = parser.parse_args()

    q = json.dumps(args.question)

    js = f"""
    (function() {{
      try {{
        const el = document.getElementById('rufus-text-area');
        if (!el) return JSON.stringify({{error: true, message: 'Alexa textarea not found - ' +
          'panel may be closed'}});
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
        setter.call(el, {q});
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        const submitBtn = document.getElementById('rufus-submit-button');
        if (!submitBtn) return JSON.stringify({{error: true, message: 'Submit button not found'}});
        submitBtn.click();
        return JSON.stringify({{success: true, question: {q}}});
      }} catch(e) {{
        return JSON.stringify({{error: true, message: e.message}});
      }}
    }})()
    """
    print(js)


if __name__ == '__main__':
    main()
