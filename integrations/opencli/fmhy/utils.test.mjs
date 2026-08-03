import assert from 'node:assert/strict';
import test from 'node:test';

import {
    isRobotsAllowed,
    normalizePageUrl,
    parsePage,
    parseModuleResources,
    parseRobots,
    parseSitemap,
    selectPages,
} from './utils.js';

test('parses and deduplicates FMHY sitemap URLs', () => {
    const urls = parseSitemap(`
        <urlset>
          <url><loc>https://fmhy.net/ai</loc></url>
          <url><loc>https://fmhy.net/posts/jan-2026</loc></url>
          <url><loc>https://fmhy.net/ai</loc></url>
        </urlset>
    `);
    assert.deepEqual(urls, ['https://fmhy.net/ai', 'https://fmhy.net/posts/jan-2026']);
    assert.deepEqual(selectPages(urls, { group: 'posts' }), ['https://fmhy.net/posts/jan-2026']);
});

test('rejects foreign origins', () => {
    assert.throws(() => normalizePageUrl('https://example.com/ai'), /only accept fmhy\.net/);
});

test('enforces allow and disallow robots rules', () => {
    const rules = parseRobots(`
        User-agent: *
        Disallow: /assets/
        Disallow: /*.png$
        Allow: /
    `);
    assert.equal(isRobotsAllowed(new URL('https://fmhy.net/ai'), rules), true);
    assert.equal(isRobotsAllowed(new URL('https://fmhy.net/assets/app.js'), rules), false);
    assert.equal(isRobotsAllowed(new URL('https://fmhy.net/logo.png'), rules), false);
});

test('extracts headings, prose, and resource links from VitePress main content', () => {
    const page = parsePage(`
      <html><head>
        <meta property="og:title" content="Artificial Intelligence">
        <meta name="description" content="AI tools">
        <link rel="canonical" href="https://fmhy.net/ai">
      </head><body>
        <main class="main"><div class="vp-doc _ai">
          <h1>Artificial Intelligence</h1>
          <h2>Chatbots <a href="#chatbots">​</a></h2>
          <p>Useful public services.</p>
          <ul><li><a href="https://example.com/chat">Example Chat</a> - Fast &amp; free</li></ul>
        </div></main>
      </body></html>
    `, 'https://fmhy.net/ai');

    assert.equal(page.title, 'Artificial Intelligence');
    assert.deepEqual(page.records, [
        {
            page: '/ai',
            page_title: 'Artificial Intelligence',
            section: 'Artificial Intelligence > Chatbots',
            kind: 'text',
            title: '',
            description: 'Useful public services.',
            url: '',
            links: '',
        },
        {
            page: '/ai',
            page_title: 'Artificial Intelligence',
            section: 'Artificial Intelligence > Chatbots',
            kind: 'resource',
            title: 'Example Chat',
            description: 'Fast & free',
            url: 'https://example.com/chat',
            links: 'https://example.com/chat',
        },
    ]);
});

test('extracts homepage content from the VPHome wrapper', () => {
    const page = parsePage(`
      <html><head><meta property="og:title" content="Welcome"></head><body>
        <div class="VPHome"><div><h1>FMHY</h1><p>Browse the directories.</p></div></div>
        <footer><p>Footer noise</p></footer>
      </body></html>
    `, 'https://fmhy.net/');
    assert.equal(page.records[0].description, 'Browse the directories.');
    assert.equal(page.records.some(record => record.description.includes('Footer noise')), false);
});

test('extracts static startpage bookmarks from the VitePress page module', () => {
    const records = parseModuleResources(
        'const a=[{name:"YouTube",chord:"YT",url:"https://youtube.com/",icon:"video"},{name:"Guide",url:"/beginners-guide"}]',
        'https://fmhy.net/startpage',
        'Startpage',
    );
    assert.deepEqual(records.map(({ title, url }) => ({ title, url })), [
        { title: 'YouTube', url: 'https://youtube.com/' },
        { title: 'Guide', url: 'https://fmhy.net/beginners-guide' },
    ]);
});
