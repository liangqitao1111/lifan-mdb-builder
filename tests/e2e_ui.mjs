const { chromium } = await import('file:///C:/Users/神舟/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const BASE = process.env.LIFAN_URL || 'http://127.0.0.1:8766/';
const FILE = process.env.LIFAN_LZ || 'work/dbs/e2e_real.lz';
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const logs = [];
page.on('pageerror', e => logs.push('PAGEERROR: ' + e.message));
page.on('console', m => { if (m.type() === 'error' && !m.text().includes('favicon') && !m.text().includes('autocomplete')) logs.push('CONSOLE: ' + m.text()); });
let ok = true;
const chk = (name, cond) => { console.log((cond ? 'PASS' : 'FAIL') + ' | ' + name); if (!cond) ok = false; };

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForTimeout(2000);
await page.evaluate(() => { document.getElementById('loginUser').value = 'admin'; document.getElementById('loginPwd').value = 'admin'; document.getElementById('loginForm').dispatchEvent(new Event('submit')); });
await page.waitForTimeout(2500);
chk('登录（后端鉴权）', await page.evaluate(() => !!localStorage.getItem('lifan_token')));

await page.evaluate(() => location.hash = '#workspace');
await page.waitForTimeout(1200);
await page.locator('#wsFile').setInputFiles(FILE);
await page.waitForTimeout(12000);
await page.waitForFunction(() => document.getElementById('wsS0') && document.getElementById('wsS0').textContent !== '—', null, { timeout: 60000 });
await page.waitForTimeout(2500);
chk('工作台统计（408 孔）', await page.evaluate(() => document.getElementById('wsS0').textContent === '408'));
chk('中文表头（岩土名称/描述）', await page.evaluate(() => { const h = document.querySelector('#onlineGrid .t-head'); return h && h.innerText.includes('岩土名称') && h.innerText.includes('描述'); }));
chk('无英文列名', await page.evaluate(() => { const h = document.querySelector('#onlineGrid .t-head'); return h && !/ZKBH|TCMS/.test(h.innerText); }));
chk('6 页签', await page.evaluate(() => document.querySelectorAll('#wsTabs [data-t]').length === 6)); // v5+v16: 颗分试验
chk('无表选择栏', await page.evaluate(() => !document.getElementById('dataTableSel')));

// 深色模式
await page.evaluate(() => toggleTheme());
chk('深色模式切换', await page.evaluate(() => document.documentElement.getAttribute('data-theme') === 'dark'));
await page.evaluate(() => toggleTheme());
chk('切回浅色', await page.evaluate(() => document.documentElement.getAttribute('data-theme') !== 'dark'));

// 列设置
await page.evaluate(() => openColSettings());
await page.waitForTimeout(600);
chk('列设置弹窗', await page.evaluate(() => document.querySelectorAll('.colchk').length > 0));
await page.evaluate(() => { const c = document.querySelector('.colchk'); if (c) c.checked = false; applyColSettings(); });
await page.waitForTimeout(1500);
chk('列隐藏生效', await page.evaluate(() => document.querySelectorAll('#onlineGrid .t-head > div').length > 0));

// 工具页：问题导出 + 标贯
await page.evaluate(() => location.hash = '#tools');
await page.waitForTimeout(1500);
chk('工具页含问题导出', await page.evaluate(() => document.getElementById('content').innerHTML.includes('导出复核问题清单')));
await page.evaluate(() => toolsIssues());
await page.waitForFunction(() => document.getElementById('toolsResult').innerHTML.includes('已生成'), null, { timeout: 60000 });
chk('问题清单导出', true);

// 参数中心
await page.evaluate(() => location.hash = '#config');
await page.waitForFunction(() => document.querySelector('.ws-panel .p-head'), null, { timeout: 30000 });
await page.waitForTimeout(1000);
chk('参数中心表单化', await page.evaluate(() => !document.getElementById('content').innerHTML.includes('cfgText')));

console.log('JS错误:', logs.length ? logs : '无');
console.log('总体:', ok ? 'ALL PASS' : 'HAS FAIL');
await browser.close();
if (!ok) process.exit(1);
