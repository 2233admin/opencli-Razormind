import { cli, Strategy } from '@jackwener/opencli/registry';
import { fetchSitemap, sitemapRows } from './utils.js';

cli({
    site: 'fmhy',
    name: 'pages',
    access: 'read',
    description: 'List every crawlable FMHY page from the live sitemap',
    domain: 'fmhy.net',
    strategy: Strategy.PUBLIC,
    browser: false,
    args: [
        { name: 'group', type: 'str', required: false, help: 'Optional first path segment, such as posts or other' },
    ],
    columns: ['path', 'group', 'kind', 'url'],
    func: async (kwargs) => {
        const rows = sitemapRows(await fetchSitemap());
        const group = String(kwargs.group ?? '').trim().toLowerCase();
        return group ? rows.filter(row => row.group.toLowerCase() === group) : rows;
    },
});
