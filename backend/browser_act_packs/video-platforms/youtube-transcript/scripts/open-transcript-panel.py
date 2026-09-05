import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')

    js = r"""
    (function() {
      try {
        const labels = ['内容转文字', 'Show transcript', 'Open transcript', 'Transcript'];
        let btn = null;
        for (const label of labels) {
          btn = document.querySelector('button[aria-label="' + label + '"]');
          if (btn) break;
        }
        if (!btn) {
          const playerResponse = window.ytInitialPlayerResponse;
          const tracks =
            playerResponse?.captions?.playerCaptionsTracklistRenderer?.captionTracks;
          const hasTranscripts = tracks && tracks.length > 0;
          return JSON.stringify({
            error: true,
            message: hasTranscripts
              ? 'Transcript button not found on page. Try scrolling down to the video description.'
              : 'This video does not have transcripts available.'
          });
        }
        btn.click();
        return JSON.stringify({ success: true, label: btn.getAttribute('aria-label') });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
