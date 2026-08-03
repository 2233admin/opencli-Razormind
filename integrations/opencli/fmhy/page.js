import { cli, Strategy } from '@jackwener/opencli/registry';
import { fetchPage } from './utils.js';

cli({
    site: 'fmhy',
    name: 'page',
    access: 'read',
    description: 'Fetch one live FMHY sitemap page as structured text and resource records',
    domain: 'fmhy.net',
    strategy: Strategy.PUBLIC,
    browser: false,
    args: [
        { name: 'path', type: 'str', required: true, positional: true, help: 'FMHY path or full fmhy.net URL, for example ai or /other/selfhosting' },
    ],
    columns: ['page', 'page_title', 'section', 'kind', 'title', 'description', 'url', 'links'],
    func: async (kwargs) => (await fetchPage(kwargs.path)).records,
});
