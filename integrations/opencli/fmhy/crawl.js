import { cli, Strategy } from '@jackwener/opencli/registry';
import { crawlPages } from './utils.js';

cli({
    site: 'fmhy',
    name: 'crawl',
    access: 'read',
    description: 'Crawl every live FMHY sitemap page with robots enforcement and bounded concurrency',
    domain: 'fmhy.net',
    strategy: Strategy.PUBLIC,
    browser: false,
    args: [
        { name: 'group', type: 'str', required: false, help: 'Optional first path segment, such as posts or other' },
        { name: 'max-pages', type: 'int', required: false, default: 0, help: 'Maximum pages to crawl; 0 means every matching sitemap page' },
        { name: 'concurrency', type: 'int', required: false, default: 3, help: 'Concurrent requests, clamped to 1-6' },
        { name: 'delay-ms', type: 'int', required: false, default: 150, help: 'Polite delay before each page request, capped at 2000ms' },
    ],
    columns: ['page', 'page_title', 'section', 'kind', 'title', 'description', 'url', 'links'],
    func: async (kwargs) => {
        const result = await crawlPages({
            group: kwargs.group,
            maxPages: kwargs['max-pages'],
            concurrency: kwargs.concurrency,
            delayMs: kwargs['delay-ms'],
        });
        return result.records;
    },
});
