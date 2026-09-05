import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.parse_args()

    js = """
    (()=>{
      try {
        const title = document.querySelector('h1')?.textContent?.trim() || '';
        const ogDesc = document.querySelector('meta[property="og:description"]')?.content || '';
        const ogImage = document.querySelector('meta[property="og:image"]')?.content || '';

        const allLinks = Array.from(document.querySelectorAll('a[href]'));
        const visitLink = allLinks.find(a => {
          const txt = a.textContent.trim().toLowerCase();
          return txt.includes('visit') && a.href && !a.href.includes('producthunt.com');
        });
        const websiteUrl = visitLink ? visitLink.href : null;

        const topicLinks = Array.from(document.querySelectorAll('a[href^="/topics/"]'));
        const categories = [...new Set(topicLinks.map(a => a.textContent.trim()))];

        const imgs = Array.from(document.querySelectorAll('img[src*="ph-files.imgix.net"]'));
        const images = imgs.map(i => i.src).filter(s => s.includes('fit=max'));

        const upvoteBtn = Array.from(document.querySelectorAll('button')).find(
          b => /upvote\\s*\\d+/i.test(b.textContent)
        );
        const upvotes = upvoteBtn ? parseInt(upvoteBtn.textContent.match(/\\d+/)?.[0]) || 0 : 0;

        const timeEls = Array.from(document.querySelectorAll('time'));
        const launchDate = timeEls.length > 0 ? timeEls[0].getAttribute('datetime') : null;

        const makerLinks = allLinks.filter(a => {
          const href = a.getAttribute('href') || '';
          return href.match(/^\\/@[a-z0-9_]+$/i);
        }).map(a => ({href: a.getAttribute('href'), name: a.textContent.trim()}))
          .filter(m => m.name.length > 0);
        const uniqueMakers = [...new Map(makerLinks.map(m => [m.href, m])).values()];

        const description = ogDesc;

        const bodyText = document.body.innerText;
        let tagline = '';
        const lines = bodyText.split('\\n').filter(l => l.trim());
        const visitLineIdx = lines.findIndex(l => l.trim() === 'Visit');
        if (visitLineIdx > 1) {
          tagline = lines[visitLineIdx - 1].trim();
        }
        if (!tagline || tagline === title) {
          const ogTitle = document.querySelector('meta[property="og:title"]')?.content || '';
          if (ogTitle && ogTitle !== title) tagline = ogTitle;
        }

        return JSON.stringify({
          name: title,
          tagline: tagline,
          description: description,
          categories: categories,
          images: images,
          websiteUrl: websiteUrl,
          upvotes: upvotes,
          launchDate: launchDate,
          makers: uniqueMakers,
          ogImage: ogImage
        });
      } catch(e) {
        return JSON.stringify({error: true, message: e.message});
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
