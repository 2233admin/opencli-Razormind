import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('item_id')   # itemId (for documentation only; page already loaded)
    parser.parse_args()

    js = r"""
    (function() {
      try {
        var commentItems = document.querySelectorAll('[class*="Comment--"]');
        if (!commentItems || commentItems.length === 0) {
          return JSON.stringify({
            error: true,
            message: (
              'No review items found. Reviews section may not have loaded — ' +
              'try scrolling down further (scroll down --amount 8000) and ' +
              'waiting, then retry.'
            )
          });
        }
        var reviews = Array.from(commentItems).map(function(item) {
          var usernameEl = item.querySelector('[class*="userName--"]');
          var username = usernameEl ? usernameEl.textContent.trim() : null;

          // Extract date and purchased SKU from comment header text
          // Format: "{date}已购：{sku}" or embedded in the header div
          var headerEl = item.querySelector('[class*="userInfo--"], [class*="header--"]');
          var headerText = headerEl ? headerEl.textContent.trim() : '';
          var dateMatch = headerText.match(/(\d{4}-\d{2}-\d{2})/);
          var date = dateMatch ? dateMatch[1] : null;
          var skuMatch = headerText.match(/已购[：:](.*?)(?:\n|$)/);
          var purchasedSku = skuMatch ? skuMatch[1].trim() : null;
          if (!purchasedSku) {
            // Try extracting from item text after the username
            var fullText = item.textContent;
            var skuMatch2 = fullText.match(/已购[：:](.*?)(?=商品|\d{4}-|$)/);
            purchasedSku = skuMatch2 ? skuMatch2[1].trim().slice(0, 150) : null;
          }

          // Extract review content text
          var contentEl = item.querySelector(
            '[class*="content--"], [class*="reviewContent--"], ' +
            '[class*="reviewText--"]'
          );
          var content = '';
          if (contentEl) {
            content = contentEl.textContent.trim();
          } else {
            // Fallback: extract text that is not username/date/sku from the item
            var allText = item.textContent.trim();
            if (username) allText = allText.replace(username, '');
            if (date) allText = allText.replace(date, '');
            if (purchasedSku) {
              allText = allText
                .replace('已购：' + purchasedSku, '')
                .replace('已购:' + purchasedSku, '');
            }
            content = allText.trim();
          }

          // Extract review photos
          var photoEls = item.querySelectorAll(
            'img[src*="rate"], img[src*="-0-rate"], img[src*="oss"]'
          );
          if (!photoEls || photoEls.length === 0) {
            photoEls = item.querySelectorAll('img[src*="alicdn"], img[src*="gw.alicdn"]');
          }
          var photos = Array.from(photoEls)
            .map(function(img) { return img.src; })
            .filter(function(src) {
              return src && !src.includes('avatar') && !src.includes('logo');
            });

          // Rating is often not shown as a visible number in current layout
          var ratingEl = item.querySelector(
            '[class*="rating--"], [class*="star--"], [class*="score--"]'
          );
          var rating = ratingEl ? ratingEl.textContent.trim() : null;

          return {
            username: username,
            date: date,
            purchasedSku: purchasedSku,
            content: content,
            photos: photos,
            rating: rating
          };
        });

        return JSON.stringify(reviews);
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)

if __name__ == '__main__':
    main()
