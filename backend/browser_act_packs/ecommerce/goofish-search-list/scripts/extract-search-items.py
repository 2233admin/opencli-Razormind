import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

    js = r"""
    (function() {
      try {
        var items = document.querySelectorAll('a[class*="feeds-item-wrap"]');
        if (items.length === 0) {
          return JSON.stringify({
            error: true,
            message: 'No search result items found — page may not have loaded ' +
              'or search returned no results'
          });
        }
        var result = [];
        for (var i = 0; i < items.length; i++) {
          var item = items[i];
          var href = item.getAttribute('href') || '';
          var idMatch = href.match(/[?&]id=(\d+)/);
          var catMatch = href.match(/categoryId=(\d+)/);

          var titleEl = item.querySelector('[class*="row1-wrap-title"]');
          var title = titleEl ? (titleEl.getAttribute('title') ||
            titleEl.textContent.replace(/^\s+|^\s+$/g, '')) : null;

          var imgEl = item.querySelector('img[class*="feeds-image"]');

          var numEl = item.querySelector('[class*="number--"]');
          var decEl = item.querySelector('[class*="decimal--"]');
          var price = (numEl ? numEl.textContent.trim() : '') +
            (decEl ? decEl.textContent.trim() : '');

          var tagEl = item.querySelector('[class*="row2-wrap"]');
          var serviceTag = tagEl ? tagEl.textContent.trim() : null;

          var descEl = item.querySelector('[class*="price-desc"]');
          var priceDesc = descEl ? descEl.textContent.trim() : null;

          var locEl = item.querySelector('[class*="seller-text--"]');
          var location = locEl ? locEl.textContent.trim() : null;

          result.push({
            item_id: idMatch ? idMatch[1] : null,
            category_id: catMatch ? catMatch[1] : null,
            item_url: href,
            title: title,
            image_url: imgEl ? imgEl.src : null,
            price: price || null,
            service_tag: serviceTag || null,
            price_desc: priceDesc || null,
            location: location
          });
        }
        return JSON.stringify({ items: result, count: result.length });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == "__main__":
    main()
