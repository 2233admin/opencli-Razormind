import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default='', help='Date in YYYY-M-DD format for daily archive')
    parser.add_argument('--year', default='', help='Year in YYYY format for yearly archive')
    parser.add_argument('--week', default='', help='Week number for weekly archive')
    parser.add_argument('--month', default='', help='Month number for monthly archive')
    parser.add_argument('--filter-type', default='all', help='Filter type: all or featured')
    parser.parse_args()

    js = """
    (()=>{
      try {
        const cards = Array.from(document.querySelectorAll('section.group'));
        if(cards.length === 0) return JSON.stringify({
          error: true,
          message: "No product cards found on page. Check if the page loaded correctly."
        });
        const products = cards.map(card => {
          const link = card.querySelector('a[href^="/products/"]');
          if(!link) return null;
          const linkText = link.textContent.trim();
          const rankMatch = linkText.match(/^(\\d+)\\.\\s+(.+)/);
          const rank = rankMatch ? parseInt(rankMatch[1]) : null;
          const name = rankMatch ? rankMatch[2] : linkText;
          const topicLinks = Array.from(card.querySelectorAll('a[href^="/topics/"]'));
          const categories = topicLinks.map(a => a.textContent.trim());
          const img = card.querySelector('img[alt]:not([alt="Promoted"])');
          const thumbnail = img ? img.src : null;
          const lines = card.innerText.split('\\n').filter(l => l.trim());
          const tagline = lines.length > 1 ? lines[1] : '';
          const buttons = Array.from(card.querySelectorAll('button.relative'));
          const upvotes = buttons[0] ? parseInt(buttons[0].textContent.trim()) || 0 : 0;
          const comments = buttons[1] ? parseInt(buttons[1].textContent.trim()) || 0 : 0;
          const href = link.getAttribute('href');
          return {
            rank,
            name,
            tagline,
            categories,
            thumbnail,
            upvotes,
            comments,
            url: 'https://www.producthunt.com' + href,
            slug: href.replace('/products/', '')
          };
        }).filter(Boolean);
        return JSON.stringify(products);
      } catch(e) {
        return JSON.stringify({error: true, message: e.message});
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
