import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    js = r"""
    (function() {
      try {
        const statusEl = document.querySelector('.rufus-status-sr-only');
        if (!statusEl) {
          return JSON.stringify({
            error: true,
            message: 'Alexa status element not found - panel may be closed'
          });
        }
        const statusText = statusEl.textContent.trim();
        if (!statusText.includes('has completed generating')) {
          return JSON.stringify({
            error: true,
            message: 'Response not yet complete - call wait stable before extracting'
          });
        }
        const activeTurn = document.querySelector('.rufus-papyrus-active-turn');
        if (!activeTurn) {
          return JSON.stringify({
            error: true,
            message: 'No active conversation turn found'
          });
        }
        const questionEl = activeTurn.querySelector('.rufus-customer-text-wrap');
        const question = questionEl ? questionEl.textContent.trim() : '';
        // Answer is in direct children that are NOT the question (.rufus-html-turn-start)
        // and NOT the suggestion/feedback sections (.rufus-html-turn)
        const answerParts = Array.from(activeTurn.children)
          .filter(el =>
            !el.classList.contains('rufus-html-turn-start') &&
            !el.classList.contains('rufus-html-turn'))
          .map(el => el.textContent.trim())
          .filter(t => t.length > 0);
        if (answerParts.length === 0) {
          return JSON.stringify({
            error: true,
            message: 'Answer content not found in active turn'
          });
        }
        return JSON.stringify({
          question: question,
          response: answerParts.join('\n'),
          timestamp: new Date().toISOString()
        });
      } catch(e) {
        return JSON.stringify({error: true, message: e.message});
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
