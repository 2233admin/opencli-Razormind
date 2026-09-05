import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('keywords')  # job title / keywords
    parser.add_argument('location')  # location name, e.g. "United States"
    parser.add_argument('--start', default='0')  # pagination offset
    parser.add_argument('--count', default='25')  # results per page (max 100)
    # 1=On-site, 2=Remote, 3=Hybrid
    parser.add_argument('--work-type', default='')
    # F=Full-time, P=Part-time, C=Contract, T=Temporary, I=Internship, V=Volunteer
    parser.add_argument('--job-type', default='')
    # 1=Internship, 2=Entry, 3=Associate, 4=Mid-Senior, 5=Director
    parser.add_argument('--experience', default='')
    # r86400=24h, r604800=7d, r2592000=30d
    parser.add_argument('--time-posted', default='')
    parser.add_argument('--company-ids', default='')  # comma-separated LinkedIn company IDs
    args = parser.parse_args()

    filters = []
    if args.work_type:
        filters.append(f'workplaceType:List({args.work_type})')
    if args.job_type:
        filters.append(f'jobType:List({args.job_type})')
    if args.experience:
        filters.append(f'experience:List({args.experience})')
    if args.time_posted:
        filters.append(f'timePostedRange:List({args.time_posted})')
    if args.company_ids:
        ids = ','.join(args.company_ids.split(','))
        filters.append(f'company:List({ids})')

    selected_filters = ('selectedFilters:(' + ','.join(filters) + '),' if filters else '')

    js = f"""
    (async function() {{
      try {{
        const csrfToken = document.cookie.match(/JSESSIONID="([^"]+)"/)?.[1] || '';
        const headers = {{
          'csrf-token': csrfToken,
          'x-restli-protocol-version': '2.0.0',
          'accept': 'application/vnd.linkedin.normalized+json+2.1'
        }};
        const keywords = encodeURIComponent('{args.keywords}');
        const location = encodeURIComponent('{args.location}');
        const query = `(origin:JOB_SEARCH_PAGE_KEYWORD_HISTORY,keywords:${{keywords}},`
          + `locationUnion:(seoLocation:(location:${{location}})),`
          + `{selected_filters}spellCorrectionEnabled:true)`;
        const url = `/voyager/api/voyagerJobsDashJobCards?decorationId=`
          + `com.linkedin.voyager.dash.deco.jobs.search.JobSearchCardsCollectionLite-88`
          + `&count={args.count}&q=jobSearch&query=${{query}}`
          + `&servedEventEnabled=false&start={args.start}`;
        const res = await fetch(url, {{headers}});
        if (!res.ok) return JSON.stringify({{error: true, message: `HTTP ${{res.status}}`}});
        const data = await res.json();
        const cards = (data.included || []).filter(e =>
          e['$type'] === 'com.linkedin.voyager.dash.jobs.JobPostingCard' &&
          e.entityUrn?.includes('JOBS_SEARCH') &&
          e.jobPostingTitle
        );
        const jobs = cards.map(c => {{
          const id = c.jobPostingUrn?.split(':').pop();
          const locRaw = c.secondaryDescription?.text || '';
          const workTypeMatch = locRaw.match(/\\(([^)]+)\\)$/);
          return {{
            id,
            title: c.jobPostingTitle,
            company: c.primaryDescription?.text || null,
            location: locRaw.replace(/\\s*\\([^)]+\\)$/, '').trim() || null,
            workType: workTypeMatch ? workTypeMatch[1] : null,
            jobUrl: id ? 'https://www.linkedin.com/jobs/view/' + id : null,
            companyUrl: c.logo?.actionTarget ? c.logo.actionTarget.replace('/life', '') : null
          }};
        }});
        return JSON.stringify({{
          total: data.data?.paging?.total || 0,
          start: {args.start},
          count: jobs.length,
          jobs
        }});
      }} catch(e) {{
        return JSON.stringify({{error: true, message: e.message}});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
