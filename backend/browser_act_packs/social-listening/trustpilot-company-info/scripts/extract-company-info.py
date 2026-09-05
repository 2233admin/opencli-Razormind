import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description=(
            'Extract Trustpilot company profile data from the SSR data '
            'embedded in the review page.'
        )
    )
    parser.add_argument(
        '--include-response-metrics',
        action='store_true',
        help=(
            'Include reply behavior metrics (avg days to reply, reply '
            'percentage, AI response flag).'
        ),
    )
    args = parser.parse_args()

    include_metrics = 'true' if args.include_response_metrics else 'false'

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
            scrapedDateTime: new Date().toISOString()
          }});
        }}

        const bu = pp.businessUnit;
        if (!bu) return JSON.stringify({{ error: true, message:
          'businessUnit missing from pageProps' }});

        const verification = bu.verification || {{}};
        const isVerified = !!(
          verification.verifiedByGoogle ||
          verification.verifiedPaymentMethod ||
          verification.verifiedUserIdentity
        );
        const categories = bu.categories || [];
        const primaryCategory = categories.find(function(c) {{
          return c.isPrimary;
        }}) || categories[0] || null;
        const contact = bu.contactInfo || {{}};

        const result = {{
          isCompanyFound: true,
          company: bu.displayName || null,
          businessUnitId: bu.id || null,
          identifyingName: bu.identifyingName || null,
          rating: bu.trustScore != null ? String(bu.trustScore) : null,
          trustScoreNumeric: bu.trustScore != null ? bu.trustScore : null,
          stars: bu.stars != null ? bu.stars : null,
          OfficialTotalReviewCount: bu.numberOfReviews != null ? bu.numberOfReviews : null,
          numberOfReviewsLast12Months: bu.numberOfReviewsLast12Months != null
            ? bu.numberOfReviewsLast12Months : null,
          isCompanyVerified: isVerified ? 'yes' : 'no',
          verificationFlags: {{
            verifiedByGoogle: !!verification.verifiedByGoogle,
            verifiedPaymentMethod: !!verification.verifiedPaymentMethod,
            verifiedUserIdentity: !!verification.verifiedUserIdentity
          }},
          isClaimed: !!bu.isClaimed,
          isClosed: !!bu.isClosed,
          isTemporarilyClosed: !!bu.isTemporarilyClosed,
          isCollectingReviews: !!bu.isCollectingReviews,
          category: primaryCategory ? primaryCategory.name : null,
          categoryId: primaryCategory ? primaryCategory.id : null,
          allCategories: categories.map(function(c) {{
            return {{ id: c.id, name: c.name, isPrimary: !!c.isPrimary }};
          }}),
          websiteUrl: bu.websiteUrl || null,
          websiteTitle: bu.websiteTitle || null,
          profileImageUrl: bu.profileImageUrl || null,
          contactEmail: contact.email || null,
          contactPhone: contact.phone || null,
          contactCountry: contact.country || null,
          contactCity: contact.city || null,
          contactAddress: contact.address || null,
          contactZipCode: contact.zipCode || null,
          locationsCount: bu.locationsCount != null ? bu.locationsCount : null,
          companyPageUrl: location.href,
          scrapedDateTime: new Date().toISOString()
        }};

        if ({include_metrics}) {{
          const activity = bu.activity || {{}};
          const rb = activity.replyBehavior || {{}};
          result.replyAverageDaysToReply =
            rb.averageDaysToReply != null ? rb.averageDaysToReply : null;
          result.replyPercentage =
            rb.replyPercentage != null ? rb.replyPercentage : null;
          result.totalNegativeReviewsCount =
            rb.totalNegativeReviewsCount != null ? rb.totalNegativeReviewsCount : null;
          result.negativeReviewsWithRepliesCount =
            rb.negativeReviewsWithRepliesCount != null
              ? rb.negativeReviewsWithRepliesCount : null;
          result.lastReplyToNegativeReview =
            rb.lastReplyToNegativeReview != null ? rb.lastReplyToNegativeReview : null;
          result.companyUsesAIResponses = !!activity.isUsingAIResponses;
          result.claimedDate = activity.claimedDate || null;
          result.isAskingForReviews = !!activity.isAskingForReviews;
        }}

        return JSON.stringify(result);
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: 'extract failed: ' +
          (e && e.message ? e.message : String(e)) }});
      }}
    }})()
    """
    print(js)


if __name__ == '__main__':
    main()
