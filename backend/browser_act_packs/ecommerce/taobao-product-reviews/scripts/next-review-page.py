import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')

    js = """
    (function() {
      try {
        // Look for the next page button in the reviews/comments section
        var allBtns = Array.from(document.querySelectorAll(
          'button, a, div[class*="next"], div[class*="page"]'
        ));
        var nextBtn = allBtns.find(function(el) {
          var text = el.textContent.trim();
          return text === '下一页' || text === '>' || text === '›';
        });
        if (!nextBtn) {
          return JSON.stringify({ hasNext: false });
        }
        var isDisabled = nextBtn.disabled ||
          nextBtn.getAttribute('aria-disabled') === 'true' ||
          nextBtn.className.indexOf('disabled') >= 0;
        return JSON.stringify({
          hasNext: !isDisabled,
          buttonText: nextBtn.textContent.trim()
        });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)

if __name__ == '__main__':
    main()
