async function checkEmailReports(page, baseUrl = '') {
  const root = baseUrl || await page.evaluate(() => new URL('.', location.href).href);
  const preview = await page.evaluate(value => {
    const url = new URL(value);
    const local = url.protocol === 'file:' ||
      (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname));
    return {
      local,
      directory: url.protocol === 'file:'
        ? decodeURIComponent(url.pathname).replace(/^\/([A-Za-z]:)/, '$1') : ''
    };
  }, root);
  if (!preview.local) throw new Error('Use synthetic local email previews, never a live tenant.');
  const results = [];
  const failures = [];
  const signatures = new Map();
  const widths = [1440, 768, 640, 390, 320, 844];
  for (const language of ['ko', 'en', 'ja']) {
    for (const kind of ['single', 'digest']) {
      for (const inline of [false, true]) {
        const file = `${kind}-${language}${inline ? '-inline-only' : ''}.html`;
        for (const width of widths) {
          await page.setViewportSize({width, height: width === 844 ? 390 : 900});
          await page.goto(root + file);
          const report = await page.evaluate(() => {
            const text = selector => Array.from(document.querySelectorAll(selector),
              node => node.textContent.replace(/\s+/g, ' ').trim());
            const spills = [];
            const boundedText = '[class^="azb-badge-"], .azb-verify, .azb-wordmark, ' +
              '.azb-count-value, .azb-chapter-number, .azb-toc-number';
            for (const badge of document.querySelectorAll(boundedText)) {
              const cell = badge.closest('td');
              if (!cell || !badge.getClientRects().length) continue;
              const range = document.createRange();
              range.selectNodeContents(badge);
              const badgeBox = range.getBoundingClientRect();
              const cellBox = cell.getBoundingClientRect();
              if (badgeBox.right > cellBox.right + 1 || badgeBox.left < cellBox.left - 1) {
                spills.push({text: badge.textContent.trim(), excess: Math.ceil(badgeBox.right - cellBox.right)});
              }
            }
            const brokenAnchors = Array.from(document.querySelectorAll('a[href^="#"]'))
              .map(anchor => anchor.getAttribute('href'))
              .filter(href => href !== '#' && !document.getElementById(href.slice(1)));
            const firstTitle = document.querySelector('.azb-digest-title');
            const paper = document.querySelector('.azb-paper');
            const remoteImages = Array.from(document.querySelectorAll('img[src^="http"]'));
            const unsafeRemoteImages = remoteImages.filter(image => {
              const url = new URL(image.src);
              const trusted = ['learn.microsoft.com', 'azure.microsoft.com', 'www.microsoft.com',
                'techcommunity.microsoft.com', 'devblogs.microsoft.com'];
              return url.protocol !== 'https:' || !trusted.some(domain =>
                url.hostname === domain || url.hostname.endsWith(`.${domain}`));
            });
            const railErrors = [];
            const hasStyles = document.querySelectorAll('style').length > 0;
            for (const label of document.querySelectorAll('.azb-section-label')) {
              const copy = label.nextElementSibling;
              const labelBox = label.getBoundingClientRect();
              const copyBox = copy.getBoundingClientRect();
              const valid = hasStyles && innerWidth >= 800
                ? copyBox.left >= labelBox.right - 1 && Math.abs(copyBox.top - labelBox.top) <= 1
                : copyBox.top >= labelBox.bottom - 1;
              if (!valid) railErrors.push(label.textContent.trim());
            }
            const signature = JSON.stringify({
              headings: text('h1, h2, h3'),
              summaries: text('.azb-summary, .azb-digest-summary'),
              badges: text('[class^="azb-badge-"], .azb-verify'),
              commands: text('.azb-cli'),
              reasons: text('.azb-resource-reason'),
              links: Array.from(document.querySelectorAll('a'), anchor => anchor.getAttribute('href'))
            });
            return {
              actualWidth: innerWidth,
              overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
              spills, railErrors, brokenAnchors, signature,
              documentTitleCount: document.querySelectorAll('h1').length,
              paperCount: document.querySelectorAll('.azb-paper').length,
              visualCount: document.querySelectorAll('.azb-visual img').length,
              unsafeRemoteMedia: document.querySelectorAll('script, link').length + unsafeRemoteImages.length,
              imagesMissingAlt: remoteImages.filter(image => !image.alt.trim()).length,
              styleCount: document.querySelectorAll('style').length,
              contentsY: firstTitle ? Math.round(firstTitle.getBoundingClientRect().top) : null,
              titleWidth: firstTitle ? Math.round(firstTitle.getBoundingClientRect().width) : null,
              paperWidth: paper ? Math.round(paper.getBoundingClientRect().width) : null,
              height: document.documentElement.scrollHeight
            };
          });
          const caseName = `${file}@${width}`;
          const check = (condition, message) => { if (!condition) failures.push(`${caseName}: ${message}`); };
          check(report.actualWidth === width, 'Requested viewport was not applied');
          check(report.overflow <= 1, `Document overflow ${report.overflow}px`);
          check(report.spills.length === 0, `Text spills ${JSON.stringify(report.spills)}`);
          check(report.railErrors.length === 0, `Section alignment ${JSON.stringify(report.railErrors)}`);
          check(report.brokenAnchors.length === 0, 'Broken internal anchors');
          check(report.documentTitleCount === 1 && report.paperCount === 1, 'Invalid document hierarchy');
          check(report.visualCount > 0, 'Synthetic report does not exercise the visual section');
          check(report.unsafeRemoteMedia === 0, 'Email contains untrusted remote media or scripts');
          check(report.imagesMissingAlt === 0, 'Remote image is missing alternative text');
          check(!inline || report.styleCount === 0, 'Inline fallback contains head styles');
          const signatureKey = `${kind}-${language}`;
          if (!signatures.has(signatureKey)) signatures.set(signatureKey, report.signature);
          check(signatures.get(signatureKey) === report.signature, 'Content changes across viewport or style fallback');
          if (preview.directory && ((language === 'ko' && [1440, 390].includes(width)) ||
              (inline && width === 320))) {
            await page.screenshot({path: `${preview.directory}${file.replace('.html', '')}-${width}.png`, scale: 'css'});
          }
          if (kind === 'digest') {
            await page.locator('.azb-digest-title a').first().click();
            check(await page.evaluate(() => location.hash === '#azbrief-detail-1' &&
              document.getElementById('azbrief-detail-1').getBoundingClientRect().top < innerHeight),
            'Contents link does not reach the report');
            await page.locator('.azb-chapter a[href="#azbrief-summary"]').first().click();
            check(await page.evaluate(() => location.hash === '#azbrief-summary'), 'Return to contents fails');
          }
          const {signature, ...measurement} = report;
          results.push({file, width, ...measurement});
        }
      }
    }
  }
  return {passed: failures.length === 0, layouts: results.length, widths, failures, results};
}