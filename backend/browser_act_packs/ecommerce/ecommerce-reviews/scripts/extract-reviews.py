import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-reviews', type=int, default=20)
    args = parser.parse_args()

    js_template = r"""
    (function() {
      try {
        const maxReviews = MAX_REVIEWS;
        let reviews = [];

        // Strategy 1: JSON-LD Review[]
        const lds = Array.from(
          document.querySelectorAll('script[type="application/ld+json"]')
        ).map(s => {
          try { return JSON.parse(s.textContent); } catch(e) { return null; }
        }).filter(Boolean);
        const flat = lds.flatMap(l => Array.isArray(l) ? l : [l]);
        const pld = flat.find(l => l['@type'] === 'Product' && l.review);
        if (pld?.review) {
          const ldRevs = Array.isArray(pld.review) ? pld.review : [pld.review];
          reviews = ldRevs.slice(0, maxReviews).map(r => ({
            reviewer: r.author?.name || (typeof r.author === 'string' ? r.author : null),
            rating: r.reviewRating?.ratingValue != null
              ? parseFloat(r.reviewRating.ratingValue)
              : null,
            date: r.datePublished || null,
            title: r.name || null,
            body: r.reviewBody || null,
            verified: null,
            helpful_votes: null
          }));
        }

        // Strategy 2: Amazon reviews
        if (reviews.length === 0) {
          const amzCards = Array.from(document.querySelectorAll('[data-hook="review"]'));
          if (amzCards.length > 0) {
            reviews = amzCards.slice(0, maxReviews).map(card => {
              const ratingText = card.querySelector(
                '[data-hook="review-star-rating"] .a-icon-alt, '
                '[data-hook="cmps-review-star-rating"] .a-icon-alt'
              )?.textContent.trim();
              const helpfulText = card.querySelector(
                '[data-hook="helpful-vote-statement"]'
              )?.textContent.trim();
              return {
                reviewer: card.querySelector('.a-profile-name')?.textContent.trim() || null,
                rating: ratingText ? parseFloat(ratingText) : null,
                date: card.querySelector('[data-hook="review-date"]')?.textContent.trim() || null,
                title: card.querySelector(
                  '[data-hook="review-title"] span:not([class])'
                )?.textContent.trim() || null,
                body: card.querySelector('[data-hook="review-body"] span')
                  ?.textContent.trim() || null,
                verified: !!card.querySelector('[data-hook="avp-badge"]'),
                helpful_votes: helpfulText ? parseInt(helpfulText.match(/\d+/)?.[0] || '0') : 0
              };
            });
          }
        }

        // Strategy 3: WooCommerce reviews
        if (reviews.length === 0) {
          const wooCards = Array.from(
            document.querySelectorAll('.woocommerce-Reviews .review, ol.commentlist li')
          );
          if (wooCards.length > 0) {
            reviews = wooCards.slice(0, maxReviews).map(card => {
              const ratingEl = card.querySelector('.star-rating');
              const ratingAria = ratingEl?.getAttribute('aria-label');
              const rating = ratingAria ? parseFloat(ratingAria) : null;
              return {
                reviewer: card.querySelector(
                  '.reviewer, .woocommerce-review__author'
                )?.textContent.trim() || null,
                rating,
                date: card.querySelector('[datetime]')?.getAttribute('datetime')
                  || card.querySelector('time')?.textContent.trim() || null,
                title: null,
                body: card.querySelector('.description p, .comment-text p')
                  ?.textContent.trim() || null,
                verified: !!card.querySelector('.woocommerce-review__verified'),
                helpful_votes: null
              };
            });
          }
        }

        // Strategy 4: Generic [itemprop="review"]
        if (reviews.length === 0) {
          const genericCards = Array.from(document.querySelectorAll('[itemprop="review"]'));
          if (genericCards.length > 0) {
            reviews = genericCards.slice(0, maxReviews).map(card => {
              const ratingEl = card.querySelector('[itemprop="ratingValue"]');
              return {
                reviewer: card.querySelector('[itemprop="author"]')?.textContent.trim() || null,
                rating: ratingEl
                  ? parseFloat(ratingEl.getAttribute('content') || ratingEl.textContent)
                  : null,
                date: card.querySelector('[itemprop="datePublished"]')?.getAttribute('content')
                  || card.querySelector('[itemprop="datePublished"]')?.textContent.trim()
                  || null,
                title: card.querySelector('[itemprop="name"]')?.textContent.trim() || null,
                body: card.querySelector(
                  '[itemprop="description"], [itemprop="reviewBody"]'
                )?.textContent.trim() || null,
                verified: null,
                helpful_votes: null
              };
            });
          }
        }

        // Strategy 5: Generic .review containers
        if (reviews.length === 0) {
          const cards = Array.from(
            document.querySelectorAll('.review-item, .review-card, .product-review')
          );
          reviews = cards.slice(0, maxReviews).map(card => ({
            reviewer: card.querySelector(
              '[class*="name"], [class*="author"], [class*="user"]'
            )?.textContent.trim() || null,
            rating: null,
            date: card.querySelector('time, [class*="date"]')
              ?.textContent.trim() || null,
            title: card.querySelector('h3, h4, [class*="title"]')
              ?.textContent.trim() || null,
            body: card.querySelector('p, [class*="body"], [class*="text"]')
              ?.textContent.trim() || null,
            verified: null,
            helpful_votes: null
          })).filter(r => r.body);
        }

        if (reviews.length === 0) {
          return JSON.stringify({
            error: true,
            message: 'No reviews found on this page. Navigate to the product reviews '
              + 'section or reviews page first.'
          });
        }
        return JSON.stringify({ count: reviews.length, reviews });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    js = js_template.replace('MAX_REVIEWS', str(args.max_reviews))
    print(js)


if __name__ == '__main__':
    main()
