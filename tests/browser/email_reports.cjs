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
              '.azb-count-value, .azb-count-row th, .azb-chapter-number, .azb-toc-number, ' +
              '.azb-action-number, .azb-action-title, .azb-metric p, ' +
              '.azb-heading, .azb-hero-title, .azb-summary, ' +
              '.azb-resource-total, .azb-resource-summary strong, .azb-resource-summary .azb-link';
            for (const badge of document.querySelectorAll(boundedText)) {
              const cell = badge.closest('td, th');
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
            const briefErrors = [];
            const chartErrors = [];
            const shadingErrors = [];
            const summaryErrors = [];
            const paperColor = getComputedStyle(paper).backgroundColor;
            for (const concept of document.querySelectorAll('.azb-concept')) {
              const background = getComputedStyle(concept).backgroundColor;
              if (background === paperColor || background === 'rgba(0, 0, 0, 0)') {
                shadingErrors.push('Concept box is not shaded');
              }
            }
            for (const label of document.querySelectorAll('[class^="azb-badge-"]')) {
              const cell = label.closest('.azb-level-cell');
              const style = getComputedStyle(label);
              if (!cell || !cell.getAttribute('bgcolor') ||
                  getComputedStyle(cell).backgroundColor === paperColor) {
                shadingErrors.push(`Level cell is not shaded: ${label.textContent}`);
              }
              if (['borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth']
                  .some(property => parseFloat(style[property]) > 0) ||
                  style.backgroundColor !== 'rgba(0, 0, 0, 0)') {
                shadingErrors.push(`Level text still has a box: ${label.textContent}`);
              }
            }
            for (const brief of document.querySelectorAll('.azb-brief')) {
              const copy = brief.querySelector('.azb-brief-copy').getBoundingClientRect();
              const assessment = brief.querySelector('.azb-brief-assessment').getBoundingClientRect();
              const wide = document.querySelector('style') && innerWidth >= 800;
              const valid = wide
                ? Math.abs(copy.top - assessment.top) <= 1 &&
                  Math.abs(copy.right - assessment.left) <= 1 &&
                  Math.abs(copy.width / (copy.width + assessment.width) - 0.66) < 0.01
                : assessment.top >= copy.bottom - 1 &&
                  Math.abs(copy.left - assessment.left) <= 1 &&
                  Math.abs(copy.width - assessment.width) <= 1;
              if (!valid) briefErrors.push(wide ? 'Desktop columns are misaligned' : 'Fallback did not stack');
            }
            const countRows = Array.from(document.querySelectorAll('.azb-count-row'));
            const analyzedCount = countRows.reduce((total, row) =>
              total + Number(row.querySelector('.azb-count-value').textContent), 0);
            for (const row of countRows) {
              const label = row.querySelector('th');
              const value = row.querySelector('.azb-count-value');
              const count = Number(value.textContent);
              const track = row.querySelector('.azb-count-track').getBoundingClientRect();
              const fill = row.querySelector('[class^="azb-distribution-"]');
              const expected = analyzedCount ? count / analyzedCount * track.width : 0;
              if (count ? !fill || Math.abs(fill.getBoundingClientRect().width - expected) > 1.5 : fill) {
                chartErrors.push(`Incorrect bar length for ${label.textContent}`);
              }
              if (label.getBoundingClientRect().right > track.left + 1 ||
                  track.right > value.closest('td').getBoundingClientRect().left + 1) {
                chartErrors.push(`Chart labels overlap for ${label.textContent}`);
              }
              if (label.scope !== 'row' || row.closest('table').getAttribute('aria-hidden') === 'true') {
                chartErrors.push('Count labels are not accessible');
              }
            }
            for (const summary of document.querySelectorAll('.azb-resource-summary')) {
              if (summary.querySelectorAll('.azb-resource-summary-group').length > 10) {
                summaryErrors.push('Unbounded reason groups');
              }
              if (summary.closest('.azb-section-copy').querySelector('.azb-resource-row')) {
                summaryErrors.push('Summary repeats the full resource list');
              }
              for (const anchor of summary.querySelectorAll('a')) {
                const url = new URL(anchor.href);
                const query = decodeURIComponent(url.hash.split('/query/')[1] || '');
                if (url.hostname !== 'portal.azure.com' ||
                    !query.includes('subscriptionId in~ (') ||
                    !query.includes('minimumTlsVersion')) {
                  summaryErrors.push('Synthetic query lost its scope or TLS predicate');
                }
              }
            }
            for (const section of document.querySelectorAll('.azb-section-frame, .azb-section-wide')) {
              const heading = section.querySelector('.azb-section-head');
              const copy = section.querySelector('.azb-section-copy');
              const headingBox = heading.getBoundingClientRect();
              const copyBox = copy.getBoundingClientRect();
              const valid = copyBox.top >= headingBox.bottom - 1 &&
                Math.abs(copyBox.left - headingBox.left) <= 1 &&
                Math.abs(copyBox.width - headingBox.width) <= 1;
              if (!valid) railErrors.push(heading.textContent.trim());
            }
            const signature = JSON.stringify({
              headings: text('h1, h2, h3'),
              summaries: text('.azb-summary, .azb-digest-summary'),
              badges: text('[class^="azb-badge-"], .azb-verify'),
              counts: text('.azb-count-row th, .azb-count-value, .azb-digest-counts caption'),
              commands: text('.azb-cli'),
              reasons: text('.azb-resource-reason, .azb-resource-summary-group, .azb-resource-total, .azb-resource-snapshot'),
              links: Array.from(document.querySelectorAll('a'), anchor => anchor.getAttribute('href'))
            });
            return {
              actualWidth: innerWidth,
              overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
              spills, railErrors, briefErrors, chartErrors, shadingErrors, summaryErrors, brokenAnchors, signature,
              resourceSummaries: document.querySelectorAll('.azb-resource-summary').length,
              documentTitleCount: document.querySelectorAll('h1').length,
              paperCount: document.querySelectorAll('.azb-paper').length,
              visualCount: document.querySelectorAll('.azb-visual img').length,
              unsafeRemoteMedia: document.querySelectorAll('script, link').length + unsafeRemoteImages.length,
              imagesMissingAlt: remoteImages.filter(image => !image.alt.trim()).length,
              imagesNotLoaded: remoteImages.filter(image => !image.complete || image.naturalWidth === 0).length,
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
          check(report.briefErrors.length === 0, `Brief alignment ${JSON.stringify(report.briefErrors)}`);
          check(report.chartErrors.length === 0, `Count chart ${JSON.stringify(report.chartErrors)}`);
          check(report.shadingErrors.length === 0, `Shading ${JSON.stringify(report.shadingErrors)}`);
          check(report.summaryErrors.length === 0, `Resource summary ${JSON.stringify(report.summaryErrors)}`);
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
          if (language === 'ko' && width === 1440 && report.resourceSummaries > 0) {
            const link = page.locator('.azb-resource-summary a').first();
            if (await link.count()) {
              const href = await link.getAttribute('href');
              await page.route('https://portal.azure.com/**', route => route.fulfill({
                contentType: 'text/html',
                body: '<!doctype html><title>Synthetic Portal navigation</title><p>Intercepted locally.</p>'
              }));
              await link.click();
              await page.waitForURL(href);
              check(await page.title() === 'Synthetic Portal navigation', 'Portal link bypassed the local fixture');
              await page.unroute('https://portal.azure.com/**');
              await page.goto(root + file);
            }
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