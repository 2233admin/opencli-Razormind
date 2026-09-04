import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-results", type=int, default=10)
    args = parser.parse_args()
    max_results = max(1, min(args.max_results, 50))

    js = r"""(() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const firstUrl = (value) => {
    if (typeof value === 'string' && value) return value;
    if (Array.isArray(value)) {
      for (const item of value) {
        const result = firstUrl(item);
        if (result) return result;
      }
    }
    if (value && typeof value === 'object') {
      for (const key of ['url', 'src', 'srcNoWatermark', 'playUrl']) {
        const result = firstUrl(value[key]);
        if (result) return result;
      }
    }
    return null;
  };
  const isoTime = (value) => {
    const timestamp = Number(value);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return null;
    const date = new Date(timestamp < 100000000000 ? timestamp * 1000 : timestamp);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  };
  const stateValues = Object.values(window.INIT_STATE || {});
  const state = stateValues.find((value) => value && Array.isArray(value.feeds)) || {feeds: []};
  const items = state.feeds.map((feed) => {
    if (!feed || typeof feed !== 'object') return null;
    const photo = feed.photo && typeof feed.photo === 'object' ? feed.photo : null;
    if (!photo || !clean(photo.id)) return null;
    const author = feed.author && typeof feed.author === 'object' ? feed.author : {};
    const comment = feed.comment && typeof feed.comment === 'object' ? feed.comment : {};
    const photoId = clean(photo.id);
    const caption = clean(photo.caption);
    const coverUrl = firstUrl(photo.coverUrl);
    const playUrl = firstUrl(photo.manifestH265) || firstUrl(photo.manifest);
    const statistics = {
      like_count: photo.likeCount,
      comment_count: comment.us_c,
      collect_count: photo.collectCount,
      view_count: photo.viewCount,
      share_count: photo.shareCount,
    };
    Object.keys(statistics).forEach((key) => {
      if (statistics[key] === null || statistics[key] === undefined) delete statistics[key];
    });
    return {
      title: caption || `Kuaishou video ${photoId}`,
      content: caption,
      author: clean(author.name),
      author_id: clean(author.id) || null,
      author_avatar: firstUrl(author.headerUrl),
      url: `https://www.kuaishou.com/short-video/${encodeURIComponent(photoId)}`,
      photo_id: photoId,
      create_time: photo.timestamp || null,
      published_at: isoTime(photo.timestamp),
      cover_url: coverUrl,
      play_url: playUrl,
      statistics,
      media: {
        type: 'video',
        play_url: playUrl,
        cover_url: coverUrl,
        duration_ms: photo.duration || null,
        width: photo.width || null,
        height: photo.height || null,
      },
      tags: Array.isArray(feed.tags)
        ? feed.tags.map((tag) => clean(tag && tag.name)).filter(Boolean)
        : [],
    };
  }).filter(Boolean).slice(0, MAX_RESULTS);
  return {count: items.length, items};
})()"""
    print(js.replace("MAX_RESULTS", str(max_results)))


if __name__ == "__main__":
    main()
