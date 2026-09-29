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
              '.azb-count-item, .azb-count-value, .azb-chapter-number, .azb-toc-number, ' +
              '.azb-action-number, .azb-action-title, .azb-assessment-label, ' +
              '.azb-heading, .azb-hero-title, .azb-summary, .azb-digest-heading th, ' +
              '.azb-reference p, .azb-concept, .azb-resource-caption, ' +
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
            const gutters = Array.from(new Set(Array.from(document.querySelectorAll('.azb-pad'))
              .flatMap(cell => {
                const style = getComputedStyle(cell);
                return [style.paddingLeft, style.paddingRight];
              })));
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
            const contentsErrors = [];
            const countErrors = [];
            const shadingErrors = [];
            const summaryErrors = [];
            const overviewErrors = [];
            const annotationErrors = [];
            const overviewTitles = new Set(['\uac1c\uc694', 'Analysis Summary', '\u5206\u6790\u6982\u8981']);
            const sectionTitle = node => node.closest('.azb-section')
              ?.querySelector('.azb-heading')?.textContent.trim();
            const environmentTitles = new Set(['\ud658\uacbd \uc5f0\uad00\uc131',
              'Environment Relevance', '\u74b0\u5883\u3068\u306e\u95a2\u9023\u6027']);
            for (const resources of document.querySelectorAll('.azb-resources, .azb-resource-summary')) {
              if (!environmentTitles.has(sectionTitle(resources))) {
                annotationErrors.push('Resource detail is outside the environment section');
              }
            }
            const referenceTitles = new Set(['\ucc38\uace0 \ubb38\uc11c', 'References',
              '\u53c2\u8003\u30c9\u30ad\u30e5\u30e1\u30f3\u30c8']);
            if (text('.azb-heading').some(title => referenceTitles.has(title))) {
              annotationErrors.push('Standalone reference section remains');
            }
            const references = Array.from(document.querySelectorAll('.azb-reference'));
            if (!references.length || references.some(note => !note.closest('.azb-section-copy'))) {
              annotationErrors.push('Reference boxes are missing from the report body');
            }
            for (const concept of document.querySelectorAll('.azb-concept')) {
              const term = concept.querySelector('strong')?.textContent.trim();
              if (term === 'TLS' && !concept.previousElementSibling?.textContent.includes('TLS')) {
                annotationErrors.push('Glossary box is separated from its first term paragraph');
              }
            }
            const language = document.documentElement.lang.split('-')[0];
            const languageScript = {ko: /[\uac00-\ud7a3]/, ja: /[\u3040-\u30ff]/, en: /[a-z]/i};
            for (const hero of document.querySelectorAll('.azb-hero')) {
              if (/[^\x00-\x7f]/.test(hero.querySelector('.azb-hero-title').textContent)) {
                annotationErrors.push('Synthetic source title was translated');
              }
              const summary = hero.querySelector('.azb-summary').textContent;
              if (!languageScript[language].test(summary) || /[\r\n]/.test(summary)) {
                annotationErrors.push('Summary is not one localized sentence');
              }
            }
            for (const image of document.querySelectorAll('.azb-visual img')) {
              if (!overviewTitles.has(sectionTitle(image))) overviewErrors.push('Visual outside overview');
            }
            const separateTitles = ['\uc2dc\uac01 \uc790\ub8cc', 'Visual Guide',
              '\u30d3\u30b8\u30e5\u30a2\u30eb\u30ac\u30a4\u30c9', '\ud65c\uc6a9 \uae30\ud68c',
              'Opportunity', '\u6d3b\u7528\u6a5f\u4f1a'];
            if (text('.azb-heading').some(title => separateTitles.includes(title))) {
              overviewErrors.push('Standalone visual or opportunity heading');
            }
            if (document.querySelector('.azb-digest-detail') &&
                !Array.from(document.querySelectorAll('.azb-impact'))
                  .some(impact => overviewTitles.has(sectionTitle(impact)))) {
              overviewErrors.push('Capability dimensions missing from overview');
            }
            const paperColor = getComputedStyle(paper).backgroundColor;
            for (const concept of document.querySelectorAll('.azb-concept, .azb-reference')) {
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
              const valid = assessment.top >= copy.bottom - 1 &&
                Math.abs(copy.left - assessment.left) <= 1 &&
                Math.abs(copy.width - assessment.width) <= 1 &&
                Math.abs(copy.width - brief.getBoundingClientRect().width) <= 1;
              if (!valid) briefErrors.push('Summary and compact assessment are not full-width');
              const takeaway = brief.querySelector('.azb-takeaway');
              if (takeaway.querySelectorAll('p').length !== 1 ||
                  !takeaway.querySelector('.azb-summary')?.textContent.trim()) {
                briefErrors.push('Summary is missing or still has a subtitle');
              }
              const metrics = Array.from(brief.querySelectorAll('.azb-assessment .azb-metric'));
              if (metrics.length !== 3) briefErrors.push('An assessment axis is missing');
              const firstTop = metrics[0]?.getBoundingClientRect().top;
              const boxes = metrics.map(metric => metric.getBoundingClientRect());
              for (const [index, metric] of metrics.entries()) {
                const box = boxes[index];
                const label = metric.querySelector('.azb-assessment-label');
                const value = metric.querySelector('[class^="azb-badge-"]');
                if (!label?.textContent.trim() || !value?.textContent.trim() ||
                    box.height > 32 || box.left < assessment.left - 1 ||
                    box.right > assessment.right + 1 ||
                    (copy.width >= 400 && Math.abs(box.top - firstTop) > 1)) {
                  briefErrors.push('Assessment labels are not compact or did not wrap within the header');
                }
                if (boxes.slice(index + 1).some(other =>
                    Math.min(box.right, other.right) - Math.max(box.left, other.left) > 1 &&
                    Math.min(box.bottom, other.bottom) - Math.max(box.top, other.top) > 1)) {
                  briefErrors.push('Compact assessment labels overlap');
                }
              }
            }
            for (const row of document.querySelectorAll('.azb-digest-row')) {
              const copy = row.querySelector('.azb-digest-copy').getBoundingClientRect();
              const cells = Array.from(row.querySelectorAll('.azb-digest-metrics'));
              const first = cells[0].getBoundingClientRect();
              const last = cells[cells.length - 1].getBoundingClientRect();
              const metrics = {top: first.top, left: first.left, right: last.right,
                bottom: last.bottom, width: last.right - first.left};
              const wide = document.querySelector('style') && innerWidth >= 800;
              const valid = wide
                ? Math.abs(copy.top - metrics.top) <= 1 &&
                  Math.abs(copy.right - metrics.left) <= 1 &&
                  Math.abs(copy.width / (copy.width + metrics.width) - 0.70) < 0.01
                : metrics.top >= copy.bottom - 1 &&
                  Math.abs(copy.left - metrics.left) <= 1 &&
                  Math.abs(copy.width - metrics.width) <= 1;
              if (!valid) contentsErrors.push(wide ? 'Contents lost the 70/30 split' : 'Contents did not stack');
              if (wide) {
                const headers = Array.from(document.querySelectorAll('.azb-digest-heading th'));
                const layout = row.querySelector('.azb-digest-layout-row').getBoundingClientRect();
                for (const [index, cell] of cells.entries()) {
                  const box = cell.getBoundingClientRect();
                  const header = headers[index + 1].getBoundingClientRect();
                  if (Math.abs(box.top - metrics.top) > 1 ||
                      Math.abs(box.left - header.left) > 3 ||
                      Math.abs(box.width - (copy.width + metrics.width) * 0.10) > 2 ||
                      box.right > metrics.right + 1) {
                    contentsErrors.push('Contents metric wrapped or lost header alignment');
                  }
                  const badge = cell.querySelector('[class^="azb-badge-"]').getBoundingClientRect();
                  if (Math.abs(box.top - layout.top) > 1 ||
                      Math.abs(box.bottom - layout.bottom) > 1 ||
                      Math.abs((badge.top + badge.bottom) / 2 - (box.top + box.bottom) / 2) > 1) {
                    shadingErrors.push('Contents shading is not full-height or its label is not centered');
                  }
                }
                const title = row.querySelector('.azb-digest-title a');
                const originalTitle = title.textContent;
                title.textContent = Array(8).fill(originalTitle).join(' ');
                const expanded = row.querySelector('.azb-digest-layout-row').getBoundingClientRect();
                if (expanded.height <= layout.height || cells.some(cell => {
                  const box = cell.getBoundingClientRect();
                  return Math.abs(box.top - expanded.top) > 1 ||
                    Math.abs(box.bottom - expanded.bottom) > 1;
                })) shadingErrors.push('Contents shading does not grow with a long title');
                title.textContent = originalTitle;
              }
            }
            for (const label of document.querySelectorAll('.azb-digest-heading .azb-col-metric, ' +
                '.azb-digest-metrics [class^="azb-badge-"], .azb-digest-metrics .azb-metric-label')) {
              if (!label.getClientRects().length) continue;
              const cell = label.closest('td, th').getBoundingClientRect();
              const range = document.createRange();
              range.selectNodeContents(label);
              const textBox = range.getBoundingClientRect();
              if (Math.abs((textBox.left + textBox.right) / 2 - (cell.left + cell.right) / 2) > 1) {
                contentsErrors.push(`Metric text is not horizontally centered: ${label.textContent.trim()}`);
              }
            }
            const countSummary = document.querySelector('.azb-digest-counts');
            if (document.querySelector('.azb-count-track, .azb-digest-distribution')) {
              countErrors.push('Importance counts still use a chart');
            }
            if (document.querySelector('.azb-digest-table') && !countSummary) {
              countErrors.push('Digest count summary is missing');
            }
            if (countSummary) {
              const items = Array.from(countSummary.querySelectorAll('.azb-count-item'));
              const analyzed = items.reduce((total, item) =>
                total + Number(item.querySelector('.azb-count-value')?.textContent), 0);
              if (items.length !== 3 || analyzed !== document.querySelectorAll('.azb-digest-row').length ||
                  !countSummary.getAttribute('aria-label') || countSummary.getAttribute('role') !== 'group') {
                countErrors.push('Count labels or analyzed-only total were lost');
              }
              const firstTop = items[0]?.getBoundingClientRect().top;
              for (const item of items) {
                const label = item.querySelector('.azb-count-label');
                const value = item.querySelector('.azb-count-value');
                const box = item.getBoundingClientRect();
                if (!label?.textContent.trim() || !value?.textContent.trim() ||
                    Math.abs(box.top - firstTop) > 1 || box.height > 30 ||
                    parseFloat(getComputedStyle(value).fontSize) !== 14) {
                  countErrors.push('Synthetic counts no longer form a compact readable line');
                }
              }
              if (!document.querySelector('.azb-digest-status')?.textContent.includes(String(analyzed)) ||
                  (document.querySelector('.azb-digest-skip') &&
                   !document.querySelector('.azb-digest-skipped')?.textContent.trim())) {
                countErrors.push('Analysis or skipped status was lost');
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
              assessments: text('.azb-assessment-label'),
              counts: text('.azb-count-label, .azb-count-value, .azb-digest-status'),
              commands: text('.azb-cli'),
              reasons: text('.azb-resource-reason, .azb-resource-summary-group, .azb-resource-total, .azb-resource-snapshot'),
              notes: text('.azb-concept, .azb-reference'),
              links: Array.from(document.querySelectorAll('a'), anchor => anchor.getAttribute('href'))
            });
            return {
              actualWidth: innerWidth,
              overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
              gutters, spills, railErrors, briefErrors, contentsErrors, countErrors, shadingErrors, summaryErrors, overviewErrors, annotationErrors, brokenAnchors, signature,
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
          const expectedGutter = inline ? 20 : width <= 400 ? 12 : width <= 640 ? 16 : width >= 1100 ? 24 : 20;
          check(report.actualWidth === width, 'Requested viewport was not applied');
          check(report.overflow <= 1, `Document overflow ${report.overflow}px`);
          check(report.gutters.length === 1 && report.gutters[0] === `${expectedGutter}px`,
            `Section gutters ${JSON.stringify(report.gutters)}; expected ${expectedGutter}px`);
          check(report.spills.length === 0, `Text spills ${JSON.stringify(report.spills)}`);
          check(report.railErrors.length === 0, `Section alignment ${JSON.stringify(report.railErrors)}`);
          check(report.briefErrors.length === 0, `Brief alignment ${JSON.stringify(report.briefErrors)}`);
          check(report.contentsErrors.length === 0, `Contents alignment ${JSON.stringify(report.contentsErrors)}`);
          check(report.countErrors.length === 0, `Compact counts ${JSON.stringify(report.countErrors)}`);
          check(report.shadingErrors.length === 0, `Shading ${JSON.stringify(report.shadingErrors)}`);
          check(report.summaryErrors.length === 0, `Resource summary ${JSON.stringify(report.summaryErrors)}`);
          check(report.overviewErrors.length === 0, `Overview ${JSON.stringify(report.overviewErrors)}`);
          check(report.annotationErrors.length === 0, `Report content ${JSON.stringify(report.annotationErrors)}`);
          check(report.brokenAnchors.length === 0, 'Broken internal anchors');
          check(report.documentTitleCount === 1 && report.paperCount === 1, 'Invalid document hierarchy');
          check(report.visualCount > 0, 'Synthetic report does not exercise the overview visual');
          check(report.unsafeRemoteMedia === 0, 'Email contains untrusted remote media or scripts');
          check(report.imagesMissingAlt === 0, 'Remote image is missing alternative text');
          check(report.imagesNotLoaded === 0, 'Remote image failed to load');
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