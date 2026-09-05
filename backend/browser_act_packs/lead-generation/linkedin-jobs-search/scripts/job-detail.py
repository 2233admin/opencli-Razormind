import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('job_id')    # LinkedIn job posting ID (numeric)
    args = parser.parse_args()

    js = f"""
    (async function() {{
      try {{
        const csrfToken = document.cookie.match(/JSESSIONID="([^"]+)"/)?.[1] || '';
        const headers = {{
          'csrf-token': csrfToken,
          'x-restli-protocol-version': '2.0.0',
          'accept': 'application/vnd.linkedin.normalized+json+2.1'
        }};
        const url = '/voyager/api/jobs/jobPostings/{args.job_id}?decorationId='
          + 'com.linkedin.voyager.deco.jobs.web.shared.WebFullJobPosting-65';
        const res = await fetch(url, {{headers}});
        if (!res.ok) return JSON.stringify({{error: true, message: `HTTP ${{res.status}}`}});
        const data = await res.json();
        const d = data.data;
        if (!d) return JSON.stringify({{error: true, message: 'No job data returned'}});
        const workplaceMap = {{
          'urn:li:fs_workplaceType:1': 'On-site',
          'urn:li:fs_workplaceType:2': 'Remote',
          'urn:li:fs_workplaceType:3': 'Hybrid'
        }};
        const workType = d.workplaceTypes?.map(t => workplaceMap[t] || t).join(', ') || null;
        const listedAtMs = d.listedAt;
        const listedAt = listedAtMs ? new Date(listedAtMs).toISOString() : null;
        const inc = data.included || [];
        const companyUrn = d.companyDetails?.company;
        const company = inc.find(e => e.entityUrn === companyUrn);
        const companyName = company?.name || null;
        const companyUrl = company?.universalName
          ? 'https://www.linkedin.com/company/' + company.universalName
          : null;
        const salary = d.salaryInsights?.insightExists
          ? {{ text: d.salaryInsights?.salaryText, url: d.salaryInsights?.salaryExplorerUrl }}
          : null;
        return JSON.stringify({{
          id: '{args.job_id}',
          title: d.title,
          company: companyName,
          companyUrl,
          location: d.formattedLocation || null,
          workType,
          contractType: d.formattedEmploymentStatus || null,
          experienceLevel: d.formattedExperienceLevel || null,
          listedAt,
          applicantCount: d.applies ?? null,
          description: d.description?.text || null,
          salary,
          jobUrl: 'https://www.linkedin.com/jobs/view/{args.job_id}'
        }});
      }} catch(e) {{
        return JSON.stringify({{error: true, message: e.message}});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
