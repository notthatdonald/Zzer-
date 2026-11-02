#!/usr/bin/env node
/*
 * Fetch product detail pages for handbags found by scrape.js.
 * Adds collection/model, material, measurements, weight, flaws, accessories,
 * seller grade, bargain floor, brand id, etc.
 *
 * Details are cached in data/details/details.jsonl (one JSON object per line,
 * keyed by product id) so re-runs only fetch new listings. Safe to stop and
 * restart at any time.
 *
 * Usage:
 *   node scraper/details.js                    # latest snapshot
 *   node scraper/details.js --date=2026-11-02 --delay=500 --max=5000
 */
const fs = require('fs');
const path = require('path');
const readline = require('readline');
let playwright;
try { playwright = require('playwright'); } catch (e) { playwright = require('/opt/node-tools/node_modules/playwright'); }
const { chromium, devices } = playwright;

const args = Object.fromEntries(process.argv.slice(2).map(a => { const [k, v] = a.replace(/^--/, '').split('='); return [k, v === undefined ? true : v]; }));
const DELAY = Number(args.delay || 500);
const MAX = args.max ? Number(args.max) : Infinity;
const DATA = path.resolve(__dirname, '..', 'data');
const SNAP = path.join(DATA, 'snapshots');
const date = args.date || fs.readdirSync(SNAP).filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d)).sort().pop();
const CACHE = path.join(DATA, 'details', 'details.jsonl');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const URL = 'https://mix.goshare2.com/wv/home/search/result/?ppath=' + encodeURIComponent('10:78;');

async function readIds(file, field = 'id') {
  const ids = new Set();
  if (!fs.existsSync(file)) return ids;
  const rl = readline.createInterface({ input: fs.createReadStream(file) });
  for await (const line of rl) { if (line.trim()) { try { ids.add(JSON.parse(line)[field]); } catch (_) {} } }
  return ids;
}

// Keep only the fields worth storing (the raw response is ~30KB/item).
function slim(id, d) {
  const det = d.detail || {};
  const rep = det.identifyReportModuleV2 || {};
  const attrs = {};
  for (const a of d.productAttrV2 || []) {
    attrs[a.nameEn || a.name] = (a.values || []).map(v => v.value).join(' | ');
    if (a.fieldId === 'proc_tail_size_bag') for (const m of a.measurementValues || []) attrs['m_' + m.name] = m.value;
  }
  return {
    id, fetchedAt: new Date().toISOString(),
    sku: det.sku, name: det.name, brandId: det.brandId, brand: det.brand,
    categoryId: det.categoryId, categoryTwo: det.categoryTwo,
    status: det.status, stock: det.stock,
    price: Number(det.price), retailPrice: Number(det.originalPrice), discountZhe: det.discount,
    getPrice: det.getOffer && det.getOffer.getPrice, couponAmount: det.getOffer && det.getOffer.couponAmount,
    historyMaxPrice: det.historyMaxPrice, bargainBasePrice: det.bargainBasePrice, openBargain: det.openBargain,
    hideMarketPrice: det.hideMarketPrice,
    degree: det.degreeDesc, degreeExtEn: det.degreeExtEn,
    flaws: (det.flawInfo || []).filter(f => f.flawName).map(f => `${f.positionName}:${f.flawName}`),
    accessories: rep.attrAccessoryNamesEn && rep.attrAccessoryNamesEn.length ? rep.attrAccessoryNamesEn : (rep.attrAccessoryNames || []),
    maintenance: (rep.maintain || []).filter(m => (m.namesEn || m.names || []).length).map(m => `${m.typeEn || m.type}: ${(m.namesEn && m.namesEn.length ? m.namesEn : m.names).join(', ')}`),
    inspectedOn: rep.time, imageCount: (det.imageList || []).length,
    productSource: det.productSource, sellerId: det.sellerId,
    sellerGrade: d.seller && d.seller.gradeDesc, sellerOnSale: d.seller && d.seller.onSellNumber,
    brandConsignName: d.brand && d.brand.consignName, brandOnSaleTotal: d.brand && d.brand.onSaleNum,
    spuId: det.newSpuId, snId: det.newSnId,
    attrs,
  };
}

(async () => {
  const snapFile = path.join(SNAP, date, 'handbags.jsonl');
  const want = await readIds(snapFile);
  const have = await readIds(CACHE);
  const todo = [...want].filter(id => !have.has(id)).slice(0, MAX);
  log(`Snapshot ${date}: ${want.size} items, ${have.size} cached, ${todo.length} to fetch`);
  fs.mkdirSync(path.dirname(CACHE), { recursive: true });
  const out = fs.createWriteStream(CACHE, { flags: 'a' });

  const browser = await chromium.launch();
  let ctx, page;
  const open = async () => {
    if (ctx) await ctx.close().catch(() => {});
    ctx = await browser.newContext({ ...devices['iPhone 13'], locale: 'en-US' });
    page = await ctx.newPage();
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 90000 });
    await page.waitForFunction(() => document.querySelector('#app') && document.querySelector('#app').__vue__ && document.querySelector('#app').__vue__.$gwapiHttp, null, { timeout: 60000 });
  };
  await open();

  let done = 0, failed = 0;
  for (const id of todo) {
    let ok = false;
    for (let attempt = 0; attempt < 4 && !ok; attempt++) {
      try {
        const d = await page.evaluate(async (id) => {
          const r = await document.querySelector('#app').__vue__.$gwapiHttp.get('/product/api/v1/product/detail', { params: { id } });
          return r.data;
        }, id);
        if (d && d.detail) out.write(JSON.stringify(slim(id, d)) + '\n');
        else out.write(JSON.stringify({ id, fetchedAt: new Date().toISOString(), unavailable: true }) + '\n');
        ok = true;
      } catch (e) {
        const msg = e.message.split('\n')[0];
        if (/下架|不存在|sold|not exist/i.test(msg)) { out.write(JSON.stringify({ id, fetchedAt: new Date().toISOString(), unavailable: true, error: msg }) + '\n'); ok = true; break; }
        log(`  ${id} failed (${msg}); retry`);
        await sleep(3000 * 2 ** attempt);
        if (attempt >= 1) await open().catch(() => {});
      }
    }
    if (!ok) failed++;
    done++;
    if (done % 200 === 0) log(`  ${done}/${todo.length} (failed ${failed})`);
    await sleep(DELAY);
  }
  out.end();
  log(`Done: ${done} fetched, ${failed} failed`);
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
