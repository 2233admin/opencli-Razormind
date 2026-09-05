import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')

    js = r"""(function() {
  try {
    var el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
    if (!el) return JSON.stringify({
      error: true,
      message: '__UNIVERSAL_DATA_FOR_REHYDRATION__ not found — '
        + 'ensure you are on a TikTok video page'
    });
    var d = JSON.parse(el.textContent);
    var scope = d['__DEFAULT_SCOPE__'];
    if (!scope) return JSON.stringify({error: true, message: '__DEFAULT_SCOPE__ not found'});
    var vd = scope['webapp.video-detail'];
    if (!vd || !vd.itemInfo) return JSON.stringify({
      error: true,
      message: 'webapp.video-detail or itemInfo not found'
    });
    var item = vd.itemInfo.itemStruct;
    if (!item) return JSON.stringify({error: true, message: 'itemStruct not found'});
    var author = item.author || {};
    var authorStats = item.authorStats || {};
    var statsV2 = item.statsV2 || {};
    var stats = item.stats || {};
    var video = item.video || {};
    var music = item.music || {};
    var createTimeISO = item.createTime
      ? new Date(parseInt(item.createTime) * 1000).toISOString()
      : null;
    return JSON.stringify({
      id: item.id,
      text: item.desc,
      textLanguage: item.textLanguage,
      createTime: item.createTime,
      createTimeISO: createTimeISO,
      isAd: item.isAd || false,
      isPinned: item.isPinned || false,
      isSponsored: item.isSponsored || false,
      isSlideshow: !!(item.imagePost && item.imagePost.images && item.imagePost.images.length),
      locationCreated: item.locationCreated || null,
      webVideoUrl: 'https://www.tiktok.com/@' + author.uniqueId + '/video/' + item.id,
      mediaUrls: [],
      authorMeta: {
        id: author.id,
        name: author.uniqueId,
        nickName: author.nickname,
        profileUrl: 'https://www.tiktok.com/@' + author.uniqueId,
        verified: author.verified || false,
        signature: author.signature || '',
        bioLink: (author.bioLink && author.bioLink.link) || null,
        avatar: author.avatarMedium || author.avatarThumb || '',
        privateAccount: author.privateAccount || false,
        fans: parseInt(authorStats.followerCount || 0),
        following: parseInt(authorStats.followingCount || 0),
        heart: parseInt(authorStats.heart || 0),
        video: parseInt(authorStats.videoCount || 0),
        digg: parseInt(authorStats.diggCount || 0)
      },
      musicMeta: {
        musicId: music.id || '',
        musicName: music.title || '',
        musicAuthor: music.authorName || '',
        musicOriginal: music.original || false,
        coverMediumUrl: music.coverMedium || ''
      },
      videoMeta: {
        height: video.height || 0,
        width: video.width || 0,
        duration: video.duration || 0,
        coverUrl: video.cover || '',
        definition: video.definition || '',
        format: video.format || ''
      },
      diggCount: parseInt(statsV2.diggCount || stats.diggCount || 0),
      shareCount: parseInt(statsV2.shareCount || stats.shareCount || 0),
      playCount: parseInt(statsV2.playCount || stats.playCount || 0),
      collectCount: parseInt(statsV2.collectCount || stats.collectCount || 0),
      commentCount: parseInt(statsV2.commentCount || stats.commentCount || 0),
      hashtags: (item.textExtra || [])
        .filter(function(t) { return t.hashtagName; })
        .map(function(t) {
          return {id: t.hashtagId, name: t.hashtagName};
        }),
      mentions: (item.textExtra || [])
        .filter(function(t) { return t.userId; })
        .map(function(t) {
          return {id: t.userId, name: t.userUniqueId};
        }),
      effectStickers: (item.effectStickers || [])
        .map(function(s) { return {ID: s.ID, name: s.name}; }),
      slideshowImageLinks: (item.imagePost && item.imagePost.images || [])
        .map(function(img) {
          return {
            tiktokLink: img.imageURL && img.imageURL.urlList
              && img.imageURL.urlList[0] || ''
          };
        })
    });
  } catch(e) {
    return JSON.stringify({error: true, message: e.message});
  }
})()"""
    print(js)


if __name__ == '__main__':
    main()
