import { cli, Strategy } from '@jackwener/opencli/registry';
import { crawlPages } from './utils.js';

cli({
    site: 'fmhy',
    name: 'search',
    access: 'read',
    description: 'Search structured content across all live FMHY sitemap pages',
    domain: 'fmhy.net',
    strategy: Strategy.PUBLIC,
    browser: false,
    args: [
        { name: 'query', type: 'str', required: true, positional: true, help: 'Case-insensitive text to search for' },
        { name: 'group', type: 'str', required: false, help: 'Optional first path segment, such as posts or other' },
        { name: 'limit', type: 'int', required: false, default: 50, help: 'Maximum matching records, capped at 500' },
        { name: 'concurrency', type: 'int', required: false, default: 3, help: 'Concurrent requests, clamped to 1-6' },
        { name: 'delay-ms', type: 'int', required: false, default: 150, help: 'Polite delay before each page request, capped at 2000ms' },
    ],
    columns: ['page', 'page_title', 'section', 'kind', 'title', 'description', 'url', 'links'],
    func: async (kwargs) => {
        const query = String(kwargs.query ?? '').trim().toLowerCase();
        if (!query) throw new Error('FMHY search requires a non-empty query.');
        const limit = Math.max(1, Math.min(Number(kwargs.limit) || 50, 500));
        const result = await crawlPages({
            group: kwargs.group,
            concurrency: kwargs.concurrency,
            delayMs: kwargs['delay-ms'],
        });
        return result.records.filter(record => [
            record.page,
            record.page_title,
            record.section,
            record.title,
            record.description,
            record.url,
        ].some(value => String(value ?? '').toLowerCase().includes(query))).slice(0, limit);
    },
});
