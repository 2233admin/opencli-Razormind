import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')

    js = """
    (function() {
      try {
        const data = window.ytInitialPlayerResponse;
        if (!data) {
          return JSON.stringify({
            error: true,
            message: 'ytInitialPlayerResponse not found. '
              + 'Make sure a YouTube video page is open.'
          });
        }
        const tracks = data.captions?.playerCaptionsTracklistRenderer?.captionTracks;
        if (!tracks || tracks.length === 0) {
          return JSON.stringify({
            error: true,
            message: 'No caption tracks found. '
              + 'This video has transcripts disabled.'
          });
        }
        const langs = tracks.map(t => ({
          code: t.languageCode,
          name: t.name?.simpleText || t.languageCode,
          kind: t.kind || 'manual',
          is_auto: t.kind === 'asr'
        }));
        return JSON.stringify({ available_languages: langs, count: langs.length });
      } catch(e) {
        return JSON.stringify({ error: true, message: e.message });
      }
    })()
    """
    print(js)


if __name__ == '__main__':
    main()
