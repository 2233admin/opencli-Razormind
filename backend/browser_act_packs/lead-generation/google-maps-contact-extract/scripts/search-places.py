import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('keyword')            # search keyword, e.g. "coffee shops"
    parser.add_argument('--max', default='20')  # max number of places to extract
    args = parser.parse_args()

    keyword = args.keyword.replace("'", "\\'")
    max_count = int(args.max)

    js = r"""
(function() {
  try {
    var cards = Array.from(document.querySelectorAll('[role=feed] > div'))
      .filter(function(el) { return el.querySelector('a[aria-label]'); });
    if (cards.length === 0) {
      return JSON.stringify({
        error: true,
        message: 'No place cards found in feed. ' +
          'Make sure the Google Maps search results page is open with search results visible.'
      });
    }
    var results = cards.slice(0, __MAX__).map(function(el) {
      var link = el.querySelector('a');
      var nameEl = el.querySelector('[aria-label]');
      var t = el.textContent;

      // Format: "Name  4.8(292) · $1-10Category · ... Address"
      var ratingM = t.match(/([0-9]\.[0-9])\(/);
      var reviewM = t.match(/\(([0-9,]+)\)/);
      var openM = t.match(/(Open|Closed)[^·\n]*/);
      var url = link ? link.href : null;
      var placeIdM = url ? url.match(/!1s([^!]+)!/) : null;
      var coordM = url ? url.match(/!3d(-?[0-9.]+)!4d(-?[0-9.]+)/) : null;
      var priceM = t.match(/\$[\d]+[–\-][\d]+|\$\$+/);
      var phoneM = t.match(/[(]?[0-9]{3}[)]?[ .\-][0-9]{3}[ .\-][0-9]{4}/);
      // Category follows price range or review count, then ends with " ·"
      var catM = t.match(new RegExp(
        '(?:\\$[\\d]+[^C\\n]*?)' +
        '([A-Z][a-zA-Z ]+(?:shop|restaurant|cafe|bar|hotel|store|' +
        'bakery|pharmacy|gym|service|center|salon|spa|market))',
        'i'
      )) || t.match(new RegExp(
        '[·] ([A-Z][a-zA-Z ]*(?:shop|restaurant|cafe|bar|hotel|' +
        'store|bakery|pharmacy|gym|salon|spa)) [·]',
        'i'
      ));

      return {
        name: nameEl ? nameEl.getAttribute('aria-label') : null,
        rating: ratingM ? parseFloat(ratingM[1]) : null,
        review_count: reviewM ? parseInt(reviewM[1].replace(',', '')) : null,
        category: catM ? (catM[1] || catM[0]).trim() : null,
        price_range: priceM ? priceM[0] : null,
        phone: phoneM ? phoneM[0] : null,
        open_status: openM ? openM[0].trim().slice(0, 60) : null,
        place_id: placeIdM ? placeIdM[1] : null,
        lat: coordM ? parseFloat(coordM[1]) : null,
        lng: coordM ? parseFloat(coordM[2]) : null,
        maps_url: url || null
      };
    });
    return JSON.stringify({ keyword: '__KW__', count: results.length, places: results });
  } catch(e) {
    return JSON.stringify({ error: true, message: e.message });
  }
})()
""".replace('__MAX__', str(max_count)).replace('__KW__', keyword)

    print(js)


if __name__ == '__main__':
    main()
