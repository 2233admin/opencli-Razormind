import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.parse_args()

    js = """
    (()=>{
      try {
        const title = document.title || '';
        const url = location.href;
        const bodyText = document.body.innerText || '';

        const emailRegex = /[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g;
        const allEmails = bodyText.match(emailRegex) || [];
        const html = document.documentElement.innerHTML;
        const htmlEmails = html.match(emailRegex) || [];
        const combined = [...new Set([...allEmails, ...htmlEmails])];
        const filtered = combined.filter(
          e => !e.includes('sentry') &&
            !e.includes('webpack') &&
            !e.includes('example.com')
        );
        const email = filtered.length > 0 ? filtered[0] : null;

        const rawText = bodyText.replace(/\\s+/g, ' ').trim().slice(0, 5000);

        return JSON.stringify({
          title: title,
          url: url,
          email: email,
          allEmails: filtered,
          websiteRawText: rawText
        });
      } catch(e) {
        return JSON.stringify({error: true, message: e.message});
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
