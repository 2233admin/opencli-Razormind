import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('hashtag')  # hashtag name without #
    args = parser.parse_args()

    js = f"""(async () => {{
  try {{
    const endpoint = '/api/challenge/detail/'
      + '?challengeName={args.hashtag}&aid=1988&app_language=en'
      + '&app_name=tiktok_web&device_platform=web_pc';
    const r = await fetch(endpoint, {{credentials: 'include'}});
    const d = await r.json();
    const c = d.challengeInfo && d.challengeInfo.challenge;
    if (!c) return JSON.stringify({{error: true,
      message: 'challenge not found for: {args.hashtag}'}});
    return JSON.stringify({{
      id: c.id,
      title: c.title,
      videoCount: d.challengeInfo.statsV2 && d.challengeInfo.statsV2.videoCount,
      viewCount: d.challengeInfo.statsV2 && d.challengeInfo.statsV2.viewCount
    }});
  }} catch(e) {{
    return JSON.stringify({{error: true, message: e.message}});
  }}
}})()"""
    print(js)


if __name__ == '__main__':
    main()
