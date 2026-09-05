import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-results', type=int, default=20)
    args = parser.parse_args()

    js_template = r"""
    (function() {
      try {
        const maxResults = MAX_RESULTS;
        let items = [];

        // Strategy 1: JSON-LD ItemList
        const lds = Array.from(
          document.querySelectorAll('script[type="application/ld+json"]')
        ).map(s => {
          try { return JSON.parse(s.textContent); } catch(e) { return null; }
        }).filter(Boolean);
        const flat = lds.flatMap(l => Array.isArray(l) ? l : [l]);
        const listEl = flat.find(l => l['@type'] === 'ItemList' && l.itemListElement);
        if (listEl) {
          items = (listEl.itemListElement || []).slice(0, maxResults).map(e => {
            const item = e.item || e;
            const offer = Array.isArray(item.offers) ? item.offers[0] : item.offers;
            return {
              url: item.url,
              name: item.name,
              price: offer?.price != null ? parseFloat(offer.price) : null,
              currency: offer?.priceCurrency || null,
              image: Array.isArray(item.image)
                ? item.image[0]
                : (item.image || null),
              rating: item.aggregateRating?.ratingValue != null
                ? parseFloat(item.aggregateRating.ratingValue)
                : null,
              review_count: item.aggregateRating?.reviewCount != null
                ? parseInt(item.aggregateRating.reviewCount)
                : null
            };
          }).filter(item => item.url || item.name);
        }

        // Strategy 2: Amazon search results
        if (items.length === 0) {
          const cards = Array.from(
            document.querySelectorAll('[data-component-type="s-search-result"]')
          );
          if (cards.length > 0) {
            items = cards.slice(0, maxResults).map(card => {
              const link = card.querySelector('h2 a, .a-link-normal.s-no-outline');
              const url = link?.href || null;
              const name = card.querySelector(
                'h2 .a-text-normal, h2 span'
              )?.textContent.trim() || null;
              const priceText = card.querySelector('.a-price .a-offscreen')?.textContent.trim();
              const price = priceText ? parseFloat(priceText.replace(/[^0-9.]/g, '')) : null;
              const currency =
                priceText?.match(/[A-Z]{3}/)?.[0] ||
                (priceText?.includes('$') ? 'USD' : null);
              const image = card.querySelector('.s-image')?.src || null;
              const ratingText = card.querySelector('.a-icon-alt')?.textContent.trim();
              const rating = ratingText ? parseFloat(ratingText) : null;
              const rcText = card.querySelector('.a-size-small .a-link-normal')?.textContent.trim();
              const review_count = rcText ? parseInt(rcText.replace(/[^0-9]/g, '')) : null;
              const asin = card.getAttribute('data-asin') || null;
              return { url, name, price, currency, image, rating, review_count, asin };
            }).filter(item => item.url || item.name);
          }
        }

        // Strategy 3: eBay search results
        if (items.length === 0) {
          const cards = Array.from(document.querySelectorAll('.s-item:not(.s-item--placeholder)'));
          if (cards.length > 0) {
            items = cards.slice(0, maxResults).map(card => {
              const link = card.querySelector('.s-item__link');
              const url = link?.href || null;
              const name = card.querySelector('.s-item__title')?.textContent.trim() || null;
              const priceText = card.querySelector('.s-item__price')?.textContent.trim();
              const price = priceText ? parseFloat(priceText.replace(/[^0-9.]/g, '')) : null;
              const image = card.querySelector('.s-item__image img')?.src || null;
              const condition = card.querySelector(
                '.s-item__condition, .SECONDARY_INFO'
              )?.textContent.trim() || null;
              const shipping = card.querySelector(
                '.s-item__shipping, .s-item__logisticsCost'
              )?.textContent.trim() || null;
              return { url, name, price, currency: null, image, condition, shipping };
            }).filter(item => item.url || item.name);
          }
        }

        // Strategy 4: WooCommerce product grid
        if (items.length === 0) {
          const cards = Array.from(document.querySelectorAll('ul.products li.product'));
          if (cards.length > 0) {
            items = cards.slice(0, maxResults).map(card => {
              const link = card.querySelector('a.woocommerce-loop-product__link');
              const url = link?.href || null;
              const name = card.querySelector(
                '.woocommerce-loop-product__title'
              )?.textContent.trim() || null;
              const priceEl = card.querySelector('.price .woocommerce-Price-amount.amount');
              const image = card.querySelector(
                'img.attachment-woocommerce_thumbnail, img.wp-post-image'
              )?.src || null;
              const ratingEl = card.querySelector('.star-rating');
              const rating = ratingEl
                ? parseFloat(ratingEl.getAttribute('aria-label') || '') || null
                : null;
              return { url, name, price, currency: null, image, rating };
            }).filter(item => item.url || item.name);
          }
        }

        // Strategy 5: Shopify collection — JSON-LD Product array (multiple products)
        if (items.length === 0) {
          const productLds = flat.filter(l => l['@type'] === 'Product');
          if (productLds.length > 1) {
            items = productLds.slice(0, maxResults).map(p => {
              const offer = Array.isArray(p.offers) ? p.offers[0] : p.offers;
              return {
                url: p.url || p['@id'] || null,
                name: p.name || null,
                price: offer?.price != null ? parseFloat(offer.price) : null,
                currency: offer?.priceCurrency || null,
                image: Array.isArray(p.image) ? p.image[0] : (p.image || null),
                rating: p.aggregateRating?.ratingValue != null
                  ? parseFloat(p.aggregateRating.ratingValue)
                  : null
              };
            }).filter(item => item.url || item.name);
          }
        }

        // Strategy 6: Generic product cards heuristic
        if (items.length === 0) {
          const candidates = Array.from(
            document.querySelectorAll(
              'article[class*="product"], [class*="product-card"], ' +
              '[class*="product-item"], [class*="product-tile"]'
            )
          );
          const results = candidates.slice(0, maxResults).map(card => {
            const link = card.querySelector('a[href]');
            const name = card.querySelector(
              'h2, h3, h4, [class*="title"], [class*="name"]'
            )?.textContent.trim() || null;
            const priceText = card.querySelector('[class*="price"]')?.textContent.trim();
            const price = priceText ? parseFloat(priceText.replace(/[^0-9.]/g, '')) : null;
            const image = card.querySelector('img')?.src || null;
            return { url: link?.href || null, name, price, currency: null, image };
          }).filter(item => (item.url || item.name) && item.price != null);
          if (results.length > 0) items = results;
        }

        if (items.length === 0) {
          return JSON.stringify({
            error: true,
            message: 'No product listings found on this page. Ensure this is a ' +
              'category, search results, or product listing page.'
          });
        }
        return JSON.stringify({ count: items.length, items });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    js = js_template.replace('MAX_RESULTS', str(args.max_results))
    print(js)


if __name__ == '__main__':
    main()
