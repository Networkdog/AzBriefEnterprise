async function checkAdminErrors(page, baseUrl = 'http://127.0.0.1:8765') {
  const checks = [];
  const scriptErrors = [];
  const onError = error => scriptErrors.push(error.message);
  const assert = (condition, name) => {
    if (!condition) throw new Error(name);
    checks.push(name);
  };
  const waitForHistory = () => page.waitForFunction(() =>
    document.getElementById('errors-list').getAttribute('aria-busy') === 'false');
  const endpoint = '**/api/admin/errors?*';
  const runId = '3'.padStart(32, '0');
  let queries = 0;
  let releaseOld = () => {};
  const countQueries = request => { if (request.url().includes('/api/admin/errors?')) queries += 1; };
  page.on('pageerror', onError);
  page.on('request', countQueries);
  try {
    await page.setViewportSize({width: 1440, height: 900});
    await page.goto(baseUrl + '/admin');
    await page.waitForFunction(() => document.getElementById('refresh-status').textContent.startsWith('Updated'));
    assert(queries === 0, 'Opening Overview does not query cloud error history');
    await page.locator('[data-section="errors"]').click();
    await page.locator('.error-event').first().waitFor();
    await waitForHistory();
    assert(await page.locator('.error-event').count() === 3, 'Global history includes errors outside local run memory');
    assert((await page.locator('.error-message').first().innerText()).includes('exceeded its time limit'),
      'Error content is visible rather than only a failed count');
    await page.locator('input[name="errors-basis"][value="utc"]').check();
    assert(await page.locator('.error-event time').first().innerText() === '2026-09-10 08:45:00 UTC',
      'UTC display preserves the exact event time including seconds');
    await page.locator('input[name="errors-basis"][value="local"]').check();
    assert(await page.locator('.error-event time').first().innerText() === await page.evaluate(() =>
      dateTimeTextForBasis('2026-09-10T08:45:00Z', 'local')), 'Local display uses the same event instant');
    assert(queries === 1, 'Time-zone changes do not repeat the cloud query');
    await page.locator('[data-section="run"]').click();
    await page.locator('#runs-filter').selectOption('failed');
    await page.locator('#runs [data-table-row]:visible').getByRole('button', {name: 'View', exact: true}).click();
    await page.locator('#run-detail').waitFor({state: 'visible'});
    await page.locator('#run-detail-logs').click();
    await waitForHistory();
    assert(await page.locator('#errors-run-id').inputValue() === runId, 'Run details opens filtered persisted history');
    assert(await page.locator('.error-event').count() === 2, 'Run filtering retains the correlated Hosted trace');
    assert((await page.locator('#errors-list').innerText()).includes('hosted'), 'Hosted failures remain visible');
    await page.locator('#errors-clear').click(); await waitForHistory();
    assert(await page.locator('.error-event').count() === 3, 'Clearing the run filter restores all recorded errors');

    const invalidQueries = queries;
    await page.locator('#errors-run-id').fill('invalid');
    await page.locator('#errors-refresh').click();
    assert(queries === invalidQueries, 'Invalid run IDs are rejected before the API call');
    await page.locator('#errors-run-id').fill('');
    await page.route(endpoint, route => route.fulfill({status: 502, json: {detail: 'Workspace access unavailable.'}}));
    await page.locator('#errors-refresh').click(); await waitForHistory();
    assert((await page.locator('#errors-load-error').innerText()).includes('Workspace access unavailable'),
      'A failed refresh explains the query failure');
    assert(await page.locator('.error-event').count() === 3 &&
      (await page.locator('#errors-status').innerText()).includes('retained'),
    'A failed same-filter refresh retains and labels previous results');
    await page.unroute(endpoint);

    await page.route(endpoint, route => route.fulfill({json: {events: [], has_more: false, period_hours: 24}}));
    await page.locator('#errors-refresh').click(); await waitForHistory();
    assert(await page.locator('.error-event').count() === 0 &&
      (await page.locator('#errors-status').innerText()).includes('No recorded errors'),
    'A successful empty query is distinct from unavailable history');
    await page.unroute(endpoint);

    const event = {
      occurred_at: '2026-09-28T01:02:03Z', level: 'ERROR', runtime: 'scheduler',
      event: 'synthetic_failed', status: 'failed', error_type: 'RuntimeError',
      message: '<img src=x onerror="window.injected=true">' + 'LongIdentifier'.repeat(100),
      run_id: runId, trace_id: 'b'.repeat(32), update_id: '123456',
      failed: 2, pending: 1
    };
    const data = {events: [event], has_more: true, period_hours: 24};
    await page.route(endpoint, route => route.fulfill({json: data}));
    await page.locator('#errors-refresh').click(); await waitForHistory();
    assert(await page.locator('#errors-list img').count() === 0 &&
      await page.evaluate(() => window.injected !== true), 'Error content cannot create executable markup');
    assert((await page.locator('#errors-status').innerText()).includes('More events exist'),
      'A limited page never claims to contain all matching events');
    await page.locator('.error-event summary').click();
    assert((await page.locator('.error-event .run-facts').innerText()).includes(runId),
      'Run and trace context is expandable');
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({width, height: width >= 768 ? 900 : 844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        'Error history has no page overflow at ' + width + 'px');
      assert(await page.locator('.error-message').evaluate(node =>
        node.scrollWidth <= node.clientWidth + 1), 'Long error messages wrap at ' + width + 'px');
      await page.screenshot({path: 'out/admin-error-history-' + width + '.png', fullPage: true});
    }
    await page.unroute(endpoint);

    const oldGate = new Promise(resolve => { releaseOld = resolve; });
    await page.route(endpoint, async route => {
      if (route.request().url().includes('run_id=')) {
        await oldGate;
        await route.fulfill({json: {...data, events: [{...event, message: 'STALE RESULT'}]}});
      } else {
        await route.fulfill({json: {...data, events: [{...event, message: 'LATEST RESULT'}]}});
      }
    });
    await page.locator('#errors-run-id').fill(runId);
    await Promise.all([
      page.waitForRequest(request => request.url().includes('/api/admin/errors?') &&
        request.url().includes('run_id=')),
      page.locator('#errors-refresh').click()
    ]);
    await page.locator('#errors-clear').click(); await waitForHistory();
    assert((await page.locator('.error-message').innerText()) === 'LATEST RESULT',
      'New filters complete independently of an older pending request');
    const oldResponse = page.waitForResponse(response => response.url().includes('/api/admin/errors?') &&
      response.url().includes('run_id='));
    releaseOld(); await oldResponse;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
    assert((await page.locator('.error-message').innerText()) === 'LATEST RESULT',
      'Delayed responses cannot overwrite the latest error query');
    await page.unroute(endpoint);

    await page.route(endpoint, route => route.fulfill({status: 503, json: {detail: 'Error history is not configured.'}}));
    await page.reload();
    await page.locator('#errors-load-error').waitFor({state: 'visible'});
    assert(await page.locator('.panel-errors').isVisible() &&
      (await page.locator('#errors-load-error').innerText()).includes('not configured'),
    'Direct Error history navigation shows configuration failure, not no errors');
    assert(scriptErrors.length === 0, 'No browser script errors');
    return {passed: true, checks};
  } finally {
    releaseOld();
    await page.unroute(endpoint);
    page.off('request', countQueries);
    page.off('pageerror', onError);
  }
}
