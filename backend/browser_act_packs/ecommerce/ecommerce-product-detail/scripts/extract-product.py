import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.parse_args()

    js = r"""
    (function() {
      try {
        const result = {};

        // Layer 1: JSON-LD (schema.org/Product)
        const lds = Array.from(
          document.querySelectorAll('script[type="application/ld+json"]')
        ).map(s => {
          try { return JSON.parse(s.textContent); } catch(e) { return null; }
        }).filter(Boolean);
        const flat = lds.flatMap(l => Array.isArray(l) ? l : [l]);
        const pld = flat.find(l => l['@type'] === 'Product');
        const rld = flat.find(
          l => l['@type'] === 'AggregateRating' && l.itemReviewed?.['@type'] === 'Product'
        );

        if (pld) {
          result.name = pld.name || null;
          result.description = pld.description || null;
          result.brand = (typeof pld.brand === 'string' ? pld.brand : pld.brand?.name) || null;
          const imgs = Array.isArray(pld.image) ? pld.image : (pld.image ? [pld.image] : []);
          result.image = imgs[0] || null;
          result.images = imgs.length > 0 ? imgs : null;
          result.category = Array.isArray(pld.category)
            ? pld.category
            : (pld.category ? [pld.category] : null);
          result.sku = pld.sku || null;
          result.gtin = pld.gtin || pld.gtin14 || pld.gtin13 || pld.gtin12 || pld.gtin8 || null;
          result.mpn = pld.mpn || null;
          result.isbn = pld.isbn || null;
          const offer = Array.isArray(pld.offers) ? pld.offers[0] : pld.offers;
          if (offer) {
            result.price = offer.price != null ? parseFloat(offer.price) : null;
            result.price_currency = offer.priceCurrency || null;
            result.availability = offer.availability
              ? offer.availability.replace('https://schema.org/', '')
              : null;
            result.seller =
              (typeof offer.seller === 'string' ? offer.seller : offer.seller?.name) || null;
          }
          const agg = pld.aggregateRating || rld;
          if (agg) {
            result.rating = agg.ratingValue != null ? parseFloat(agg.ratingValue) : null;
            result.review_count =
              agg.reviewCount != null
                ? parseInt(agg.reviewCount)
                : (agg.ratingCount != null ? parseInt(agg.ratingCount) : null);
          }
          if (pld.hasVariant) {
            const vars = Array.isArray(pld.hasVariant) ? pld.hasVariant : [pld.hasVariant];
            result.variants = vars.slice(0, 30).map(v => ({
              name: v.name,
              sku: v.sku,
              price: v.offers?.price != null ? parseFloat(v.offers.price) : null
            }));
          }
          result._source = 'json-ld';
        }

        // Layer 2a: Shopify — window.ShopifyAnalytics.meta.product
        if (window.ShopifyAnalytics?.meta?.product) {
          const sp = window.ShopifyAnalytics.meta.product;
          if (!result.brand) result.brand = sp.vendor || null;
          if (!result.category && sp.type) result.category = [sp.type];
          if (!result.sku && sp.variants?.[0]?.sku) result.sku = sp.variants[0].sku;
          if (!result.variants && sp.variants?.length > 0) {
            result.variants = sp.variants.slice(0, 30).map(v => ({
              name: v.public_title || v.name,
              sku: v.sku,
              price: v.price / 100
            }));
          }
          result._platform = 'shopify';
        }

        // Layer 2b: Amazon DOM
        if (window.location.hostname.includes('amazon.')) {
          const asin = window.location.pathname.match(/\/dp\/([A-Z0-9]{10})/)?.[1];
          if (!result.name) {
            result.name = document.querySelector('#productTitle')?.textContent.trim() || null;
          }
          if (!result.price) {
            const pt = document.querySelector('.a-price .a-offscreen')?.textContent.trim();
            if (pt) {
              result.price = parseFloat(pt.replace(/[^0-9.]/g, ''));
              result.price_currency =
                pt.match(/[A-Z]{3}/)?.[0] || (pt.includes('$') ? 'USD' : null);
            }
          }
          if (!result.brand) {
            result.brand = document.querySelector('#bylineInfo')?.textContent
              .trim().replace(/^(Brand:|Visit the|by\s+)/i, '').trim() || null;
          }
          if (!result.rating) {
            const rt = document.querySelector('#acrPopover')?.getAttribute('title') || '';
            result.rating = rt ? parseFloat(rt) : null;
          }
          if (!result.review_count) {
            const rc = document.querySelector('#acrCustomerReviewText')?.textContent.trim();
            result.review_count = rc ? parseInt(rc.replace(/[^0-9]/g, '')) : null;
          }
          if (!result.availability) {
            result.availability =
              document.querySelector('#availability span')?.textContent.trim() || null;
          }
          if (!result.image) {
            result.image = document.querySelector('#landingImage')?.src
              || document.querySelector('#imgBlkFront')?.src || null;
          }
          if (!result.images || result.images.length === 0) {
            result.images = Array.from(
              document.querySelectorAll('.imageThumbnail img, #altImgTmb img')
            ).map(img => img.src.replace(/\._[A-Z0-9_,]+_\./i, '._SX522_.'))
              .filter(s => s.length > 20 && !s.includes('grey-pixel')).slice(0, 10);
          }
          if (!result.description) {
            result.description = Array.from(
              document.querySelectorAll('#feature-bullets .a-list-item')
            ).map(el => el.textContent.trim()).filter(t => t.length > 5).join('\n') || null;
          }
          const details = {};
          document.querySelectorAll('#detailBulletsWrapper_feature_div li').forEach(li => {
            const spans = li.querySelectorAll('span');
            if (spans.length >= 2) {
              const k = spans[0].textContent.replace(':', '').trim();
              const v = spans[1]?.textContent.trim();
              if (k && v && k.length < 60) details[k] = v;
            }
          });
          document.querySelectorAll(
            '#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr'
          ).forEach(row => {
            const k = row.querySelector('th')?.textContent.trim();
            const v = row.querySelector('td')?.textContent.trim().replace(/\s+/g, ' ');
            if (k && v) details[k] = v;
          });
          if (asin) { details['ASIN'] = asin; if (!result.sku) result.sku = asin; }
          if (Object.keys(details).length > 0) result.identifiers = details;
          if (!result._platform) result._platform = 'amazon';
        }

        // Layer 2c: WooCommerce DOM
        const wooPrice = document.querySelector('.price .woocommerce-Price-amount.amount');
        if (wooPrice && !result._platform) {
          if (!result.name) {
            result.name =
              document.querySelector('.product_title, h1.product_title')?.textContent.trim()
              || null;
          }
          if (!result.price) {
            result.price = parseFloat(wooPrice.textContent.replace(/[^0-9.]/g, '')) || null;
          }
          if (!result.sku) result.sku = document.querySelector('.sku')?.textContent.trim() || null;
          if (!result.description) {
            result.description = document.querySelector(
              '.woocommerce-product-details__short-description'
            )?.textContent.trim() || null;
          }
          if (!result.category) {
            result.category = Array.from(document.querySelectorAll('.posted_in a'))
              .map(a => a.textContent.trim());
          }
          if (!result.availability) {
            result.availability = document.querySelector('.stock')?.textContent.trim() || null;
          }
          if (!result.rating) {
            const stars = document.querySelector('.star-rating')?.getAttribute('aria-label');
            result.rating = stars ? parseFloat(stars) : null;
          }
          if (!result.review_count) {
            const rc = document.querySelector('.woocommerce-review-link')?.textContent
              .trim().match(/\d+/)?.[0];
            result.review_count = rc ? parseInt(rc) : null;
          }
          result._platform = 'woocommerce';
        }

        // Layer 3: OG meta fallback
        if (!result.name) {
          result.name = document.querySelector('meta[property="og:title"]')
            ?.getAttribute('content') || null;
        }
        if (!result.image) {
          result.image = document.querySelector('meta[property="og:image"]')
            ?.getAttribute('content') || null;
        }
        if (!result.description) {
          result.description = document.querySelector(
            'meta[property="og:description"], meta[name="description"]'
          )?.getAttribute('content') || null;
        }
        if (!result.price) {
          const ogp = document.querySelector('meta[property="og:price:amount"]')
            ?.getAttribute('content');
          if (ogp) result.price = parseFloat(ogp);
        }
        if (!result.price_currency) {
          result.price_currency = document.querySelector('meta[property="og:price:currency"]')
            ?.getAttribute('content') || null;
        }

        // Layer 4: Microdata fallback
        if (!result.name) {
          result.name = document.querySelector('[itemprop="name"]')?.textContent.trim() || null;
        }
        if (!result.price) {
          const mp = document.querySelector('[itemprop="price"]');
          if (mp) {
            result.price = parseFloat(
              mp.getAttribute('content') || mp.textContent.replace(/[^0-9.]/g, '')
            ) || null;
          }
        }
        if (!result.sku) {
          result.sku = document.querySelector('[itemprop="sku"]')?.textContent.trim()
            || document.querySelector('[itemprop="sku"]')?.getAttribute('content') || null;
        }
        if (!result.brand) {
          result.brand = document.querySelector(
            '[itemprop="brand"] [itemprop="name"], [itemprop="brand"]'
          )?.textContent.trim() || null;
        }

        result.url = window.location.href;

        if (!result.name && !result.price) {
          return JSON.stringify({
            error: true,
            message: 'No product data found. Ensure this is a product detail page and'
              + ' the page has fully loaded.'
          });
        }
        return JSON.stringify(result);
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
