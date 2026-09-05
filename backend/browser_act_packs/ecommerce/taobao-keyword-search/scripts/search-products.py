import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'keyword'
    )  # Search keyword (documentation only; URL is already set).
    parser.add_argument('--page', default='1')  # Page number, 1-based.
    parser.add_argument(
        '--sort', default=''
    )  # Sort: '', 'sale-desc', 'price-asc', 'price-desc'.
    parser.add_argument('--tab', default='')  # 'mall' for Tmall-only, '' for all.
    parser.add_argument('--start-price', default='')  # Minimum price in yuan.
    parser.add_argument('--end-price', default='')  # Maximum price in yuan.
    parser.parse_args()

    js = """
    (function() {
      try {
        var cards = document.querySelectorAll('[id^="item_id_"]');
        if (!cards || cards.length === 0) {
          return JSON.stringify({ error: true, message: 'No product cards found. Page may \
not have loaded or login is required.' });
        }
        var items = Array.from(cards).map(function(card) {
          var a = card.tagName === 'A' ? card : card.querySelector('a[href*="item.taobao.c\
om"], a[href*="detail.tmall.com"]');
          var href = a ? a.href : '';
          var url;
          try { url = new URL(href || 'https://taobao.com'); } catch(e) { url = new URL('h\
ttps://taobao.com'); }
          var img = card.querySelector('img[class*="mainImg"]') || card.querySelector('img');
          var priceInt = card.querySelector('[class*="priceInt--"]');
          var priceFloat = card.querySelector('[class*="priceFloat--"]');
          var priceDesc = card.querySelector('[class*="priceDesc--"]');
          var sales = card.querySelector('[class*="realSales--"]');
          var shopName = card.querySelector('[class*="shopNameText--"]');
          var location = card.querySelector('[class*="provcity--"], [class*="location--"]');
          var titleEl = card.querySelector('[class*="title--"], [class*="itemTitle--"]');
          var subTitle = card.querySelector('[class*="subTitle--"]');
          var rate = card.querySelector('[class*="shopRating--"], [class*="rating--"]');
          var tags = Array.from(card.querySelectorAll('[class*="tag--"], [class*="Tag--"],\
 [class*="label--"]'))
            .map(function(t) { return t.textContent.trim(); })
            .filter(function(t) { return t.length > 0; })
            .slice(0, 5);
          var rawId = url.searchParams.get('id') || card.id.replace('item_id_', '');
          var rawHref = href.indexOf('simba.taobao.com') >= 0 ? href : (href.split('?')[0]\
 + (rawId ? '?id=' + rawId : ''));
          return {
            itemId: rawId,
            itemUrl: rawHref,
            title: (titleEl ? titleEl.textContent.trim() : (img ? img.alt : '')) || '',
            subTitle: subTitle ? subTitle.textContent.trim() : '',
            priceYuan: (priceInt && priceFloat) ? parseFloat(priceInt.textContent.trim() +\
 priceFloat.textContent.trim()) : null,
            priceDesc: priceDesc ? priceDesc.textContent.trim() : '',
            imageUrl: img ? img.src : '',
            salesCount: sales ? sales.textContent.trim() : '',
            shopName: shopName ? shopName.textContent.trim() : '',
            location: location ? location.textContent.trim() : '',
            rating: rate ? rate.textContent.trim() : null,
            tags: tags
          };
        });
        return JSON.stringify(items);
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
