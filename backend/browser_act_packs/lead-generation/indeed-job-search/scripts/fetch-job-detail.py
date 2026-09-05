import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('jobkey', help='Indeed job key (jk parameter)')
    args = parser.parse_args()

    jobkey = args.jobkey

    js = f"""(async function() {{
  try {{
    const jk = '{jobkey}';
    const url = '/viewjob?jk=' + jk + '&viewtype=embedded&spa=1';
    const resp = await fetch(url);
    if (!resp.ok) return JSON.stringify({{error: true, message: "HTTP " + resp.status}});
    const data = await resp.json();
    if (data.status !== 'success' || !data.body) {{
      return JSON.stringify({{
        error: true, message: "Unexpected response: " + (data.status || "unknown")
      }});
    }}
    const body = data.body;
    const info = body.jobInfoWrapperModel && body.jobInfoWrapperModel.jobInfoModel;
    const header = info && info.jobInfoHeaderModel;
    const salary = body.salaryInfoModel;
    const benefits = body.benefitsModel;
    const hiring = body.hiringInsightsModel;
    const metaHeader = info && info.jobMetadataHeaderModel;
    const description = info ? info.sanitizedJobDescription : null;
    const result = {{
      id: jk,
      positionName: header ? header.jobTitle : null,
      company: header ? header.companyName : null,
      location: header ? header.formattedLocation : null,
      rating: header && header.companyReviewModel && header.companyReviewModel.ratingsModel
        ? header.companyReviewModel.ratingsModel.rating : null,
      reviewsCount: header && header.companyReviewModel && header.companyReviewModel.ratingsModel
        ? header.companyReviewModel.ratingsModel.count : null,
      companyLogo: header && header.companyImagesModel ? header.companyImagesModel.logoUrl : null,
      salary: salary ? salary.salaryText : null,
      salaryMin: salary ? salary.salaryMin : null,
      salaryMax: salary ? salary.salaryMax : null,
      salaryCurrency: salary ? salary.salaryCurrency : null,
      salaryType: salary ? salary.salaryType : null,
      jobType: metaHeader ? metaHeader.jobType : null,
      description: description || null,
      benefits: benefits && benefits.benefits
        ? benefits.benefits.map(function(b) {{ return b.label; }}) : null,
      postedAt: hiring ? hiring.age : null,
      isExpired: body.isJobExpired || false,
      url: "https://www.indeed.com/viewjob?jk=" + jk
    }};
    return JSON.stringify(result);
  }} catch(e) {{
    return JSON.stringify({{error: true, message: e.message}});
  }}
}})()"""
    print(js)


if __name__ == '__main__':
    main()
