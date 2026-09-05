import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "item_id"
    )  # Taobao/Tmall itemId (for documentation only; page already loaded)
    args = parser.parse_args()

    js = f"""
    (function() {{
      try {{
        var result = {{}};
        result.itemId = new URLSearchParams(location.search).get('id') || '{args.item_id}';
        result.itemUrl = location.href;
        result.isTmall = location.hostname.indexOf('tmall.com') >= 0;

        var titleEl = document.querySelector('[class*="mainTitle--"]');
        result.title = titleEl ? titleEl.textContent.trim() : null;
        if (!result.title) {{
          return JSON.stringify({{ error: true, message:
            'Title not found. Product page may not have loaded correctly.' }});
        }}

        var priceWrap = document.querySelector('[class*="priceWrap--"]');
        var priceText = priceWrap ? priceWrap.querySelector('[class*="text--"]') : null;
        var priceSymbol = priceWrap ? priceWrap.querySelector('[class*="symbol--"]') : null;
        result.price = priceText ? parseFloat(priceText.textContent.trim()) : null;
        result.priceFormatted = (priceSymbol && priceText)
          ? (priceSymbol.textContent.trim() + priceText.textContent.trim()) : null;
        var subPriceText = priceWrap
          ? priceWrap.querySelector('[class*="subPrice--"] [class*="text--"]') : null;
        result.originalPrice = subPriceText ? parseFloat(subPriceText.textContent.trim()) : null;

        var shopHeader = document.querySelector('[class*="shopHeader--"]');
        var shopLink = shopHeader
          ? shopHeader.querySelector('a[href*="taobao.com"]') : null;
        var shopLinkText = shopLink ? shopLink.textContent.trim() : '';
        // Extract just the shop name: remove rating/metric text that follows the name
        var shopNameMatch = shopLinkText.match(
          /^([\\u4e00-\\u9fa5a-zA-Z0-9 ·&()\\-_]+?)(?=[0-9]{{1}}\\.[0-9]|\\d{{2}}VIP|好评率|$)/
        );
        result.shopName = shopNameMatch
          ? shopNameMatch[1].trim() : shopLinkText.slice(0, 30).trim();
        result.shopUrl = shopLink ? shopLink.href : null;
        var shopIdMatch = (shopLink ? shopLink.href : '').match(/shop(\\d+)/);
        result.shopId = shopIdMatch ? shopIdMatch[1] : null;

        var galleryImgs = Array.from(document.querySelectorAll('[class*="Gallery"] img'));
        var imgUrls = galleryImgs.map(function(img) {{
          return img.src.replace(/_q50[^.]*\\.jpg[^.]*$/, '').replace(/\\.webp$/, '');
        }}).filter(function(u) {{ return u && u.indexOf('alicdn.com') >= 0; }});
        var seenUrls = {{}};
        result.images = imgUrls.filter(function(u) {{
          if (seenUrls[u]) return false;
          seenUrls[u] = true;
          return true;
        }}).slice(0, 10);

        var skuItems = Array.from(document.querySelectorAll('[class*="valueItem--"]'));
        result.skuVariants = skuItems.map(function(el) {{ return el.textContent.trim(); }});

        var attrs = {{}};
        var emphTitles = Array.from(
          document.querySelectorAll('[class*="emphasisParamsInfoItemTitle--"]')
        );
        var emphSubs = Array.from(
          document.querySelectorAll('[class*="emphasisParamsInfoItemSubTitle--"]')
        );
        emphTitles.forEach(function(el, i) {{
          var name = emphSubs[i] ? emphSubs[i].textContent.trim() : null;
          if (name) attrs[name] = el.textContent.trim();
        }});
        var genTitles = Array.from(
          document.querySelectorAll('[class*="generalParamsInfoItemTitle--"]')
        );
        var genSubs = Array.from(
          document.querySelectorAll('[class*="generalParamsInfoItemSubTitle--"]')
        );
        genTitles.forEach(function(el, i) {{
          var val = genSubs[i] ? genSubs[i].textContent.trim() : null;
          if (val) attrs[el.textContent.trim()] = val;
        }});
        result.attributes = attrs;

        var bodyText = document.body.textContent;
        var reviewMatch = bodyText.match(/评价[·：]\\s*([0-9,+万]+)/);
        result.reviewCount = reviewMatch ? reviewMatch[1] : null;

        return JSON.stringify(result);
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)


if __name__ == "__main__":
    main()
