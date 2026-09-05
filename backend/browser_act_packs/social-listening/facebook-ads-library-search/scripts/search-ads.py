import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description='Generate JS to search Meta Ad Library')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--query', default=None)           # keyword to search
    group.add_argument('--page-id', default=None)         # Facebook page ID to get all ads from
    parser.add_argument('--country', default='ALL')        # 2-letter ISO code or ALL
    parser.add_argument('--active-status', default='active',
                        choices=['active', 'inactive', 'all'])
    parser.add_argument('--ad-type', default='ALL',
                        choices=['ALL', 'POLITICAL_AND_ISSUE_ADS', 'HOUSING_ADS'])
    parser.add_argument('--media-type', default='all',
                        choices=['all', 'image', 'video', 'meme'])
    parser.add_argument('--platforms', nargs='*', default=[],
                        help='publisher platforms filter, e.g. facebook instagram')
    parser.add_argument('--cursor', default=None)          # pagination cursor
    parser.add_argument('--first', type=int, default=10)   # number of results per page
    args = parser.parse_args()

    page_ids = [args.page_id] if args.page_id else []
    search_type = 'page' if args.page_id else 'keyword_unordered'
    countries = ['ALL'] if args.country.upper() == 'ALL' else [args.country.upper()]

    variables = {
        "activeStatus": args.active_status,
        "adType": args.ad_type,
        "bylines": [],
        "collationToken": None,
        "contentLanguages": [],
        "countries": countries,
        "cursor": args.cursor,
        "excludedIDs": None,
        "first": args.first,
        "isTargetedCountry": False,
        "location": None,
        "mediaType": args.media_type,
        "multiCountryFilterMode": None,
        "pageIDs": page_ids,
        "potentialReachInput": None,
        "publisherPlatforms": args.platforms,
        "queryString": args.query,
        "regions": None,
        "searchType": search_type,
        "sessionID": "skill-session",
        "sortData": None,
        "source": None,
        "startDate": None,
        "v": "1368af"
    }

    vars_json = json.dumps(variables, ensure_ascii=True)

    js = f"""(async function() {{
  var scripts = document.getElementsByTagName('script');
  var lsd = null;
  for (var i = 0; i < scripts.length; i++) {{
    var t = scripts[i].text;
    var idx = t.indexOf('"token":"');
    if (idx >= 0) {{
      var s = t.slice(idx + 9);
      var end = s.indexOf('"');
      if (end > 10) {{ lsd = s.slice(0, end); break; }}
    }}
  }}
  if (!lsd) return JSON.stringify({{error: true, message: "lsd token not found - " +
    "navigate to facebook.com first"}});

  try {{
    var vars = {vars_json};
    var body = "av=0&__user=0&__a=1&lsd=" + encodeURIComponent(lsd) +
      "&fb_api_req_friendly_name=AdLibrarySearchPaginationQuery" +
      "&variables=" + encodeURIComponent(JSON.stringify(vars)) +
      "&doc_id=27201872659451053";
    var r = await fetch("https://www.facebook.com/api/graphql/", {{
      method: "POST",
      headers: {{
        "Content-Type": "application/x-www-form-urlencoded",
        "X-FB-LSD": lsd
      }},
      body: body
    }});
    var d = JSON.parse(await r.text());
    var conn = d.data && d.data.ad_library_main && d.data.ad_library_main.search_results_connection;
    if (!conn) {{
      var hint = d.errors ? d.errors[0].message : JSON.stringify(d).slice(0, 200);
      return JSON.stringify({{error: true, message: "unexpected response: " + hint}});
    }}
    var ads = [];
    conn.edges.forEach(function(e) {{
      (e.node.collated_results || []).forEach(function(ad) {{
        var snap = ad.snapshot || {{}};
        ads.push({{
          ad_archive_id: ad.ad_archive_id,
          ad_id: ad.ad_id,
          page_id: ad.page_id,
          page_name: ad.page_name || snap.page_name,
          page_profile_uri: snap.page_profile_uri,
          page_profile_picture_url: snap.page_profile_picture_url,
          is_active: ad.is_active,
          start_date: ad.start_date,
          end_date: ad.end_date,
          publisher_platform: ad.publisher_platform,
          currency: ad.currency,
          spend: ad.spend,
          impressions_with_index: ad.impressions_with_index,
          reach_estimate: ad.reach_estimate,
          categories: ad.categories,
          contains_sensitive_content: ad.contains_sensitive_content,
          body: snap.body,
          caption: snap.caption,
          title: snap.title,
          cta_text: snap.cta_text,
          cta_type: snap.cta_type,
          link_url: snap.link_url,
          display_format: snap.display_format,
          cards: snap.cards,
          images: snap.images,
          videos: snap.videos
        }});
      }});
    }});
    return JSON.stringify({{
      error: false,
      count: ads.length,
      has_next_page: conn.page_info.has_next_page,
      end_cursor: conn.page_info.end_cursor,
      ads: ads
    }});
  }} catch(e) {{
    return JSON.stringify({{error: true, message: e.message}});
  }}
}})()"""

    sys.stdout.buffer.write(js.encode('utf-8'))


if __name__ == '__main__':
    main()
