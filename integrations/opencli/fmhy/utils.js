const BASE_URL = 'https://fmhy.net';
const SITEMAP_URL = `${BASE_URL}/sitemap.xml`;
const ROBOTS_URL = `${BASE_URL}/robots.txt`;
const DEFAULT_TIMEOUT_MS = 20_000;
const USER_AGENT = 'OpenCLI-FMHY/1.0 (+https://github.com/2233admin/opencli-Razormind)';

let robotsPromise;

function decodeEntities(value = '') {
    const named = {
        amp: '&', apos: "'", gt: '>', lt: '<', nbsp: ' ', quot: '"',
    };
    return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (match, entity) => {
        if (entity[0] === '#') {
            const hex = entity[1]?.toLowerCase() === 'x';
            const codePoint = Number.parseInt(entity.slice(hex ? 2 : 1), hex ? 16 : 10);
            return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
        }
        return named[entity.toLowerCase()] ?? match;
    });
}

function stripTags(value = '') {
    return decodeEntities(
        value
            .replace(/<!--([\s\S]*?)-->/g, ' ')
            .replace(/<(script|style|noscript)\b[^>]*>[\s\S]*?<\/\1>/gi, ' ')
            .replace(/<br\s*\/?>/gi, '\n')
            .replace(/<[^>]+>/g, ' '),
    )
        .replace(/[\t\r ]+/g, ' ')
        .replace(/\s*\n\s*/g, '\n')
        .trim();
}

function extractAttribute(tag, name) {
    const match = tag.match(new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s>]+))`, 'i'));
    return decodeEntities(match?.[1] ?? match?.[2] ?? match?.[3] ?? '');
}

function metaContent(html, key, attribute = 'name') {
    const metaRegex = /<meta\b[^>]*>/gi;
    for (const tag of html.match(metaRegex) ?? []) {
        if (extractAttribute(tag, attribute).toLowerCase() === key.toLowerCase()) {
            return extractAttribute(tag, 'content');
        }
    }
    return '';
}

function canonicalUrl(html, fallback) {
    for (const tag of html.match(/<link\b[^>]*>/gi) ?? []) {
        if (extractAttribute(tag, 'rel').toLowerCase() === 'canonical') {
            return new URL(extractAttribute(tag, 'href'), fallback).href;
        }
    }
    return fallback;
}

function mainHtml(html) {
    return html.match(/<main\b[^>]*class=(?:"[^"]*\bmain\b[^"]*"|'[^']*\bmain\b[^']*')[^>]*>([\s\S]*?)<\/main>/i)?.[1]
        ?? html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/i)?.[1]
        ?? extractBalancedDiv(html, /<div\b[^>]*class=(?:"[^"]*\bVPHome\b[^"]*"|'[^']*\bVPHome\b[^']*')[^>]*>/i)
        ?? '';
}

function extractBalancedDiv(html, openingPattern) {
    const opening = openingPattern.exec(html);
    if (!opening) return '';
    const start = opening.index + opening[0].length;
    const tags = /<div\b[^>]*>|<\/div\s*>/gi;
    tags.lastIndex = start;
    let depth = 1;
    let tag;
    while ((tag = tags.exec(html))) {
        depth += /^<\/div/i.test(tag[0]) ? -1 : 1;
        if (depth === 0) return html.slice(start, tag.index);
    }
    return '';
}

function pageModuleUrl(html, pageUrl) {
    for (const tag of html.match(/<link\b[^>]*>/gi) ?? []) {
        if (extractAttribute(tag, 'rel').toLowerCase() !== 'modulepreload') continue;
        const href = extractAttribute(tag, 'href');
        if (/\.md\..+\.lean\.js(?:\?|$)/i.test(href)) return new URL(href, pageUrl).href;
    }
    return '';
}

function decodeJsString(value) {
    try {
        return JSON.parse(`"${value.replace(/"/g, '\\"')}"`);
    } catch {
        return value.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\//g, '/');
    }
}

export function parseModuleResources(moduleText, pageUrl, pageTitle) {
    const records = [];
    const seen = new Set();
    for (const objectMatch of moduleText.matchAll(/\{[^{}]{1,800}\}/g)) {
        const object = objectMatch[0];
        const nameMatch = object.match(/\bname:"((?:\\.|[^"\\])*)"/);
        const urlMatch = object.match(/\burl:"((?:\\.|[^"\\])*)"/);
        if (!nameMatch || !urlMatch) continue;
        const title = decodeJsString(nameMatch[1]);
        const rawUrl = decodeJsString(urlMatch[1]);
        if (!title.trim() || !rawUrl.trim()) continue;
        let url;
        try {
            url = new URL(rawUrl, pageUrl).href;
        } catch {
            continue;
        }
        if (seen.has(url)) continue;
        seen.add(url);
        const chordMatch = object.match(/\bchord:"((?:\\.|[^"\\])*)"/);
        records.push({
            page: new URL(pageUrl).pathname,
            page_title: pageTitle,
            section: `${pageTitle} > Bookmarks`,
            kind: 'resource',
            title,
            description: chordMatch ? `Shortcut: ${decodeJsString(chordMatch[1])}` : '',
            url,
            links: url,
        });
    }
    return records;
}

function anchorsFrom(block, pageUrl) {
    const anchors = [];
    const anchorRegex = /<a\b([^>]*)>([\s\S]*?)<\/a>/gi;
    let match;
    while ((match = anchorRegex.exec(block))) {
        const href = extractAttribute(`<a ${match[1]}>`, 'href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:')) continue;
        let url;
        try {
            url = new URL(href, pageUrl).href;
        } catch {
            continue;
        }
        const title = stripTags(match[2]);
        anchors.push({ title, url });
    }
    return anchors;
}

function descriptionWithoutTitle(text, title) {
    if (!title) return text;
    const escaped = title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return text
        .replace(new RegExp(`^\\s*${escaped}\\s*(?:[-–—:]\\s*)?`, 'i'), '')
        .trim();
}

export function normalizePageUrl(input) {
    const raw = String(input ?? '').trim();
    if (!raw || raw === '/') return `${BASE_URL}/`;
    const url = new URL(raw.startsWith('http://') || raw.startsWith('https://') ? raw : `/${raw.replace(/^\/+/, '')}`, BASE_URL);
    if (url.origin !== BASE_URL) throw new Error('FMHY adapters only accept fmhy.net page URLs.');
    url.hash = '';
    url.search = '';
    return url.href;
}

export function parseSitemap(xml) {
    const urls = [];
    const seen = new Set();
    const locRegex = /<loc>([\s\S]*?)<\/loc>/gi;
    let match;
    while ((match = locRegex.exec(xml))) {
        const url = normalizePageUrl(decodeEntities(match[1].trim()));
        if (!seen.has(url)) {
            seen.add(url);
            urls.push(url);
        }
    }
    return urls;
}

function robotsPattern(value) {
    const anchored = value.endsWith('$');
    const source = value
        .replace(/\$$/, '')
        .replace(/[.+?^${}()|[\]\\]/g, '\\$&')
        .replace(/\*/g, '.*');
    return new RegExp(`^${source}${anchored ? '$' : ''}`);
}

export function parseRobots(text) {
    const rules = [];
    let applies = false;
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.replace(/\s*#.*$/, '').trim();
        if (!line) continue;
        const separator = line.indexOf(':');
        if (separator < 0) continue;
        const field = line.slice(0, separator).trim().toLowerCase();
        const value = line.slice(separator + 1).trim();
        if (field === 'user-agent') {
            applies = value === '*';
        } else if (applies && (field === 'allow' || field === 'disallow') && value) {
            rules.push({ allow: field === 'allow', pattern: value, regex: robotsPattern(value) });
        }
    }
    return rules;
}

export function isRobotsAllowed(url, rules) {
    const path = `${url.pathname}${url.search}`;
    const matches = rules.filter(rule => rule.regex.test(path));
    if (!matches.length) return true;
    matches.sort((a, b) => b.pattern.length - a.pattern.length || Number(b.allow) - Number(a.allow));
    return matches[0].allow;
}

export async function fetchText(url, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
    const response = await fetch(url, {
        headers: {
            accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8',
            'user-agent': USER_AGENT,
        },
        redirect: 'follow',
        signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) throw new Error(`FMHY request failed (${response.status}) for ${url}`);
    return response.text();
}

async function getRobotsRules() {
    robotsPromise ??= fetchText(ROBOTS_URL).then(parseRobots);
    return robotsPromise;
}

export async function fetchSitemap() {
    return parseSitemap(await fetchText(SITEMAP_URL));
}

export async function fetchPage(input, { sitemap } = {}) {
    const url = new URL(normalizePageUrl(input));
    const knownUrls = sitemap ?? await fetchSitemap();
    if (!knownUrls.includes(url.href)) throw new Error(`FMHY page is not present in the live sitemap: ${url.pathname}`);
    const rules = await getRobotsRules();
    if (!isRobotsAllowed(url, rules)) throw new Error(`FMHY robots.txt disallows crawling: ${url.pathname}`);
    const html = await fetchText(url.href);
    const page = parsePage(html, url.href);
    if (page.records.length === 1 && page.records[0].kind === 'page') {
        const moduleUrl = pageModuleUrl(html, url.href);
        if (moduleUrl) {
            const moduleRecords = parseModuleResources(await fetchText(moduleUrl), page.url, page.title);
            if (moduleRecords.length) page.records = moduleRecords;
        }
    }
    return page;
}

export function parsePage(html, requestedUrl) {
    const url = canonicalUrl(html, requestedUrl);
    const title = metaContent(html, 'og:title', 'property')
        || stripTags(html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? '')
            .replace(/\s*[•|]\s*freemediaheckyeah\s*$/i, '');
    const pageDescription = metaContent(html, 'description');
    const content = mainHtml(html);
    const pagePath = new URL(url).pathname;
    const records = [];
    const headings = [];
    const blockRegex = /<h([1-6])\b[^>]*>([\s\S]*?)<\/h\1>|<(p|li)\b[^>]*>([\s\S]*?)<\/\3>/gi;
    let match;
    while ((match = blockRegex.exec(content))) {
        if (match[1]) {
            const level = Number(match[1]);
            const heading = stripTags(match[2]).replace(/​/g, '').trim();
            if (!heading) continue;
            headings[level - 1] = heading;
            headings.length = level;
            continue;
        }

        const kind = match[3].toLowerCase() === 'li' ? 'resource' : 'text';
        const block = match[4];
        const text = stripTags(block);
        if (!text || /^(Got feedback\?|Send us your suggestions)/i.test(text)) continue;
        const anchors = anchorsFrom(block, url);
        const primary = anchors.find(anchor => anchor.title) ?? anchors[0];
        records.push({
            page: pagePath,
            page_title: title,
            section: headings.filter(Boolean).join(' > '),
            kind,
            title: primary?.title ?? '',
            description: descriptionWithoutTitle(text, primary?.title ?? ''),
            url: primary?.url ?? '',
            links: anchors.map(anchor => anchor.url).join(' '),
        });
    }

    if (!records.length) {
        records.push({
            page: pagePath,
            page_title: title,
            section: '',
            kind: 'page',
            title,
            description: pageDescription,
            url,
            links: '',
        });
    }
    return { url, title, description: pageDescription, records };
}

export function sitemapRows(urls) {
    return urls.map(url => {
        const pathname = new URL(url).pathname;
        const parts = pathname.split('/').filter(Boolean);
        return {
            path: pathname,
            group: parts[0] ?? 'root',
            kind: parts[0] === 'posts' && parts.length > 1 ? 'post' : 'directory',
            url,
        };
    });
}

export async function mapLimit(values, concurrency, mapper) {
    const results = new Array(values.length);
    let next = 0;
    async function worker() {
        while (next < values.length) {
            const index = next++;
            results[index] = await mapper(values[index], index);
        }
    }
    const count = Math.max(1, Math.min(Number(concurrency) || 1, 6, values.length || 1));
    await Promise.all(Array.from({ length: count }, worker));
    return results;
}

export function selectPages(urls, { group, maxPages } = {}) {
    let selected = urls;
    if (group) {
        const normalized = String(group).replace(/^\/+|\/+$/g, '').toLowerCase();
        selected = selected.filter(url => new URL(url).pathname.split('/').filter(Boolean)[0]?.toLowerCase() === normalized);
    }
    const cap = Number(maxPages) || 0;
    return cap > 0 ? selected.slice(0, cap) : selected;
}

export async function crawlPages({ group, maxPages = 0, concurrency = 3, delayMs = 150 } = {}) {
    const sitemap = await fetchSitemap();
    const pages = selectPages(sitemap, { group, maxPages });
    const settled = await mapLimit(pages, concurrency, async (url) => {
        if (delayMs > 0) await new Promise(resolve => setTimeout(resolve, Math.min(Number(delayMs) || 0, 2_000)));
        try {
            return { ok: true, page: await fetchPage(url, { sitemap }) };
        } catch (error) {
            return { ok: false, url, error: error instanceof Error ? error.message : String(error) };
        }
    });
    const records = settled.flatMap(result => result.ok ? result.page.records : [{
        page: new URL(result.url).pathname,
        page_title: '',
        section: '',
        kind: 'error',
        title: '',
        description: result.error,
        url: result.url,
        links: '',
    }]);
    return { pages, records, failures: settled.filter(result => !result.ok) };
}

export const __test__ = {
    decodeEntities,
    extractBalancedDiv,
    mainHtml,
    pageModuleUrl,
    stripTags,
};
