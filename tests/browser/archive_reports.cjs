async function checkArchiveReports(page, baseUrl = '') {
  const root = baseUrl || await page.evaluate(() => location.origin);
  const local = await page.evaluate(value => {
    const url = new URL(value);
    return url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  }, root);
  if (!local) throw new Error('Use the synthetic loopback preview, never a live tenant.');
  const listingResponse = await page.request.get(root + '/api/archive/analyses?limit=1');
  const listing = await listingResponse.json();
  const archiveId = listing.items[0].archive_id;
  const detailPath = '/api/archive/analyses/' + archiveId;
  const documentResponse = await page.request.get(root + detailPath);
  const original = await documentResponse.json();
  const originalJson = JSON.stringify(original);
  const emptyDetails = {
    cost_impact: '', security_impact: '', performance_impact: '', operational_impact: ''
  };
  const summary = '**Retained legacy summary** with a [reference](https://learn.microsoft.com/azure/).';
  const cases = [
    {name: 'absent details', category: 'retirement'},
    {name: 'null details', details: null},
    {name: 'empty object', details: {}},
    {name: 'empty dimensions', details: emptyDetails},
    {name: 'whitespace sections', details: {
      cost_impact: ' ', security_impact: '\n', performance_impact: '\t', operational_impact: ' \n '
    }, blankNarrative: true},
    {name: 'legacy summary fallback', details: emptyDetails, summary},
    {name: 'partial dimensions', details: {...emptyDetails, security_impact: 'Retained security evidence'}, summary},
    {name: 'populated dimensions', category: 'retirement', details: {
      cost_impact: 'Retained cost evidence', security_impact: 'Retained security evidence',
      performance_impact: 'Retained performance evidence', operational_impact: 'Retained operational evidence'
    }, checks: ['', ' \n ', 'Retained additional check']},
    {name: 'empty JSON change', category: 'retirement', details: emptyDetails,
      summary: JSON.stringify(emptyDetails), noJson: true},
    {name: 'empty JSON capability', details: {}, summary: JSON.stringify(emptyDetails), noJson: true},
    {name: 'legacy JSON populated', details: null,
      summary: JSON.stringify({...emptyDetails, security_impact: 'Restored legacy protection'}),
      expectedDimensions: ['Restored legacy protection'], noJson: true},
    {name: 'paragraph numbering and nearby notes', details: emptyDetails, notes: true,
      narrative: '1. TLS 1.2 protects database backup connections.\n\n1. Retention applies for seven days.\n\n1. Existing backups are protected.\n\n> **TLS**: Retained term explanation.'},
    {name: 'real numbered procedure preserved', details: emptyDetails,
      narrative: '1. Read the configuration.\n2. Verify the result.', listItems: 2},
    {name: 'loose numbering retained', details: emptyDetails,
      narrative: '1. Read the configuration.\n\n2. Verify the result.\n\n3) Record the decision.',
      listItems: 3, starts: [1,2,3]},
    {name: 'literal numbering in code preserved', details: emptyDetails,
      narrative: '```text\n1. Literal example\n\n1. Another literal\n```', code: true},
    {name: 'action details and unsafe references', category: 'retirement', details: emptyDetails, safety: true}
  ];
  const failures = [];
  const scriptErrors = [];
  let checks = 0;
  let currentDocument = original;
  const onError = error => scriptErrors.push(error.message);
  const routePattern = '**' + detailPath + '?view=report';
  const routeHandler = route => route.fulfill({json: currentDocument});
  const assert = (condition, name) => {
    checks += 1;
    if (!condition) failures.push(name);
  };
  page.on('pageerror', onError);
  await page.route(routePattern, routeHandler);
  try {
    for (const language of ['ko', 'en', 'ja']) {
      for (const width of [1440, 768, 390, 320]) {
        await page.setViewportSize({width, height: 900});
        for (const scenario of cases) {
          currentDocument = JSON.parse(originalJson);
          Object.assign(currentDocument.result, {
            update_category: scenario.category || 'preview',
            impact_details: scenario.details,
            impact_summary: scenario.summary || ''
          });
          if (scenario.blankNarrative) Object.assign(currentDocument.result, {
            one_line_summary: ' \n ', relevance_reason: '\t', relevance_evidence: ' \n ',
            additional_checks: ['', '\t', ' \n ']
          });
          if (scenario.checks) currentDocument.result.additional_checks = scenario.checks;
          if (scenario.narrative) currentDocument.result.relevance_reason = scenario.narrative;
          if (scenario.notes) currentDocument.result.reference_docs = [{title: 'TLS 1.2',
            url: 'https://learn.microsoft.com/azure/storage/tls', description: 'Retained reference explanation.'}];
          if (scenario.safety) {
            currentDocument.result.action_items = [{task: 'Verify the target', why: 'Preserved action reason',
              procedure: '1. Inspect the configuration.\n2. Verify the outcome.', estimated_time: '17 min',
              verification_status: 'caution', verification_notes: ['Preserved verification note'],
              cli_command: 'echo "<literal>"', reference_url: 'https://learn.microsoft.com/azure/'}];
            currentDocument.result.reference_docs = [{title: '<img src=x onerror=alert(1)>',
              url: 'javascript:alert(1)', description: '<script>window.injected=true</script>'}];
          }
          const presentationResponse = await page.request.post(root + '/api/preview/report-presentation',
            {data: currentDocument.result});
          if (!presentationResponse.ok()) throw new Error('Synthetic presentation validation failed');
          currentDocument.presentation = await presentationResponse.json();
          await page.goto(root + '/archive/' + archiveId + '?lang=' + language);
          await page.waitForFunction(() => document.getElementById('detail').getAttribute('aria-busy') === 'false');
          const rendered = await page.evaluate(() => {
            const body = document.getElementById('detail-body');
            const sections = Array.from(body.querySelectorAll('.detail-section'));
            const links = Array.from(document.querySelectorAll('#detail-outline a'));
            return {
              title: document.getElementById('detail-title').textContent,
              emptySections: sections.filter(section => {
                const content = section.cloneNode(true);
                content.querySelector('h2').remove();
                return !content.textContent.trim();
              }).map(section => section.querySelector('h2').textContent),
              emptyItems: Array.from(body.querySelectorAll('li')).filter(item => !item.textContent.trim()).length,
              dimensions: Array.from(body.querySelectorAll('.detail-impact dd'), node => node.textContent),
              fallback: body.textContent.includes('Retained legacy summary'),
              fallbackFormatted: Array.from(body.querySelectorAll('strong')).some(node => node.textContent === 'Retained legacy summary'),
              rawJsonVisible: /"(?:cost|security|performance|operational)_impact"\s*:/.test(body.textContent),
              overviewNumbers: body.querySelectorAll('.detail-overview .markdown > ol li').length,
              orderedStarts: Array.from(body.querySelectorAll('.detail-overview .markdown > ol'), node => node.start),
              literalCode: body.querySelector('.detail-overview pre code')?.textContent || '',
              combinedEnvironment: !body.querySelector('.resource-list') ||
                Boolean(body.querySelector('.detail-environment .resource-list')),
              impactInOverview: !body.querySelector('.detail-impact dd') ||
                Boolean(body.querySelector('.detail-overview .detail-impact')),
              referenceBoxes: body.querySelectorAll('.reference-note').length,
              separateReferences: sections.some(section => ['References','\ucc38\uace0 \ubb38\uc11c','\u53c2\u8003\u30c9\u30ad\u30e5\u30e1\u30f3\u30c8']
                .includes(section.querySelector('h2').textContent)),
              noteOrder: (() => {
                const paragraph = Array.from(body.querySelectorAll('.detail-overview p'))
                  .find(node => node.textContent === 'TLS 1.2 protects database backup connections.');
                return paragraph?.nextElementSibling?.tagName === 'BLOCKQUOTE' &&
                  paragraph.nextElementSibling.nextElementSibling?.classList.contains('reference-note');
              })(),
              actionFieldsRetained: ['Preserved action reason','17 min','Preserved verification note']
                .every(value => body.querySelector('.detail-actions')?.textContent.includes(value)),
              unsafeNodes: body.querySelectorAll('script,img,[onerror],a[href^="javascript:"]').length,
              checkRetained: body.textContent.includes('Retained additional check'),
              outlineValid: links.length === sections.length && links.every(link => document.getElementById(link.hash.slice(1))),
              overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
              width: innerWidth
            };
          });
          const prefix = `${language}/${width}/${scenario.name}: `;
          const dimensions = scenario.expectedDimensions || Object.values(scenario.details || {}).filter(value => value.trim());
          const legacyFallback = scenario.summary === summary && !dimensions.length;
          assert(rendered.title === original.update.title, prefix + 'report loaded');
          assert(rendered.emptySections.length === 0, prefix + 'no empty sections: ' + rendered.emptySections.join(', '));
          assert(rendered.emptyItems === 0, prefix + 'no blank list items');
          assert(JSON.stringify(rendered.dimensions) === JSON.stringify(dimensions), prefix + 'only populated dimensions remain');
          assert(rendered.fallback === legacyFallback, prefix + 'summary fallback is used only without dimensions');
          if (legacyFallback) assert(rendered.fallbackFormatted, prefix + 'fallback keeps safe Markdown');
          assert(!rendered.rawJsonVisible, prefix + 'no serialized impact JSON is displayed');
          assert(rendered.combinedEnvironment, prefix + 'resources stay in the environment section');
          if (dimensions.length) assert(rendered.impactInOverview === ((scenario.category || 'preview') !== 'retirement'),
            prefix + 'capability dimensions are in overview and changes remain separate');
          assert(!rendered.separateReferences && rendered.referenceBoxes === currentDocument.result.reference_docs.length,
            prefix + 'reference notes replace a separate footer section');
          if (scenario.notes) assert(rendered.noteOrder && rendered.overviewNumbers === 0,
            prefix + 'numbered prose is normalized and both notes follow their paragraph');
          if (scenario.listItems) assert(rendered.overviewNumbers === scenario.listItems, prefix + 'real list survives');
          if (scenario.starts) assert(JSON.stringify(rendered.orderedStarts) === JSON.stringify(scenario.starts), prefix + 'list start values survive');
          if (scenario.code) assert(rendered.literalCode === '1. Literal example\n\n1. Another literal', prefix + 'code survives');
          if (scenario.safety) assert(rendered.actionFieldsRetained && rendered.unsafeNodes === 0,
            prefix + 'action details remain and injected HTML/links cannot execute');
          if (scenario.checks) assert(rendered.checkRetained, prefix + 'nonempty check survives');
          assert(rendered.outlineValid, prefix + 'outline matches visible sections');
          assert(rendered.overflow <= 0 && rendered.width === width, prefix + 'responsive geometry');
        }
      }
    }
    const storedResponse = await page.request.get(root + detailPath);
    assert(JSON.stringify(await storedResponse.json()) === originalJson, 'Stored report remains unchanged');
    assert(scriptErrors.length === 0, 'No page script errors');
    return {passed: failures.length === 0, cases: cases.length * 12, checks,
      failureCount: failures.length, failures: failures.slice(0, 20), scriptErrors};
  } finally {
    await page.unroute(routePattern, routeHandler);
    page.off('pageerror', onError);
  }
}