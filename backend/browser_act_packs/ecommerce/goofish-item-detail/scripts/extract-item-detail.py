import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

    js = r"""
    (function() {
      try {
        var bodyText = document.body.innerText || '';
        if (
          bodyText.includes('网络不见了')
          || bodyText.includes('页面不存在')
          || bodyText.includes('糟糕！宝贝被删掉了')
        ) {
          return JSON.stringify({ error: true, message: 'Page failed to load or item removed' });
        }

        var captcha = document.querySelector('[class*="baxia-dialog"], [class*="punish"]');
        if (captcha && captcha.offsetParent !== null) {
          return JSON.stringify({ error: true, message: 'CAPTCHA verification required' });
        }

        var itemContainer = document.querySelector('[class*="item-container"]');
        if (!itemContainer) {
          return JSON.stringify({
            error: true,
            message: 'Item container not found — page may still be loading'
          });
        }

        // Description (also serves as title on Goofish detail pages)
        var descEl = document.querySelector(
          '[class*="main--"][class*="open"], span[class*="desc--"]'
        );
        var description = descEl ? descEl.textContent.trim() : null;
        var title = description;

        // Price: current/bargain price in item-main-info area
        var priceEl = document.querySelector(
          '[class*="item-main-info"] [class*="price--"][class*="windows"]'
        );
        if (!priceEl) {
          priceEl = document.querySelector('[class*="value--"] [class*="price--"]');
        }
        var price = priceEl ? priceEl.textContent.trim() : null;

        // Original price from tips area
        var tipsEl = document.querySelector('[class*="item-main-info"] [class*="tips--"]');
        var originalPrice = null;
        if (tipsEl) {
          var tipsText = tipsEl.textContent || '';
          var opMatch = tipsText.match(/直接买\\s*[￥¥](\\d+(?:\\.\\d{1,2})?)/);
          if (opMatch) originalPrice = opMatch[1];
        }

        // Seller info
        var nickEl = document.querySelector('[class*="item-user-info-nick"]');
        var sellerName = nickEl ? nickEl.textContent.trim() : null;

        var avatarEl = document.querySelector('[class*="item-user-info-avatar"] img');
        var sellerAvatar = avatarEl ? (avatarEl.src || null) : null;

        var labelEls = document.querySelectorAll('[class*="item-user-info-label"]');
        var sellerLabels = Array.from(labelEls).map(function(el) {
          return el.textContent.trim();
        }).filter(Boolean);

        // Images from gallery (deduplicate by base filename)
        var imgEls = document.querySelectorAll('[class*="item-main-window"] img[src*="alicdn"]');
        if (imgEls.length === 0) {
          imgEls = document.querySelectorAll('[class*="item-main"] img[src*="alicdn"]');
        }
        var seen = {};
        var images = [];
        Array.from(imgEls).forEach(function(el) {
          var src = el.src || el.getAttribute('data-src');
          if (!src || src.indexOf('alicdn') === -1 || src.indexOf('tps-2-2') > -1) return;
          var keyMatch = src.match(/O1CN[^.]+/);
          var key = keyMatch ? keyMatch[0] : src;
          if (!seen[key]) {
            seen[key] = true;
            images.push(src);
          }
        });

        // Item attributes/tags
        var tagEls = document.querySelectorAll('[class*="labels--"] [class*="item--"]');
        var tags = Array.from(tagEls).map(function(el) {
          return el.textContent.trim();
        }).filter(Boolean);

        // Want count
        var wantEl = document.querySelector('[class*="want--"]');
        var wantCount = null;
        if (wantEl) {
          var wantText = wantEl.textContent || '';
          var wantMatch = wantText.match(/(\\d+人想要)/);
          wantCount = wantMatch ? wantMatch[1] : wantText.trim();
        }

        var itemUrl = window.location.href;
        var idMatch = itemUrl.match(/[?&]id=(\\d+)/);

        if (!description && !price) {
          return JSON.stringify({
            error: true,
            message: 'Item content not loaded — page may still be loading'
          });
        }

        return JSON.stringify({
          item_id: idMatch ? idMatch[1] : null,
          item_url: itemUrl,
          title: title,
          price: price,
          original_price: originalPrice,
          seller_name: sellerName,
          seller_avatar: sellerAvatar,
          seller_labels: sellerLabels.length > 0 ? sellerLabels : null,
          description: description,
          images: images.length > 0 ? images : null,
          tags: tags.length > 0 ? tags : null,
          want_count: wantCount
        });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)

if __name__ == '__main__':
    main()
