import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.parse_args()

    js = """
(function() {
  try {
    var url = window.location.href;
    var placeIdM = url.match(/!1s([^!]+)!/);
    var coordM = url.match(/@(-?[0-9.]+),(-?[0-9.]+)/);
    var placeId = placeIdM ? placeIdM[1] : null;
    var lat = coordM ? parseFloat(coordM[1]) : null;
    var lng = coordM ? parseFloat(coordM[2]) : null;

    // Validate we are on a place detail page (not the main maps page)
    var isPlacePage = url.indexOf('/maps/place/') !== -1
      || url.indexOf('place_id') !== -1
      || placeId;
    if (!isPlacePage) {
      return JSON.stringify({
        error: true,
        message: 'Not on a place detail page. Navigate to a Google Maps '
          + 'place URL first.'
      });
    }
    var h1 = document.querySelector('h1');
    var name = h1 ? h1.textContent.trim() : null;
    if (!name) {
      return JSON.stringify({
        error: true,
        message: 'Place detail not loaded yet. Make sure h1 is visible '
          + 'before running.'
      });
    }

    var ratingEl = document.querySelector('[aria-label$=" stars "]')
      || document.querySelector('[aria-label*="stars"]');
    var ratingText = ratingEl ? ratingEl.getAttribute('aria-label') : '';
    var ratingM = ratingText.match(/([0-9.]+)/);
    var rating = ratingM ? parseFloat(ratingM[1]) : null;

    var reviewEl = document.querySelector('[aria-label*="reviews"]');
    var reviewText = reviewEl ? reviewEl.getAttribute('aria-label') : '';
    var reviewM = reviewText.match(/([0-9,]+)/);
    var review_count = reviewM ? parseInt(reviewM[1].replace(',', '')) : null;

    var catSpans = Array.from(document.querySelectorAll('[jsaction*="category"]'));
    var category = catSpans.length > 0 ? catSpans[0].textContent.trim() : null;

    var addressEl = document.querySelector('[data-item-id="address"]');
    var address = addressEl ? addressEl.textContent.trim() : null;

    var phoneEl = Array.from(document.querySelectorAll('[data-item-id]')).find(function(el) {
      return (el.getAttribute('data-item-id') || '').startsWith('phone:');
    });
    var phone = phoneEl ? phoneEl.textContent.trim() : null;

    var websiteEl = document.querySelector('[data-item-id="authority"]');
    var website = websiteEl ? websiteEl.getAttribute('href') : null;

    var menuEl = document.querySelector('[data-item-id="menu"]');
    var menu_url = menuEl ? menuEl.getAttribute('href') : null;

    var priceEls = Array.from(document.querySelectorAll('[data-item-id]')).filter(function(el) {
      return (el.getAttribute('data-item-id') || '').match(/^[0-9]+$/);
    });
    var price_range = priceEls.length > 0 ? priceEls[0].textContent.trim() : null;

    var hoursRows = Array.from(document.querySelectorAll('tr'))
      .map(function(tr) { return tr.textContent.trim(); })
      .filter(function(t) {
        return t.match(/Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday/);
      });
    var hours = hoursRows.length > 0 ? hoursRows : null;

    var serviceOpts = Array.from(document.querySelectorAll(
      '[aria-label*="ine-in"],[aria-label*="akeout"],'
      + '[aria-label*="elivery"],[aria-label*="ickup"]'
    )).map(function(el) { return el.getAttribute('aria-label'); });

    var aboutPattern = new RegExp(
      '^(Has|Accepts|No |Good for|Popular for|Casual|Cozy|Trendy|'
      + 'Upscale|Quick bite|Family|Serves great|Has great)'
    );
    var aboutLabels = Array.from(document.querySelectorAll('[aria-label]')).map(function(el) {
      return el.getAttribute('aria-label');
    }).filter(function(l) {
      return l && l.length > 5 && l.length < 80
        && l.match(aboutPattern);
    });

    var locatedIn = document.querySelector('[data-item-id="locatedin"]');
    var located_in = locatedIn ? locatedIn.textContent.trim() : null;

    return JSON.stringify({
      place_id: placeId,
      name: name,
      rating: rating,
      review_count: review_count,
      category: category,
      address: address,
      located_in: located_in,
      phone: phone,
      website: website,
      menu_url: menu_url,
      price_range: price_range,
      coordinates: (lat && lng) ? { lat: lat, lng: lng } : null,
      hours: hours,
      service_options: serviceOpts.length > 0 ? serviceOpts : null,
      amenities: aboutLabels.length > 0 ? aboutLabels.slice(0, 20) : null,
      maps_url: url
    });
  } catch(e) {
    return JSON.stringify({ error: true, message: e.message });
  }
})()
"""

    print(js)


if __name__ == '__main__':
    main()
