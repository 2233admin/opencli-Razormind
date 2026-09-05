import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')

    js = r"""
    (async function() {
      try {
        const panel = document.querySelector(
          'ytd-engagement-panel-section-list-renderer'
        );
        if (!panel) {
          return JSON.stringify({
            error: true,
            message: 'Transcript panel not found. '
              + 'Call open-transcript-panel first and wait stable.',
          });
        }
        const initialSegments = panel.querySelectorAll(
          'transcript-segment-view-model'
        );
        if (initialSegments.length === 0) {
          return JSON.stringify({
            error: true,
            message: 'No transcript segments found. '
              + 'Panel may still be loading or transcripts are unavailable.',
          });
        }

        // Scroll panel to trigger lazy loading for long videos
        const scrollable = panel.querySelector('ytd-transcript-body-renderer') || panel;
        let prevCount = initialSegments.length;
        for (let i = 0; i < 60; i++) {
          scrollable.scrollTop = scrollable.scrollHeight;
          await new Promise(r => setTimeout(r, 150));
          const newCount = panel.querySelectorAll('transcript-segment-view-model').length;
          if (newCount === prevCount) break;
          prevCount = newCount;
        }

        const allSegments = Array.from(
          panel.querySelectorAll('transcript-segment-view-model')
        ).map(s => ({
          ts: s.querySelector('.ytwTranscriptSegmentViewModelTimestamp')?.textContent?.trim() || '',
          text: s.querySelector('span[class*="ytAttributedString"]')?.textContent?.trim() || ''
        })).filter(s => s.text);

        if (allSegments.length === 0) {
          return JSON.stringify({
            error: true,
            message: 'Transcript segments parsed but all text was empty.',
          });
        }

        const fullText = allSegments.map(s => s.text).join(' ');
        const timestampedText = allSegments.map(s => (s.ts ? s.ts + ' ' : '') + s.text).join('\n');

        return JSON.stringify({
          segment_count: allSegments.length,
          segments: allSegments,
          full_text: fullText,
          timestamped_text: timestampedText
        });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
