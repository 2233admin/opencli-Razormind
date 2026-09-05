import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    # 'shallow' = homepage only; 'deep' = also discover contact/about pages
    parser.add_argument('--depth', default='shallow')
    args = parser.parse_args()

    depth = args.depth

    js = r"""
(function() {
  try {
    var html = document.documentElement.innerHTML;
    var url = window.location.href;
    var origin = window.location.origin;

    // Extract emails from mailto links and visible text
    var emailSet = {};
    var mailtoRe = /mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})/gi;
    var emailRe = /\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b/g;
    var spamWords = ['example','pixel','sentry','schema.org','shopify','apple.com','wix.com',
      'google.com','amazon.com','cloudflare','w3.org','facebook.com','instagram.com',
      'twitter.com','tiktok.com','youtube.com','linkedin.com','pinterest.com','discord.com'];
    function isSpam(e) { return spamWords.some(function(w) { return e.indexOf(w) !== -1; }); }

    var m;
    while ((m = mailtoRe.exec(html)) !== null) {
      var decoded = '';
      try {
        decoded = decodeURIComponent(m[1]).toLowerCase();
      } catch(x) {
        decoded = m[1].toLowerCase();
      }
      if (!isSpam(decoded)) emailSet[decoded] = true;
    }
    var bodyText = document.body ? (document.body.innerText || '') : '';
    while ((m = emailRe.exec(bodyText)) !== null) {
      var e = m[1].toLowerCase();
      if (!isSpam(e)) emailSet[e] = true;
    }
    var emails = Object.keys(emailSet).slice(0, 20);

    // Extract social media profile links
    function extractSocial(pattern) {
      var re = new RegExp(pattern, 'gi');
      var found = {};
      var match;
      while ((match = re.exec(html)) !== null) {
        var link = match[0].split('"')[0].split("'")[0]
          .replace(/&amp;/g, '&').replace(/[?#].*$/, '').replace(/\/$/, '');
        if (link.length < 150 && link.length > 15) found[link] = true;
      }
      return Object.keys(found).slice(0, 3);
    }

    var socials = {};
    var fbLinks = extractSocial(
      'https?://(?:www[.])?facebook[.]com/' +
      '(?!sharer|share|dialog|tr[?]|plugins|embed|video|events|groups)' +
      '[a-zA-Z0-9._/%-]+'
    );
    var igLinks = extractSocial(
      'https?://(?:www[.])?instagram[.]com/' +
      '(?!p/|reel/|explore/|_/|share/|accounts/)' +
      '[a-zA-Z0-9._]+/?'
    );
    var twLinks = extractSocial(
      'https?://(?:www[.])?(?:twitter|x)[.]com/' +
      '(?!intent/|share[?]|hashtag|home|search|i/)' +
      '[a-zA-Z0-9._]+/?'
    );
    var liLinks = extractSocial(
      'https?://(?:www[.])?linkedin[.]com/(?:company|in)/' +
      '[a-zA-Z0-9._/-]+'
    );
    var ytLinks = extractSocial(
      'https?://(?:www[.])?youtube[.]com/(?:@|c/|channel/|user/)' +
      '[a-zA-Z0-9._/-]+'
    );
    var ttLinks = extractSocial('https?://(?:www[.])?tiktok[.]com/@[a-zA-Z0-9._/-]+');
    var piLinks = extractSocial('https?://(?:www[.])?pinterest[.]com/[a-zA-Z0-9._/-]+');
    var dcLinks = extractSocial('https?://(?:www[.])?discord[.](?:gg|com/invite)/[a-zA-Z0-9._/-]+');

    if (fbLinks.length) socials.facebook = fbLinks;
    if (igLinks.length) socials.instagram = igLinks;
    if (twLinks.length) socials.twitter = twLinks;
    if (liLinks.length) socials.linkedin = liLinks;
    if (ytLinks.length) socials.youtube = ytLinks;
    if (ttLinks.length) socials.tiktok = ttLinks;
    if (piLinks.length) socials.pinterest = piLinks;
    if (dcLinks.length) socials.discord = dcLinks;

    // Extract phone numbers from visible text
    var phoneRe = /(?:[+]?1[ .\-]?)?[(]?([0-9]{3})[)]?[ .\-]?([0-9]{3})[ .\-]?([0-9]{4})/g;
    var phones = {};
    while ((m = phoneRe.exec(bodyText)) !== null) {
      phones[m[0].trim()] = true;
    }
    var phone_numbers = Object.keys(phones).slice(0, 5);

    // Find contact/about page links (only when --depth deep)
    var subpages = [];
    if ('__DEPTH__' === 'deep') {
      var contactLinks = Array.from(document.querySelectorAll('a[href]')).filter(function(a) {
        var href = a.href || '';
        var text = (a.textContent || '').toLowerCase();
        return (href.indexOf('/contact') !== -1 ||
                href.indexOf('/about') !== -1 ||
                href.indexOf('/reach') !== -1 ||
                text.match(/contact|about|reach us|get in touch/)) &&
               href.indexOf(origin) === 0 && href !== url;
      }).map(function(a) { return a.href.split('#')[0]; });
      var seen = {};
      contactLinks.forEach(function(l) { seen[l] = true; });
      subpages = Object.keys(seen).slice(0, 5);
    }

    return JSON.stringify({
      source_url: url,
      emails: emails,
      phone_numbers: phone_numbers,
      social_media: socials,
      contact_subpages: subpages
    });
  } catch(e) {
    return JSON.stringify({ error: true, message: e.message });
  }
})()
""".replace('__DEPTH__', depth)

    print(js)


if __name__ == '__main__':
    main()
