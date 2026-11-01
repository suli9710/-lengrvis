// Reproducible UI review. All approval/task screenshots use controlled fixtures,
// never a real approval or filesystem mutation. Pass baseline/current URLs.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const AxeBuilder = require('@axe-core/playwright').default;
const { openDisposablePage, installHealthyBackendMocks, installUnavailableBackendMocks,
  completedComputerTemplateTask } = require('./first-launch-smoke.cjs');
const output = path.resolve(__dirname, '../../docs/ui-review-2026-11-01');
const current = process.argv[2] || 'http://127.0.0.1:5175';
const baseline = process.argv[3];
const sessions = [];
const report = { scenarios: [], accessibility: [], performance: [], errors: [] };
const sizes = [['desktop',1440,1000], ['tablet',768,1024], ['mobile',390,844], ['narrow',320,740]];

async function makePage(url, offline = false, tasks = [], configure) {
  const session = await openDisposablePage();
  sessions.push(session);
  const {page} = session;
  session.counters = {};
  page.on('pageerror', error => report.errors.push(error.message));
  await page.addInitScript(() => {
    window.reviewVitals = {lcp: null, cls: 0, interactions: []};
    new PerformanceObserver(list => {for(const e of list.getEntries())window.reviewVitals.lcp=e.startTime;})
      .observe({type:'largest-contentful-paint',buffered:true});
    new PerformanceObserver(list => {for(const e of list.getEntries())if(!e.hadRecentInput)window.reviewVitals.cls+=e.value;})
      .observe({type:'layout-shift',buffered:true});
    new PerformanceObserver(list => {for(const e of list.getEntries())if(e.interactionId)window.reviewVitals.interactions.push(e.duration);})
      .observe({type:'event',buffered:true,durationThreshold:16});
  });
  if(offline) await installUnavailableBackendMocks(page,session.counters);
  else await installHealthyBackendMocks(page,session.counters,{tasks});
  if(configure)await configure(page);
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goto(url);
  await page.locator('#office-command-input').waitFor();
  await page.getByRole('button',{name:'刷新',exact:true}).waitFor();
  await page.waitForFunction(() => !document.querySelector('button[aria-label="刷新"]')?.disabled);
  return session;
}
async function snapshot(page, name, fullPage = false) {
  const captureName = name.replace(/^after-/, 'refined-').replace(/^before-/, 'refined-baseline-');
  await page.screenshot({path:path.join(output,captureName+'.png'),fullPage,animations:'disabled'});
}
async function audit(page, scene) {
  const result = await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
  report.accessibility.push({scene, violations:result.violations.map(v=>({id:v.id,impact:v.impact,description:v.description,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))});
}
async function navigate(page, name) {
  const button = page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name, exact: true });
  if (!await button.isVisible()) await page.getByTestId('nav-tools-toggle').click();
  await button.click();
}
async function main() {
  for(const [prefix,url] of [['before',baseline],['after',current]]) {
    if(!url)continue;
    const {page,context}=await makePage(url,true);
    if(prefix==='after') {
      assert.equal(await page.getByTestId('home-templates').getAttribute('open'),null);
      assert.equal(await page.getByTestId('home-more').getAttribute('open'),null);
      assert.equal(await page.getByTestId('home-result-timeline-card').isVisible(),false);
      assert.equal(await page.getByTestId('home-status-strip').isVisible(),false);
      report.scenarios.push({scene:'first screen focuses task input; templates, empty result, status and history remain collapsed',passed:true});
    }
    for(const [size,width,height] of sizes) {
      await page.setViewportSize({width,height});
      await page.locator('#office-command-input').scrollIntoViewIfNeeded();
      await page.evaluate(()=>{window.scrollTo(0,0);document.querySelector('.lengrvis-home').scrollTop=0;});
      await snapshot(page,`${prefix}-${size}`,size!=='desktop');
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
      report.scenarios.push({scene:`${prefix}-${size}-offline`,horizontalOverflow:overflow});
      if(prefix==='after'){assert.equal(overflow,false);await audit(page,`home-${size}`);}
    }
    if(prefix==='after') {
      await page.setViewportSize({width:1440,height:1000});
      // 1440x1000 display at 200%: 720x500 CSS pixels, DPR 2.
      // This verifies reflow; native Electron toolbar zoom is a separate check.
      await page.setViewportSize({width:720,height:500});
      const cdp = await page.context().newCDPSession(page);
      await cdp.send('Emulation.setDeviceMetricsOverride',{width:720,height:500,deviceScaleFactor:2,mobile:false});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const zoomCapture=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
      fs.writeFileSync(path.join(output,'refined-zoom-200.png'),Buffer.from(zoomCapture.data,'base64'));
      const zoomOverflow = await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
      assert.equal(zoomOverflow,false);
      report.scenarios.push({scene:'200% zoom equivalent reflow: 720x500 CSS pixels at DPR2',horizontalOverflow:zoomOverflow});
      await cdp.send('Emulation.clearDeviceMetricsOverride');
      await page.setViewportSize({width:1440,height:1000});
      const input=page.locator('#office-command-input');
      await input.fill('保留这份草稿：只查找文件，不删除。');
      await page.getByTestId('home-templates').locator('summary').first().click();
      await page.getByTestId('office-template-clean-downloads').click();
      assert.equal(await input.inputValue(),'保留这份草稿：只查找文件，不删除。');
      await page.getByTestId('office-template-summarize-document').click();
      await page.getByRole('button',{name:'首页',exact:true}).click();
      assert.equal(await input.inputValue(),'保留这份草稿：只查找文件，不删除。');
      await page.reload();await input.waitFor();
      assert.equal(await input.inputValue(),'保留这份草稿：只查找文件，不删除。');
      await page.locator('.command-footer__send').click();
      await page.waitForFunction(()=>document.querySelector('#office-command-status')?.getAttribute('role')==='alert');
      assert.equal(await input.inputValue(),'保留这份草稿：只查找文件，不删除。');
      await snapshot(page,'after-offline-retry');
      report.scenarios.push({scene:'manual draft protected from template; document direct-open/return; refresh; offline send failure preserves input',passed:true});
      await input.focus();await page.keyboard.press('Tab');
      assert.equal(await page.locator('.command-footer__send').evaluate(el=>el===document.activeElement),true);
      await page.getByRole('button',{name:/打开活动中心/}).click();
      await page.keyboard.press('Escape');
      await page.waitForFunction(()=>document.activeElement?.classList.contains('activity-center-trigger'));
      report.scenarios.push({scene:'keyboard input to send; Escape activity drawer restores focus',passed:true});
    }
    await context.close();
    const complete=await makePage(url,false,[completedComputerTemplateTask]);
    await complete.page.setViewportSize({width:1440,height:1000});
    await snapshot(complete.page,`${prefix}-completed`);
    if(prefix==='after') {
      assert.equal(await complete.page.getByTestId('home-result-timeline-card').isVisible(),true);
      assert.equal(await complete.page.getByTestId('home-templates').getAttribute('open'),null);
      assert.equal(await complete.page.getByTestId('home-more').getAttribute('open'),null);
      assert.equal(await complete.page.locator('.result-phase-details').getAttribute('open'),null);
      assert.equal(await complete.page.locator('.result-timeline-card__action').isVisible(),true);
      await complete.page.getByRole('button',{name:'进度',exact:true}).click();
      await complete.page.getByText('任务成果物',{exact:true}).waitFor();
      assert.equal(await complete.page.getByText('任务成果物',{exact:true}).isVisible(),true);
      await snapshot(complete.page,'after-progress');await audit(complete.page,'progress');
      const routes = [
        ['首页','首页'], ['对话','对话'], ['进度','进度'], ['审批','审批'],
        ['管家','AI 管家'], ['文件工具','文件工具'], ['知识库','文档'], ['图库','图库'],
        ['此电脑','此电脑'], ['技能','技能'], ['记忆','记忆'], ['浏览器','浏览器监看'], ['设置','设置']
      ];
      for(const [name, title] of routes) {
        await navigate(complete.page, name);
        await complete.page.locator('.window-bar').getByRole('heading',{name:title,exact:true}).waitFor();
        await complete.page.locator('.route-loading').waitFor({state:'hidden'});
        assert.equal(await complete.page.locator('.route-load-failure').count(),0, `${name} route should load`);
        if (['审批','文件工具','设置'].includes(name)) await audit(complete.page,name);
      }
      report.scenarios.push({scene:'all 13 retained navigation routes open the correct heading without a route-load failure',passed:true});
      report.performance.push({environment:'local production preview, fixture API, Chromium, single run',...await complete.page.evaluate(()=>window.reviewVitals)});
    }
    await complete.context.close();
  }
  const idle = await makePage(current);
  const navToggle = idle.page.getByTestId('nav-tools-toggle');
  const navPanel = idle.page.locator('#nav-tools-panel');
  for (const [size, width, height] of [sizes[0], sizes[2]]) {
    await idle.page.setViewportSize({width,height});
    await snapshot(idle.page, `after-home-${size}`);
    assert.equal(await idle.page.locator('.primary-nav > button').count(), 5);
    assert.equal(await navPanel.isVisible(), false);
    await audit(idle.page, `home-online-${size}`);
  }
  await navToggle.click();
  assert.equal(await navPanel.isVisible(), true);
  assert.equal(await idle.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1), false);
  await snapshot(idle.page, 'after-navigation-mobile');
  await audit(idle.page, 'navigation-mobile-expanded');
  await idle.page.setViewportSize({width:320,height:740});
  assert.equal(await idle.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1), false);
  await snapshot(idle.page, 'after-navigation-narrow');
  await idle.page.setViewportSize({width:390,height:844});
  await idle.page.keyboard.press('Tab');
  await idle.page.keyboard.press('Escape');
  assert.equal(await navPanel.isVisible(), false);
  assert.equal(await navToggle.evaluate(el=>el===document.activeElement), true);
  await idle.page.keyboard.press('Enter');
  for (let tab = 0; tab < 12; tab++) {
    await idle.page.keyboard.press('Tab');
    if (await idle.page.getByRole('button',{name:'设置',exact:true}).evaluate(el=>el===document.activeElement)) break;
  }
  assert.equal(await idle.page.getByRole('button',{name:'设置',exact:true}).evaluate(el=>el===document.activeElement), true);
  await idle.page.keyboard.press('Enter');
  await idle.page.locator('.window-bar h1').filter({hasText:'设置'}).waitFor();
  assert.equal(await navPanel.isVisible(), false);
  assert.equal(await navToggle.getAttribute('aria-current'), 'page');
  assert.equal(await navToggle.evaluate(el=>el===document.activeElement), true);
  assert.match(await navToggle.innerText(), /设置/);
  report.scenarios.push({scene:'four primary routes plus tools; 390px and 320px expanded tools fit; Escape restores focus; keyboard settings selection closes tools and identifies current page',passed:true});
  await idle.context.close();

  const runningTask = {
    id:'ui-review-running-task', user_goal:'整理测试资料', final_summary:'正在读取已授权的测试目录。', status:'running',
    created_at:new Date(Date.now()-600000).toISOString(), updated_at:new Date(Date.now()-300000).toISOString()
  };
  const mixed = await makePage(current,false,[runningTask, {...completedComputerTemplateTask, updated_at:new Date().toISOString()}]);
  await mixed.page.setViewportSize({width:1440,height:1000});
  const currentTaskCard = mixed.page.getByTestId('home-result-timeline-card');
  assert.equal(await currentTaskCard.getByText('整理测试资料',{exact:true}).isVisible(), true);
  assert.equal(await currentTaskCard.getByText('正在处理',{exact:true}).isVisible(), true);
  assert.equal(await currentTaskCard.getByText('完成结果已核验',{exact:true}).isVisible(), false);
  await snapshot(mixed.page,'after-current-task');
  await audit(mixed.page,'current-running-task-before-newer-completion');
  report.scenarios.push({scene:'older running task remains the home priority when another task has completed more recently',passed:true});
  await mixed.context.close();

  const approval = {id:'ui-review-approval',task_id:'ui-review-task',approval_type:'tool_execution',
    message:'测试预览：删除示例文件前需要你的确认。',diff_preview:{operation:'delete',path:'D:/Review/example.txt'},
    tool_name:'delete_file',risk_level:'R3_DESTRUCTIVE_OR_SYSTEM',tool_effects:['delete'],resource_kinds:['file'],
    dry_run_summary:'仅为界面测试，不会操作真实文件。',status:'pending',created_at:new Date().toISOString(),
    expires_at:new Date(Date.now()+600000).toISOString()};
  const review=await makePage(current,false,[],async page=>{
    await page.route('**/api/approvals/pending',route=>route.fulfill({json:[approval]}));
    await page.addInitScript(() => {
      window.reviewApprovalDecisions = 0;
      window.lengrvis.approvals = {
        approve: async () => {
          window.reviewApprovalDecisions++;
          await new Promise(resolve=>setTimeout(resolve,200));
          return {ok:false,status:503,receivedAt:new Date().toISOString(),error:{message:'测试故障：审批尚未提交，请重试。'}};
        },
        reject: async () => {throw new Error('Not exercised');}
      };
    });
  });
  const {page}=review;
  await page.setViewportSize({width:1440,height:1000});
  assert.equal(await page.locator('.home-attention').isVisible(),true);
  assert.equal(await page.getByRole('button',{name:'查看审批',exact:true}).isVisible(),true);
  await page.locator('.home-attention').getByRole('button',{name:'查看审批',exact:true}).click();
  await page.getByLabel('审批备注').fill('请先核对范围，保留这条备注');
  assert.equal(await page.locator('.approval-tech-details').getAttribute('open'),'');
  await snapshot(page,'after-approval');await audit(page,'high-risk-approval');
  await page.getByRole('button',{name:'批准',exact:true}).dblclick();
  await page.getByRole('alert').filter({hasText:'测试故障'}).waitFor();
  assert.equal(await page.evaluate(()=>window.reviewApprovalDecisions),1);
  assert.equal(await page.getByLabel('审批备注').inputValue(),'请先核对范围，保留这条备注');
  await snapshot(page,'after-approval-failure');
  await page.setViewportSize({width:390,height:844});
  await snapshot(page,'after-approval-mobile');await audit(page,'high-risk-approval-mobile');
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await page.getByRole('button',{name:'有待审批项目'}).click();
  assert.equal(await page.getByLabel('审批备注').inputValue(),'请先核对范围，保留这条备注');
  report.scenarios.push({scene:'fixture high-risk boundary visible; approval double click once; failure/cancel/reopen preserves note',passed:true});
  await review.context.close();
  let releaseReply;
  const delayedReply = new Promise(resolve=>{releaseReply=resolve;});
  const pending = await makePage(current,false,[],async page=>{
    await page.route('**/api/chat',async route=>{
      await delayedReply;
      await route.fulfill({json:{status:'completed',message:'测试回复已到达，没有执行文件操作。',delegated:false}});
    });
  });
  await pending.page.locator('#office-command-input').fill('测试：查看文件建议');
  await Promise.all([
    pending.page.waitForRequest(request=>new URL(request.url()).pathname==='/api/chat' && request.method()==='POST'),
    pending.page.locator('.command-footer__send').click()
  ]);
  await navigate(pending.page, '设置');
  const completedResponse=pending.page.waitForResponse(response=>new URL(response.url()).pathname==='/api/chat');
  releaseReply();await completedResponse;
  await pending.page.waitForFunction(()=>sessionStorage.getItem('lengrvis.draft.home')===null);
  assert.equal(await pending.page.locator('.window-bar h1').innerText(),'设置');
  report.scenarios.push({scene:'delayed successful reply does not interrupt user navigation to settings',passed:true});
  await pending.context.close();
}
main().catch(error=>{report.errors.push(error.stack);process.exitCode=1;}).finally(async ()=>{
 await Promise.all(sessions.map(s=>s.context.close().catch(()=>{})));
 if(report.accessibility.some(a=>a.violations.length))process.exitCode=1;
 fs.writeFileSync(path.join(output,'validation/refined-ui-review.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({scenarios:report.scenarios,accessibility:report.accessibility.map(a=>({scene:a.scene,violations:a.violations.length})),performance:report.performance,errors:report.errors},null,2));
});
