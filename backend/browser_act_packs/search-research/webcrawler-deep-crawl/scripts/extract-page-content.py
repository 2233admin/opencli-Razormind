import argparse
import json
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser(
        description=(
            "Extract main content + metadata + outbound links from the currently loaded page"
        )
    )
    parser.add_argument(
        'start_url',
        help="Crawl scope: only outbound links under this URL prefix are kept",
    )
    parser.add_argument(
        '--remove-selectors',
        default='',
        help=(
            'Comma-separated CSS selectors to remove before extraction '
            '(e.g. ".cookie-banner,#chat")'
        ),
    )
    parser.add_argument(
        '--keep-selector',
        default='',
        help='CSS selector identifying the main content area; if set, only this is used',
    )
    parser.add_argument(
        '--output-format',
        default='markdown',
        choices=['markdown', 'text', 'html', 'all'],
        help='Body output format',
    )
    parser.add_argument(
        '--include-globs',
        default='[]',
        help='JSON array of glob patterns for outbound links',
    )
    parser.add_argument(
        '--exclude-globs',
        default='[]',
        help='JSON array of glob patterns to drop',
    )
    args = parser.parse_args()

    include_globs = json.loads(args.include_globs)
    exclude_globs = json.loads(args.exclude_globs)

    default_remove = [
        'nav', 'header', 'footer', 'aside',
        'script', 'style', 'noscript', 'iframe', 'svg',
        '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
        '.nav', '.navbar', '.navigation', '.menu', '.sidebar',
        '.header', '.footer', '.site-footer', '.site-header',
        '.cookie', '.cookies', '#cookie', '#cookies', '.cookie-banner', '.cookie-consent',
        '.advertisement', '.ads', '.ad', '[class*="advertisement"]',
        '.modal', '.popup', '.overlay',
        '.share', '.social', '.social-share',
        '.breadcrumb', '.breadcrumbs',
        '.toc', '.table-of-contents',
        '.search', '.search-box',
        '[aria-hidden="true"]',
    ]

    user_remove = [s.strip() for s in args.remove_selectors.split(',') if s.strip()]

    js = f"""
    (function() {{
      try {{
        const startUrl = {args.start_url!r};
        const keepSelector = {args.keep_selector!r};
        const outputFormat = {args.output_format!r};
        const defaultRemove = {json.dumps(default_remove)};
        const userRemove = {json.dumps(user_remove)};
        const includeGlobs = {json.dumps(include_globs)};
        const excludeGlobs = {json.dumps(exclude_globs)};

        // ---------- glob -> regex ----------
        function globToRe(g) {{
          let r = '';
          let i = 0;
          const meta = '.+^$()|[]{{}}\\\\';
          while (i < g.length) {{
            const c = g[i];
            if (c === '*' && g[i+1] === '*') {{ r += '.*'; i += 2; }}
            else if (c === '*') {{ r += '[^/]*'; i += 1; }}
            else if (c === '?') {{ r += '.'; i += 1; }}
            else if (meta.includes(c)) {{ r += '\\\\' + c; i += 1; }}
            else {{ r += c; i += 1; }}
          }}
          return new RegExp('^' + r + '$');
        }}
        const includeRes = includeGlobs.map(globToRe);
        const excludeRes = excludeGlobs.map(globToRe);

        // ---------- clone document so we don't mutate the live page ----------
        const docClone = document.cloneNode(true);

        // ---------- pick content root ----------
        let root;
        if (keepSelector) {{
          root = docClone.querySelector(keepSelector);
          if (!root) {{
            return JSON.stringify({{
              error: true,
              message: 'keep-selector matched nothing: ' + keepSelector,
              url: location.href
            }});
          }}
        }} else {{
          // Heuristic: prefer <main>, [role=main], <article>, then largest <div>
          root = docClone.querySelector('main')
              || docClone.querySelector('[role="main"]')
              || docClone.querySelector('article')
              || docClone.querySelector('#content')
              || docClone.querySelector('.content')
              || docClone.body;
        }}

        // ---------- strip boilerplate inside the chosen root ----------
        for (const sel of [...defaultRemove, ...userRemove]) {{
          try {{
            root.querySelectorAll(sel).forEach(n => n.remove());
          }} catch(e) {{ /* invalid selector, skip */ }}
        }}

        // ---------- gather metadata from the original document ----------
        function metaContent(name) {{
          const el = document.querySelector(
            'meta[name="' + name + '"], meta[property="' + name + '"]'
          )
                  || document.querySelector('meta[property="og:' + name + '"]');
          return el ? (el.getAttribute('content') || null) : null;
        }}
        const canonicalEl = document.querySelector('link[rel="canonical"]');
        const metadata = {{
          canonicalUrl: canonicalEl ? canonicalEl.href : location.href,
          title: (document.querySelector('title')?.textContent || '').trim() || null,
          description: metaContent('description') || metaContent('og:description'),
          author: metaContent('author') || metaContent('article:author'),
          keywords: (metaContent('keywords') || '').split(',').map(s => s.trim()).filter(Boolean),
          languageCode: document.documentElement.getAttribute('lang') || null,
          publishedAt: metaContent('article:published_time') || metaContent('date') || null,
          modifiedAt: metaContent('article:modified_time') || null,
          ogImage: metaContent('og:image'),
          ogType: metaContent('og:type')
        }};

        // ---------- collect outbound links from the ORIGINAL doc (before strip) ----------
        const scopeUrl = new URL(startUrl);
        const scopeBase = scopeUrl.origin + scopeUrl.pathname.replace(/\\/[^\\/]*$/, '/');
        const linkSeen = new Set();
        const outboundLinks = [];
        for (const a of document.querySelectorAll('a[href]')) {{
          let href;
          try {{ href = new URL(a.getAttribute('href'), document.baseURI).toString(); }}
          catch(e) {{ continue; }}
          href = href.split('#')[0];
          if (!href || linkSeen.has(href)) continue;
          if (!href.startsWith(scopeUrl.origin)) continue;
          if (!href.startsWith(scopeBase) && href !== scopeUrl.origin + scopeUrl.pathname) continue;
          const assetExtensions = (
            'png|jpg|jpeg|gif|svg|webp|ico|mp4|mp3|webm|woff2?|ttf|css|js|json|'
            + 'xml|zip|tar\\.gz|pdf|doc|docx|xls|xlsx'
          );
          const assetRe = new RegExp('\\\\.(' + assetExtensions + ')$', 'i');
          if (assetRe.test(href)) continue;
          if (excludeRes.some(re => re.test(href))) continue;
          if (includeRes.length > 0 && !includeRes.some(re => re.test(href))) continue;
          linkSeen.add(href);
          outboundLinks.push(href);
        }}

        // ---------- text extraction ----------
        function getText(node) {{
          return (node.textContent || '')
            .replace(/[ \\t]+/g, ' ')
            .replace(/\\n{{3,}}/g, '\\n\\n')
            .trim();
        }}

        // ---------- HTML to markdown (lightweight) ----------
        function htmlToMarkdown(node) {{
          let out = '';
          for (const child of node.childNodes) {{
            if (child.nodeType === Node.TEXT_NODE) {{
              out += child.textContent.replace(/\\s+/g, ' ');
              continue;
            }}
            if (child.nodeType !== Node.ELEMENT_NODE) continue;
            const tag = child.tagName.toLowerCase();
            switch (tag) {{
              case 'h1': case 'h2': case 'h3': case 'h4': case 'h5': case 'h6': {{
                const level = parseInt(tag.charAt(1), 10);
                out += '\\n\\n' + '#'.repeat(level) + ' ' + getText(child) + '\\n\\n';
                break;
              }}
              case 'p':
                out += '\\n\\n' + htmlToMarkdown(child).trim() + '\\n\\n';
                break;
              case 'br':
                out += '\\n';
                break;
              case 'hr':
                out += '\\n\\n---\\n\\n';
                break;
              case 'strong': case 'b':
                out += '**' + htmlToMarkdown(child).trim() + '**';
                break;
              case 'em': case 'i':
                out += '*' + htmlToMarkdown(child).trim() + '*';
                break;
              case 'code':
                if (child.parentElement && child.parentElement.tagName.toLowerCase() === 'pre') {{
                  out += child.textContent;
                }} else {{
                  out += '`' + child.textContent + '`';
                }}
                break;
              case 'pre': {{
                const codeText = child.textContent.replace(/\\n+$/, '');
                out += '\\n\\n```\\n' + codeText + '\\n```\\n\\n';
                break;
              }}
              case 'a': {{
                const href = child.getAttribute('href') || '';
                const text = htmlToMarkdown(child).trim();
                if (href) out += '[' + text + '](' + href + ')';
                else out += text;
                break;
              }}
              case 'img': {{
                const alt = child.getAttribute('alt') || '';
                const src = child.getAttribute('src') || '';
                if (src) out += '![' + alt + '](' + src + ')';
                break;
              }}
              case 'ul': case 'ol': {{
                out += '\\n\\n';
                const items = child.querySelectorAll(':scope > li');
                items.forEach((li, idx) => {{
                  const prefix = tag === 'ol' ? (idx + 1) + '. ' : '- ';
                  out += prefix + htmlToMarkdown(li).trim().replace(/\\n/g, '\\n  ') + '\\n';
                }});
                out += '\\n';
                break;
              }}
              case 'blockquote':
                out += (
                  '\n\n> ' + htmlToMarkdown(child).trim().replace(/\n/g, '\n> ') + '\n\n'
                );
                break;
              case 'table': {{
                const rows = [];
                child.querySelectorAll('tr').forEach(tr => {{
                  const cells = [];
                  tr.querySelectorAll('th,td').forEach(td => {{
                    cells.push(getText(td).replace(/\\|/g, '\\\\|'));
                  }});
                  rows.push('| ' + cells.join(' | ') + ' |');
                }});
                if (rows.length > 0) {{
                  out += '\\n\\n' + rows[0] + '\\n';
                  const cols = (rows[0].match(/\\|/g) || []).length - 1;
                  out += '|' + ' --- |'.repeat(cols) + '\\n';
                  for (let i = 1; i < rows.length; i++) out += rows[i] + '\\n';
                  out += '\\n';
                }}
                break;
              }}
              case 'div': case 'section': case 'span': case 'article': case 'main':
              case 'header': case 'footer': case 'figure': case 'figcaption':
              case 'details': case 'summary':
                out += htmlToMarkdown(child);
                break;
              default:
                out += htmlToMarkdown(child);
            }}
          }}
          return out;
        }}

        const rawText = getText(root);
        let markdown = null;
        let html = null;
        if (outputFormat === 'markdown' || outputFormat === 'all') {{
          markdown = htmlToMarkdown(root)
            .replace(/\\n{{3,}}/g, '\\n\\n')
            .replace(/[ \\t]+\\n/g, '\\n')
            .trim();
        }}
        if (outputFormat === 'html' || outputFormat === 'all') {{
          html = root.innerHTML;
        }}

        const result = {{
          error: false,
          url: location.href,
          crawl: {{
            loadedUrl: location.href,
            loadedTime: new Date().toISOString(),
            referrerUrl: document.referrer || null
          }},
          metadata: metadata,
          text: rawText,
          markdown: markdown,
          html: html,
          outboundLinks: outboundLinks
        }};
        // Drop nulls that the caller did not ask for to keep payload small
        if (outputFormat === 'text') {{ delete result.markdown; delete result.html; }}
        else if (outputFormat === 'markdown') {{ delete result.html; }}
        else if (outputFormat === 'html') {{ delete result.markdown; }}

        return JSON.stringify(result);
      }} catch(e) {{
        return JSON.stringify({{
          error: true,
          message: e.message,
          stack: e.stack,
          url: location.href
        }});
      }}
    }})()
    """
    print(js)


if __name__ == '__main__':
    main()
