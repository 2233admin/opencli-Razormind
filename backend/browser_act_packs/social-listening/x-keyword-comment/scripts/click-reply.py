import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')

    parser = argparse.ArgumentParser()
    parser.add_argument(
        'reply_btn_idx',
        type=int,
        help="Index of the target tweet's reply button (from scan-search-tweets.py)",
    )
    args = parser.parse_args()

    js = f"""
    (async () => {{
      try {{
        const idx = {args.reply_btn_idx};
        const replyBtns = Array.from(document.querySelectorAll('[data-testid="reply"]'));
        const replyBtn = replyBtns[idx];

        if (!replyBtn) {{
          return JSON.stringify({{
            ok: false, reason: 'reply_btn_out_of_range', total: replyBtns.length
          }});
        }}

        replyBtn.scrollIntoView({{ block: 'center' }});
        await new Promise(r => setTimeout(r, 300));
        replyBtn.click();
        await new Promise(r => setTimeout(r, 800));

        return JSON.stringify({{
          ok: true, replyBtnFound: true, totalReplyBtns: replyBtns.length
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)


if __name__ == '__main__':
    main()
