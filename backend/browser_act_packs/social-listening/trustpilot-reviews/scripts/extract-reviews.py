import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description='Extract reviews from the currently loaded Trustpilot review page.'
    )
    parser.add_argument(
        '--include-reviewer-metadata',
        action='store_true',
        help=(
            'Adds reviewerNumberOfReviews and reviewerProfileIsVerified '
            'to each review.'
        ),
    )
    parser.add_argument(
        '--include-reply-analysis',
        action='store_true',
        help=(
            'Adds companyReplyPublishedDate and companyReplyUpdatedDate '
            'when a reply exists.'
        ),
    )
    parser.add_argument(
        '--include-extended-metadata',
        action='store_true',
        help=(
            'Adds reviewSource, reviewVerificationSource, reviewLikes '
            'to each review.'
        ),
    )
    parser.add_argument(
        '--include-review-photos',
        action='store_true',
        help=(
            'Adds reviewPhotoUrls array (best-effort DOM scan inside the '
            'review card).'
        ),
    )
    parser.add_argument(
        '--no-experience-date',
        action='store_true',
        help='Omit reviewDateOfExperience from the output (kept by default).',
    )
    parser.add_argument(
        '--filter-country',
        default='',
        help=(
            'Client-side filter: keep only reviews whose reviewer countryCode '
            'matches the given ISO Alpha-2 code (e.g. US, GB, DK).'
        ),
    )
    args = parser.parse_args()

    inc_reviewer = 'true' if args.include_reviewer_metadata else 'false'
    inc_reply = 'true' if args.include_reply_analysis else 'false'
    inc_ext = 'true' if args.include_extended_metadata else 'false'
    inc_photos = 'true' if args.include_review_photos else 'false'
    inc_exp = 'false' if args.no_experience_date else 'true'
    filter_country = (args.filter_country or '').upper()

    js = f"""
    (function() {{
      try {{
        const s = document.querySelector('script#__NEXT_DATA__');
        if (!s) return JSON.stringify({{ error: true, message: 'NEXT_DATA script not found' }});
        const data = JSON.parse(s.textContent);
        const pp = data && data.props && data.props.pageProps;
        if (!pp) return JSON.stringify({{ error: true, message: 'pageProps missing' }});

        function extractDomainFromPath() {{
          const path = location.pathname || '';
          const prefix = '/review/';
          if (path.indexOf(prefix) === 0) {{
            return decodeURIComponent(path.substring(prefix.length).split('/')[0].split('?')[0]);
          }}
          return null;
        }}

        if (data.page === '/_error' || pp.statusCode === 404) {{
          return JSON.stringify({{
            isCompanyFound: false,
            companyPageUrl: location.href,
            searchedDomain: extractDomainFromPath(),
            scrapedDateTime: new Date().toISOString(),
            reviews: [],
            pagination: null
          }});
        }}

        const bu = pp.businessUnit;
        if (!bu) return JSON.stringify({{ error: true, message:
          'businessUnit missing from pageProps' }});

        const reviews = Array.isArray(pp.reviews) ? pp.reviews : [];
        const filters = pp.filters || {{}};
        const pagination = filters.pagination || {{}};
        const selected = filters.selected || {{}};
        const filterCountry = "{filter_country}";
        const scrapedAt = new Date().toISOString();
        const pageNum = pagination.currentPage || 1;
        const companyUrl = location.href;

        const mapped = [];
        for (let i = 0; i < reviews.length; i++) {{
          const r = reviews[i];
          const consumer = r.consumer || {{}};
          if (
            filterCountry &&
            (consumer.countryCode || '').toUpperCase() !== filterCountry
          ) continue;

          const labels = r.labels || {{}};
          const ver = labels.verification || {{}};
          const reply = r.reply || null;
          const dates = r.dates || {{}};

          const obj = {{
            companyName: bu.displayName || null,
            companyPageUrl: companyUrl,
            businessUnitId: bu.id || null,
            reviewId: r.id || null,
            reviewUrl: r.id ? ('https://www.trustpilot.com/reviews/' + r.id) : null,
            reviewTitle: r.title || null,
            reviewDescription: r.text || null,
            reviewRatingScore: r.rating != null ? r.rating : null,
            reviewDate: dates.publishedDate || null,
            reviewLabel: ver.verificationLevel || (ver.isVerified ? 'verified' : null),
            isReviewVerified: !!ver.isVerified,
            reviewer: consumer.displayName || null,
            reviewerId: consumer.id || null,
            reviewersCountry: consumer.countryCode || null,
            reviewLanguage: r.language || null,
            reviewCompanyResponse: reply ? (reply.message || '') : '',
            scrapedDateTime: scrapedAt,
            scrapedAtReviewPageNumber: pageNum
          }};

          if ({inc_exp}) {{
            obj.reviewDateOfExperience = dates.experiencedDate || null;
          }}
          if ({inc_reviewer}) {{
            obj.reviewerNumberOfReviews =
              consumer.numberOfReviews != null ? consumer.numberOfReviews : null;
            obj.reviewerProfileIsVerified = !!consumer.isVerified;
          }}
          if ({inc_reply}) {{
            obj.companyReplyPublishedDate = reply ? (reply.publishedDate || null) : null;
            obj.companyReplyUpdatedDate = reply ? (reply.updatedDate || null) : null;
          }}
          if ({inc_ext}) {{
            obj.reviewSource = r.source || null;
            obj.reviewVerificationSource = ver.verificationSource || null;
            obj.reviewLikes = r.likes != null ? r.likes : 0;
          }}
          if ({inc_photos}) {{
            const urls = [];
            try {{
              const candidates = document.querySelectorAll(
                '[data-review-id="' + r.id + '"], article[id*="' + r.id + '"]'
              );
              candidates.forEach(function(card) {{
                card.querySelectorAll('img').forEach(function(img) {{
                  const src = img.getAttribute('src') || '';
                  if (
                    src &&
                    (src.indexOf('consumer-image-uploads') !== -1 ||
                      src.indexOf('review-photos') !== -1 ||
                      src.indexOf('reviewphotos') !== -1)
                  ) {{
                    if (urls.indexOf(src) === -1) urls.push(src);
                  }}
                }});
              }});
            }} catch(e) {{}}
            obj.reviewPhotoUrls = urls;
          }}

          mapped.push(obj);
        }}

        return JSON.stringify({{
          reviews: mapped,
          pagination: {{
            currentPage: pagination.currentPage || 1,
            perPage: pagination.perPage || 20,
            totalPages: pagination.totalPages || 0,
            totalCount: pagination.totalCount || 0
          }},
          activeFilters: {{
            languages: selected.languages || null,
            sort: selected.sort || null,
            stars: selected.stars || null,
            verified: !!selected.verified,
            replies: !!selected.replies,
            date: selected.date || null,
            search: selected.search || null,
            countryFilter: filterCountry || null
          }},
          companyContext: {{
            businessUnitId: bu.id || null,
            companyName: bu.displayName || null,
            identifyingName: bu.identifyingName || null,
            trustScore: bu.trustScore != null ? bu.trustScore : null,
            stars: bu.stars != null ? bu.stars : null,
            totalReviews: bu.numberOfReviews != null ? bu.numberOfReviews : null
          }},
          totalFiltered:
            filters.totalNumberOfFilteredReviews != null
              ? filters.totalNumberOfFilteredReviews : null,
          scrapedDateTime: scrapedAt,
          isCompanyFound: true
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: 'extract failed: ' +
          (e && e.message ? e.message : String(e)) }});
      }}
    }})()
    """
    print(js)


if __name__ == '__main__':
    main()
