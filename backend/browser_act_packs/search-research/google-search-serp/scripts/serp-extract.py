import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')

    js = r"""
    (function() {
      try {
        if (window.location.pathname.includes('/sorry/')) {
          return JSON.stringify({
            error: true,
            message: 'Google blocked the request — captcha required. Solve captcha and retry.'
          });
        }
        if (!document.querySelector('#search, #rso, #result-stats')) {
          return JSON.stringify({
            error: true,
            message: 'No search results found on page. Check if the page loaded correctly.'
          });
        }

        var urlParams = new URLSearchParams(window.location.search);
        var numResults = parseInt(urlParams.get('num') || '10');
        var start = parseInt(urlParams.get('start') || '0');
        var searchQuery = {
          term: urlParams.get('q') || '',
          url: window.location.href,
          device: 'DESKTOP',
          page: Math.floor(start / numResults) + 1,
          type: 'SEARCH',
          domain: window.location.hostname,
          countryCode: urlParams.get('gl') ? urlParams.get('gl').toUpperCase() : null,
          languageCode: urlParams.get('hl') || null
        };

        var statsText = document.querySelector('#result-stats')
          ? document.querySelector('#result-stats').textContent.trim()
          : null;
        var totalMatch = statsText ? statsText.match(/[\d,]+/) : null;
        var resultsTotal = totalMatch ? totalMatch[0].replace(/,/g, '') : null;

        var organicResults = Array.from(document.querySelectorAll('.tF2Cxc')).map(function(el, i) {
          var desc = el.querySelector('.VwiC3b');
          var emphasized = [];
          if (desc) {
            Array.from(desc.querySelectorAll('b, em')).forEach(function(b) {
              var t = b.textContent.trim();
              if (t.length > 0) emphasized.push(t);
            });
          }
          return {
            position: i + 1,
            type: 'organic',
            title: el.querySelector('h3')
              ? el.querySelector('h3').textContent.trim()
              : null,
            url: el.querySelector('a[href]')
              ? el.querySelector('a[href]').href
              : null,
            displayedUrl: el.querySelector('cite')
              ? el.querySelector('cite').textContent.trim()
              : null,
            description: desc ? desc.textContent.trim() : null,
            emphasizedKeywords: emphasized,
            siteLinks: Array.from(el.querySelectorAll('.HiHjCd a, .fl a')).map(function(a) {
              return {title: a.textContent.trim(), url: a.href};
            })
          };
        });

        var paidResults = Array.from(
          document.querySelectorAll('#tads .uEierd')
        ).map(function(el, i) {
          var urlEl = el.querySelector('a[data-rw]') || el.querySelector('a.sVXRqc');
          var titleEl = el.querySelector('.CCgQ5') || el.querySelector('.Va3FIb');
          var descEl = el.querySelector('.MUxGbd .yDYNvb') || el.querySelector('.yDYNvb');
          return {
            adPosition: i + 1,
            type: 'paid',
            title: titleEl ? titleEl.textContent.trim() : null,
            url: urlEl ? urlEl.href : null,
            displayedUrl: el.querySelector('.x2VHCd')
              ? el.querySelector('.x2VHCd').textContent.trim()
              : null,
            description: descEl ? descEl.textContent.trim() : null,
            siteLinks: Array.from(el.querySelectorAll('.fl a')).map(function(a) {
              return {title: a.textContent.trim(), url: a.href};
            })
          };
        });

        var relatedQueries = Array.from(document.querySelectorAll('#bres a')).map(function(a) {
          return {title: a.textContent.trim(), url: a.href};
        }).filter(function(r) { return r.title.length > 0; });

        var searchTerm = urlParams.get('q') || '';
        var peopleAlsoAsk = Array.from(document.querySelectorAll('[data-q]')).map(function(el) {
          return {question: el.getAttribute('data-q')};
        }).filter(function(q) { return q.question && q.question !== searchTerm; });

        // AI Overview: only extract when heading is present and content is not an error state
        var aiOverview = null;
        var aiHeadingPresent = Array.from(
          document.querySelectorAll('[role=heading], .Fzsovc')
        ).some(function(el) {
          return el.textContent.trim() === 'AI Overview';
        });
        if (aiHeadingPresent) {
          var notAvailable = Array.from(document.querySelectorAll('span')).some(function(el) {
            return el.textContent.trim().includes('AI Overview is not available');
          });
          if (!notAvailable) {
            var aiTexts = Array.from(document.querySelectorAll('.rIRoqf.hXY9cf')).map(function(el) {
              return el.textContent.trim();
            }).filter(function(t) { return t.length > 30; });
            if (aiTexts.length > 0) aiOverview = aiTexts.join(' ');
          }
        }

        return JSON.stringify({
          searchQuery: searchQuery,
          resultsTotal: resultsTotal,
          organicResults: organicResults,
          paidResults: paidResults,
          relatedQueries: relatedQueries,
          peopleAlsoAsk: peopleAlsoAsk,
          aiOverview: aiOverview
        });
      } catch(e) {
        return JSON.stringify({error: true, message: e.message});
      }
    })()
    """
    print(js)

if __name__ == '__main__':
    main()
