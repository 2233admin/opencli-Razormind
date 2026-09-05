import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser()
    parser.parse_args()

    js = """
    (function() {
      try {
        var socialDomains = ['x.com','twitter.com','instagram.com','facebook.com',
          'linkedin.com','tiktok.com','youtube.com','pinterest.com','reddit.com',
          'snapchat.com','threads.net','weibo.com','vk.com','discord.com','twitch.tv',
          'github.com','medium.com','quora.com','tumblr.com','soundcloud.com'];

        function isSocialDomain(url) {
          try {
            var host = new URL(url).hostname.replace(/^www\\./, '');
            return socialDomains.some(function(d) {
              return host === d || host.endsWith('.' + d);
            });
          } catch(e) { return false; }
        }

        function getPlatformName(url) {
          try {
            var host = new URL(url).hostname.replace(/^www\\./, '');
            var known = {
              'x.com': 'X', 'twitter.com': 'Twitter', 'instagram.com': 'Instagram',
              'facebook.com': 'Facebook', 'linkedin.com': 'LinkedIn', 'tiktok.com': 'TikTok',
              'youtube.com': 'YouTube', 'pinterest.com': 'Pinterest', 'reddit.com': 'Reddit',
              'snapchat.com': 'Snapchat', 'threads.net': 'Threads', 'weibo.com': 'Weibo',
              'vk.com': 'VK', 'discord.com': 'Discord', 'twitch.tv': 'Twitch',
              'github.com': 'GitHub', 'medium.com': 'Medium', 'quora.com': 'Quora',
              'tumblr.com': 'Tumblr', 'soundcloud.com': 'SoundCloud'
            };
            return known[host] || host.split('.')[0];
          } catch(e) { return ''; }
        }

        var containers = document.querySelectorAll('.tF2Cxc');
        if (!containers.length) {
          return JSON.stringify({
            error: true,
            message: (
              'No search result containers found (.tF2Cxc). Page may not have ' +
              'loaded or Google changed its layout.'
            )
          });
        }

        var items = [];
        containers.forEach(function(el) {
          var link = el.querySelector('a.zReHs');
          var url = link ? link.href : '';
          if (!url || !isSocialDomain(url)) return;

          var platformUser = el.querySelector('.VuuXrf')
            ? el.querySelector('.VuuXrf').textContent.trim() : '';
          var parts = platformUser.split(/\\s*\\u00b7\\s*/);

          var title = el.querySelector('h3.LC20lb')
            ? el.querySelector('h3.LC20lb').textContent.trim() : null;
          var snippet = el.querySelector('.VwiC3b')
            ? el.querySelector('.VwiC3b').textContent.trim() : null;
          var followers = el.querySelector('cite.qLRx3b')
            ? el.querySelector('cite.qLRx3b').textContent.trim() : null;

          items.push({
            platform: getPlatformName(url),
            username: parts[1] ? parts[1].trim() : '',
            url: url,
            title: title,
            snippet: snippet,
            followers: followers
          });
        });

        return JSON.stringify({ error: false, count: items.length, results: items });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
