import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser()
    parser.parse_args()

    js = """
    (()=>{
      try {
        const name = document.querySelector('h1')?.textContent?.trim() || '';
        const bodyText = document.body.innerText;

        const headlineMatch = bodyText.match(new RegExp(name + '\\n(.+?)\\n'));
        const headline = headlineMatch ? headlineMatch[1].trim() : '';

        const aboutSection = bodyText.match(
          /About\\n([\\s\\S]*?)(?=Links|Badges|Maker History|Forums|$)/
        );
        const aboutText = aboutSection
          ? aboutSection[1].trim().split('\\n').filter(l => l.trim()).join(' ')
          : '';

        const allLinks = Array.from(document.querySelectorAll('a[href]'));
        const externalLinks = allLinks.filter(a => {
          const href = a.href;
          return (
            href &&
            !href.includes('producthunt.com') &&
            !href.includes('javascript:') &&
            !href.includes('#') &&
            !href.includes('google.com') &&
            !href.includes('lu.ma')
          );
        }).map(a => a.href);
        const uniqueLinks = [...new Set(externalLinks)];

        const followersMatch = bodyText.match(/(\\d+)\\s*followers/i);
        const followers = followersMatch ? parseInt(followersMatch[1]) : 0;

        const slugMatch = location.pathname.match(/^\\/@(.+)/);
        const slug = slugMatch ? '@' + slugMatch[1] : '';

        return JSON.stringify({
          name: name,
          slug: slug,
          headline: headline,
          aboutText: aboutText,
          links: uniqueLinks,
          followers: followers,
          url: location.href
        });
      } catch(e) {
        return JSON.stringify({error: true, message: e.message});
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
