import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.parse_args()

    js = r"""
    (function() {
      try {
        // rel=next link (SEO-standard)
        const relNext = document.querySelector('link[rel="next"]')?.href;
        if (relNext) {
          return JSON.stringify({
            next_url: relNext, has_next: true, method: 'rel-next'
          });
        }

        // Amazon pagination
        const amzNext = document.querySelector('.a-pagination .a-last:not(.a-disabled) a')?.href;
        if (amzNext) return JSON.stringify({ next_url: amzNext, has_next: true, method: 'amazon' });

        // eBay pagination
        const ebayNext = document.querySelector(
          '.pagination__next a, a[aria-label="Go to next search page"]'
        )?.href;
        if (ebayNext) {
          return JSON.stringify({
            next_url: ebayNext, has_next: true, method: 'ebay'
          });
        }

        // WooCommerce pagination
        const wooNext = document.querySelector('.woocommerce-pagination .next')?.href;
        if (wooNext) {
          return JSON.stringify({
            next_url: wooNext, has_next: true, method: 'woocommerce'
          });
        }

        // Generic next link patterns
        const genericNext = document.querySelector(
          'a[aria-label*="Next"], a[aria-label*="next"], .next-page a, '
          + '.pagination .next a, [class*="pagination"] a[rel="next"], '
          + '.pager .next a, a[class*="next-page"], a[class*="page-next"]'
        )?.href;
        if (genericNext) {
          return JSON.stringify({
            next_url: genericNext, has_next: true, method: 'generic'
          });
        }

        // URL-based page parameter increment
        const url = new URL(window.location.href);
        const pageKey = url.searchParams.has('page')
          ? 'page'
          : (url.searchParams.has('p')
            ? 'p'
            : (url.searchParams.has('pg') ? 'pg' : null));
        if (pageKey) {
          const nextPage = parseInt(url.searchParams.get(pageKey)) + 1;
          url.searchParams.set(pageKey, nextPage);
          const hasItems = document.querySelectorAll(
            '[data-component-type="s-search-result"], .s-item, '
            + 'ul.products li.product, [class*="product-card"]'
          ).length > 0;
          if (hasItems) {
            return JSON.stringify({
              next_url: url.href, has_next: true, method: 'url-param'
            });
          }
        }

        return JSON.stringify({ next_url: null, has_next: false });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
