async function checkControlSurfaces(page, baseUrl = 'http://127.0.0.1:8765') {
  const checks = [];
  const scriptErrors = [];
  const onError = error => scriptErrors.push(error.message);
  const assert = (condition, name) => {
    if (!condition) throw new Error(name);
    checks.push(name);
  };
  const readyAdmin = () => page.waitForFunction(() => document.getElementById('refresh-status').textContent.startsWith('Updated'));
  const readyArchive = () => page.waitForFunction(() => document.getElementById('results').getAttribute('aria-busy') === 'false');
  page.on('pageerror', onError);
  try {
    await page.setViewportSize({width:1440, height:900});
    await page.goto(baseUrl + '/admin');
    await readyAdmin();
    assert(await page.locator('section.panel:visible').count() === 1, 'Admin shows one workspace section');
    await page.locator('[data-section="subscriber"]').click();
    await page.locator('#subs-search').fill('security');
    assert(await page.locator('#subs [data-table-row]:visible').count() === 1, 'Subscriber search filters visible rows');
    assert(await page.locator('#subs-search').evaluate(node => parseFloat(getComputedStyle(node).paddingLeft) >= 34),
      'Search icon has separate text padding');
    await page.locator('#subs-search').fill('');
    await page.locator('#subs').getByRole('button', {name:'Edit', exact:true}).first().click();
    assert(await page.locator('#sub-add').innerText() === 'Save changes', 'Subscriber edit mode is reachable');
    await page.locator('#sub-cancel').click();
    assert(await page.locator('#sub-email').inputValue() === '', 'Cancel resets subscriber form');
    await page.locator('[data-section="updates"]').click();
    await page.locator('#updates').getByRole('button', {name:'Select', exact:true}).first().click();
    assert(await page.locator('.panel-run').isVisible(), 'Selecting an update opens manual runs');
    assert((await page.locator('#update-url').inputValue()).startsWith('https://learn.microsoft.com/'), 'Selected update populates run target');
    let runRequests = 0;
    const trackRuns = request => { if (request.method() === 'POST' && request.url().endsWith('/api/admin/runs')) runRequests += 1; };
    page.on('request', trackRuns);
    await page.locator('#run-mode').selectOption('recent');
    await page.locator('#recent-count').fill('0');
    await page.locator('#run').click();
    assert(await page.locator('#recent-count').evaluate(node => !node.validity.valid) && runRequests === 0,
      'Invalid recent count never submits');
    await page.locator('#run-mode').selectOption('date_range');
    await page.locator('#start-date').fill('2026-09-10');
    await page.locator('#end-date').fill('2026-09-01');
    await page.locator('#run').click();
    assert((await page.locator('#msg').innerText()).includes('Start date') && runRequests === 0,
      'Inverted run date range never submits');
    await page.locator('#dry').check();
    assert(await page.locator('#send-email').isDisabled() && !(await page.locator('#send-email').isChecked()),
      'Dry runs cannot request email');
    await page.locator('#runs-filter').selectOption('failed');
    assert(await page.locator('#runs [data-table-row]:visible').count() === 1, 'Run status filter shows failed runs');
    await page.locator('#runs').getByRole('button', {name:'View', exact:true}).last().click();
    await page.locator('#run-detail-error').waitFor({state:'visible'});
    assert(await page.locator('#run-detail-error').innerText() === 'Error: Synthetic upstream timeout', 'Run diagnostics keep the specific failure');
    await page.locator('#run-detail-close').click();
    assert(!(await page.locator('#run-detail').isVisible()), 'Run details close');
    page.off('request', trackRuns);

    await page.goto(baseUrl + '/archive');
    await readyArchive();
    assert(await page.locator('#results > li').count() === 25, 'Archive loads first page');
    await page.locator('#more').click(); await readyArchive();
    assert(await page.locator('#results > li').count() === 30, 'Archive pagination appends records');
    await page.locator('#q').fill('Storage');
    await page.locator('#filter-toggle').click();
    await page.locator('#category').selectOption('retirement');
    await page.locator('#filters button[type="submit"]').click(); await readyArchive();
    assert(await page.evaluate(() => new URLSearchParams(location.search).get('category')) === 'retirement', 'Archive filters are bookmarkable');
    assert(await page.locator('#active-filters > button').count() === 2, 'Active filters are individually removable');
    const filteredCount = await page.locator('#results > li').count();
    assert(filteredCount > 0 && filteredCount < 25, 'Archive category filter reaches API');
    const detailId = await page.locator('.row-title').first().getAttribute('data-archive-id');
    await page.locator('.row-title').first().click();
    await page.locator('#detail-body .detail-section').first().waitFor({state:'visible'});
    assert(await page.locator('#detail-outline a').count() >= 6, 'Report has a section outline');
    assert(await page.locator('#detail-body blockquote').count() > 0, 'Safe Markdown concept boxes remain rendered');
    let outlineRequests = 0;
    const trackOutline = request => { if (request.url().includes('/api/archive/analyses/')) outlineRequests += 1; };
    page.on('request', trackOutline);
    await page.locator('#detail-outline a').last().click();
    assert(await page.evaluate(() => scrollY > 0) && outlineRequests === 0, 'Outline scrolls without refetching the report');
    page.off('request', trackOutline);
    assert((await page.locator('#detail-feedback').getAttribute('href')).includes('report=archive%3A' + detailId),
      'Report feedback carries immutable archive reference');
    await page.locator('#back').click();
    await page.locator('#browser').waitFor({state:'visible'});
    assert(await page.locator('#q').inputValue() === 'Storage' && await page.locator('#results > li').count() === filteredCount,
      'Back to results preserves filters and loaded rows');
    await page.reload(); await readyArchive();
    assert(await page.locator('#category').inputValue() === 'retirement', 'Reload restores filter URL');
    await page.locator('#active-filters button').filter({hasText:'Retirement'}).click(); await readyArchive();
    assert(await page.evaluate(() => !new URLSearchParams(location.search).has('category')), 'Removing one filter updates URL');
    await page.locator('#q').fill('nothing-matches-this-synthetic-query');
    await page.locator('#filters button[type="submit"]').click(); await readyArchive();
    assert(await page.locator('#results > li').count() === 0 && await page.locator('#list-state').isVisible(),
      'Archive empty state is visible');
    await page.locator('#list-state button').click();
    await page.waitForFunction(() => document.querySelectorAll('#results > li').length === 25);
    await readyArchive();
    assert(await page.locator('#results > li').count() === 25, 'Empty-state reset restores results');

    const slowUrl = '**/api/archive/analyses?q=slow*';
    let releaseSlow;
    const slowGate = new Promise(resolve => { releaseSlow = resolve; });
    await page.route(slowUrl, async route => {
      await slowGate;
      try { await route.fulfill({json:{items:[], has_more:false, next_cursor:''}}); } catch (_) {}
    });
    await page.locator('#q').fill('slow');
    const slowRequest = page.waitForRequest(request => request.url().includes('/api/archive/analyses?q=slow'));
    await page.locator('#filters button[type="submit"]').click(); await slowRequest;
    await page.locator('#q').fill('Storage');
    await page.locator('#filters button[type="submit"]').click(); await readyArchive();
    releaseSlow(); await page.unroute(slowUrl);
    assert(await page.locator('#results > li').count() === 25 && await page.evaluate(() => new URLSearchParams(location.search).get('q')) === 'Storage',
      'Latest Archive query wins over a delayed response');

    await page.goto(baseUrl + '/feedback?lang=en&report=' + encodeURIComponent('archive:' + detailId));
    await page.locator('#submit').click();
    assert(await page.locator('#subject').getAttribute('aria-invalid') === 'true', 'Feedback reports inline required errors');
    assert(await page.locator('#subject').evaluate(node => node === document.activeElement), 'Feedback focuses first invalid field');
    await page.locator('#subject').fill('Synthetic browser test');
    await page.locator('#details').fill('This is synthetic feedback for interaction testing only.');
    await page.locator('#language').selectOption('ko');
    assert(await page.locator('#subject').inputValue() === 'Synthetic browser test', 'Language switch preserves feedback draft');
    assert(await page.locator('html').getAttribute('lang') === 'ko', 'Language switch updates document language');
    assert(await page.locator('#back-report').getAttribute('href') === '/archive/' + detailId + '?lang=ko',
      'Feedback return link stays within the current archive');
    let feedbackRequests = 0;
    await page.route('**/api/feedback', async route => {
      feedbackRequests += 1;
      if (feedbackRequests === 1) await route.fulfill({status:429, json:{detail:'Rate limited'}});
      else await route.fulfill({status:201, json:{accepted:true, feedback_id:'SYNTHETIC-RECEIPT', notification_sent:false}});
    });
    await page.locator('#submit').click(); await page.locator('#status').waitFor({state:'visible'});
    assert(await page.locator('#details').inputValue() === 'This is synthetic feedback for interaction testing only.',
      'Rate-limit failure preserves input');
    await page.locator('#submit').click(); await page.locator('#receipt').waitFor({state:'visible'});
    assert(await page.locator('#receipt-id').innerText() === 'SYNTHETIC-RECEIPT', 'Accepted feedback displays receipt');
    assert((await page.locator('#receipt-message').innerText()).includes('저장되었지만'),
      'Email notification failure is not reported as storage failure');
    await page.locator('#feedback-form').evaluate(form => form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    assert(feedbackRequests === 2, 'Receipt state prevents duplicate submission');
    await page.locator('#send-another').click();
    assert(await page.locator('#subject').inputValue() === '' && await page.locator('#report-reference').inputValue() === 'archive:' + detailId,
      'New feedback resets content and retains original report reference');
    await page.unroute('**/api/feedback');

    const layouts = [];
    for (const width of [1440,768,390,320]) {
      await page.setViewportSize({width,height:900});
      for (const path of ['/admin#subscriber','/admin#run','/archive','/archive/' + detailId,'/feedback?lang=ja']) {
        await page.goto(baseUrl + path);
        if (path.startsWith('/admin')) await readyAdmin();
        if (path === '/archive') await readyArchive();
        if (path.startsWith('/archive/')) await page.locator('#detail-body .detail-section').first().waitFor();
        const layout = await page.evaluate(() => ({
          overflow:Math.max(0,document.documentElement.scrollWidth - document.documentElement.clientWidth),
          clippedButtons:Array.from(document.querySelectorAll('button')).filter(node => node.getClientRects().length && node.scrollWidth > node.clientWidth + 2).map(node => node.id || node.textContent.trim()),
          mainVisible:document.querySelector('main').getBoundingClientRect().width > 0
        }));
        layouts.push({width,path,...layout});
        assert(layout.overflow === 0 && !layout.clippedButtons.length && layout.mainVisible,
          'Responsive layout: ' + width + ' ' + path);
      }
    }
    assert(scriptErrors.length === 0, 'No page script errors');
    return {passed:checks.length,checks,layouts,scriptErrors};
  } finally {
    page.off('pageerror', onError);
  }
}