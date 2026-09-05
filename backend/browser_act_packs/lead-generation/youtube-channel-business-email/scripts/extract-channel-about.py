import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description=(
            "Emit JS that extracts the channel About metadata (description, links, "
            "counters, channel id) from the current /about page's ytInitialData and "
            "pulls any business email already present in the description text."
        )
    )
    args = parser.parse_args()
    _ = args

    js = r"""
(function() {
  try {
    if (!window.ytInitialData) {
      return JSON.stringify({error: true, message: "ytInitialData not present on page; "
        + "navigate to a channel /about URL first"});
    }
    var findAbout = function(obj, depth) {
      if (depth > 25 || !obj || typeof obj !== "object") return null;
      if (obj.aboutChannelViewModel) return obj.aboutChannelViewModel;
      for (var k in obj) {
        if (!Object.prototype.hasOwnProperty.call(obj, k)) continue;
        var r = findAbout(obj[k], depth + 1);
        if (r) return r;
      }
      return null;
    };
    var m = findAbout(window.ytInitialData, 0);
    if (!m) {
      return JSON.stringify({error: true, message: "aboutChannelViewModel not found in "
        + "ytInitialData; the page may not be a channel /about view"});
    }
    var channelTitle = null;
    try {
      var meta = window.ytInitialData.metadata
        && window.ytInitialData.metadata.channelMetadataRenderer;
      if (meta) channelTitle = meta.title || null;
    } catch (e) {}
    if (!channelTitle) {
      try {
        var header = JSON.stringify(window.ytInitialData.header || {});
        var titleMatch = header.match(/"title"\s*:\s*"([^"]{1,120})"/);
        if (titleMatch) channelTitle = titleMatch[1];
      } catch (e) {}
    }
    var unwrapRedirect = function(href) {
      if (!href) return null;
      try {
        var u = new URL(href, "https://www.youtube.com/");
        if (u.hostname.indexOf("youtube.com") !== -1 && u.pathname === "/redirect") {
          var q = u.searchParams.get("q");
          if (q) return q;
        }
        return href;
      } catch (e) {
        return href;
      }
    };
    var rawLinks = Array.isArray(m.links) ? m.links : [];
    var links = rawLinks.map(function(item) {
      var v = item && item.channelExternalLinkViewModel;
      if (!v) return null;
      var title = (v.title && v.title.content) || null;
      var displayUrl = (v.link && v.link.content) || null;
      var redirectHref = null;
      try {
        var cmd = v.link && v.link.commandRuns && v.link.commandRuns[0]
          && v.link.commandRuns[0].onTap && v.link.commandRuns[0].onTap.innertubeCommand;
        if (cmd) {
          if (cmd.urlEndpoint && cmd.urlEndpoint.url) redirectHref = cmd.urlEndpoint.url;
        }
      } catch (e) {}
      var unwrapped = unwrapRedirect(redirectHref);
      var finalUrl = unwrapped || (displayUrl
        ? (displayUrl.indexOf("http") === 0 ? displayUrl : "https://" + displayUrl)
        : null);
      return {title: title, display: displayUrl, url: finalUrl};
    }).filter(function(x) { return x !== null; });

    var description = m.description || "";
    var emailRe = /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/g;
    var rawMatches = description.match(emailRe) || [];
    var emails = [];
    var seen = {};
    for (var i = 0; i < rawMatches.length; i++) {
      var addr = rawMatches[i].replace(/[\.,;:]+$/, "");
      var key = addr.toLowerCase();
      if (!seen[key]) { seen[key] = true; emails.push(addr); }
    }

    var pickText = function(node) {
      if (!node) return null;
      if (typeof node === "string") return node;
      if (node.content) return node.content;
      return null;
    };

    var result = {
      channel_id: m.channelId || null,
      channel_name: channelTitle,
      canonical_channel_url: m.canonicalChannelUrl || null,
      description: description,
      country: m.country || null,
      subscriber_count_text: m.subscriberCountText || null,
      view_count_text: m.viewCountText || null,
      video_count_text: m.videoCountText || null,
      joined_date_text: pickText(m.joinedDateText),
      has_business_email_reveal_button: !!m.businessEmailRevealButton,
      bypass_business_email_captcha: !!m.bypassBusinessEmailCaptcha,
      emails_in_description: emails,
      links: links
    };
    return JSON.stringify(result);
  } catch (e) {
    return JSON.stringify({error: true, message: String(e && e.message ? e.message : e)});
  }
})()
"""
    print(js)


if __name__ == "__main__":
    main()
