import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "shop_id"
    )  # shop ID (for documentation only; page already loaded)
    parser.add_argument("--page", default="1")  # current page number
    parser.parse_args()

    js = """
    (function() {{
      try {{
        var cards = document.querySelectorAll('.item');
        if (!cards || cards.length === 0) {{
          return JSON.stringify({{ error: true, message: 'No product cards found '
            + '(.item). Page may not have loaded or shopId is invalid.' }});
        }}
        var items = Array.from(cards).map(function(card) {{
          var link = card.querySelector('a[href*="id="]');
          var href = link ? link.href : '';
          var idMatch = href.match(/[?&]id=([^&]+)/);
          var itemId = idMatch ? idMatch[1] : null;
          if (!itemId) return null;

          var titleEl = card.querySelector('a.item-name, [class*="title"], .title');
          var title = titleEl ? titleEl.textContent.trim() : '';

          var img = card.querySelector('img[src], img[data-ks-lazyload-custom]');
          var imgUrl = '';
          if (img) {{
            imgUrl = img.src || img.getAttribute('data-ks-lazyload-custom') || '';
            if (imgUrl && !imgUrl.startsWith('http')) imgUrl = 'https:' + imgUrl;
          }}

          return {{
            itemId: itemId,
            title: title,
            imageUrl: imgUrl,
            itemUrl: 'https://item.taobao.com/item.htm?id=' + itemId
          }};
        }}).filter(function(item) {{ return item !== null; }});

        // Deduplicate by itemId
        var seen = {{}};
        var unique = items.filter(function(item) {{
          if (seen[item.itemId]) return false;
          seen[item.itemId] = true;
          return true;
        }});

        return JSON.stringify(unique);
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    js = js.replace("{{", "{").replace("}}", "}")
    print(js)


if __name__ == '__main__':
    main()
