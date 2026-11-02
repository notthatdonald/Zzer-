#!/usr/bin/env node
/*
 * ZZER (只二) handbag scraper.
 *
 * zzer.com's shop is the mobile web app served from mix.goshare2.com. Listings
 * are fetched by that page from gwapi-v2.goshare2.com and every request is
 * signed client-side. Instead of re-implementing the signing, this script opens
 * the public search page in a real (headless) Chromium with a phone profile and
 * calls the page's own HTTP client ($gwapiHttp) – i.e. exactly the requests a
 * visitor's browser makes when scrolling the Bags category.
 *
 * Output (one folder per run date):
 *   data/snapshots/<date>/handbags.jsonl   raw product records + labels
 *   data/snapshots/<date>/facets.json      site-reported filter counts
 *
 * Usage:
 *   node scraper/scrape.js                       # main crawl + default enrichments
 *   node scraper/scrape.js --enrich=gender,color,location,collection
 *   node scraper/scrape.js --enrich=none --delay=800
 *   node scraper/scrape.js --limit-pages=2       # quick smoke test
 *   node scraper/scrape.js --labels-only --enrich=collection   # add model names to today's snapshot
 *   node scraper/scrape.js --sweep               # also page the whole category (catches uncategorised items)
 */
const fs = require('fs');
const path = require('path');
let playwright;
try { playwright = require('playwright'); } catch (e) { playwright = require('/opt/node-tools/node_modules/playwright'); }
const { chromium, devices } = playwright;

const args = Object.fromEntries(process.argv.slice(2).map(a => {
  const [k, v] = a.replace(/^--/, '').split('=');
  return [k, v === undefined ? true : v];
}));
const DELAY = Number(args.delay || 600);           // ms between requests
const PAGE_SIZE = Number(args.size || 100);
const LIMIT_PAGES = args['limit-pages'] ? Number(args['limit-pages']) : Infinity;
const ENRICH = (args.enrich || 'gender,color,location').split(',').filter(x => x && x !== 'none');
const DATE = args.date || new Date().toISOString().slice(0, 10);
const OUT = path.resolve(__dirname, '..', 'data', 'snapshots', DATE);

const BAGS = '10:78;';
// filter tab id on the site -> label column we write
const ENRICH_TABS = { gender: '315', color: '330', location: '327', collection: '101', style: '102' };
const SEARCH_URL = 'https://mix.goshare2.com/wv/home/search/result/?ppath=' + encodeURIComponent(BAGS);

const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function openPage(browser) {
  const ctx = await browser.newContext({ ...devices['iPhone 13'], locale: 'en-US' });
  const page = await ctx.newPage();
  await page.goto(SEARCH_URL, { waitUntil: 'networkidle', timeout: 90000 });
  await page.waitForFunction(() => document.querySelector('#app') && document.querySelector('#app').__vue__ && document.querySelector('#app').__vue__.$gwapiHttp, null, { timeout: 60000 });
  return { ctx, page };
}

function makeSearch(state) {
  return async function search(params, attempt = 0) {
    try {
      const data = await state.page.evaluate(async (p) => {
        const http = document.querySelector('#app').__vue__.$gwapiHttp;
        const body = Object.assign({ key: '', page: 1, size: 20, sort: '', sortRule: '', putTypeList: '1', searchTab: 0,
          isLatestVersion: true, filterSwitch: '0', filter: '0', containImage: '', defaultFilter: '', fastFilters: '', warehouseCode: '' }, p);
        const r = await http.post('/esbiz/api/v1/product/searchList', body);
        return r.data;
      }, params);
      await sleep(DELAY);
      return data;
    } catch (e) {
      if (attempt >= 5) throw e;
      const wait = 2000 * 2 ** attempt;
      log(`  request failed (${e.message.split('\n')[0]}), retry in ${wait / 1000}s`);
      await sleep(wait);
      if (attempt >= 1) { // refresh the page/session
        try { await state.ctx.close(); } catch (_) {}
        Object.assign(state, await openPage(state.browser));
      }
      return search(params, attempt + 1);
    }
  };
}

// Page through every listing matching ppath; calls onItem(product) for each.
async function crawl(search, ppath, onItem, label) {
  const first = await search({ ppath, page: 1, size: PAGE_SIZE });
  const total = first.count || 0;
  const pages = Math.min(Math.ceil(total / PAGE_SIZE), LIMIT_PAGES);
  let seen = 0;
  const handle = d => (d.list || []).forEach(x => { if (x && x.product) { seen++; onItem(x.product); } });
  handle(first);
  for (let pg = 2; pg <= pages; pg++) {
    const d = await search({ ppath, page: pg, size: PAGE_SIZE, dataTime: first.dataTime });
    if (!d.list || d.list.length === 0) break;
    handle(d);
    if (pg % 10 === 0) log(`  ${label}: page ${pg}/${pages}`);
  }
  return { total, seen };
}

const flatTabs = t => (t.allTabs ? t.allTabs.flatMap(g => g.tabs || []) : (t.tabs || []));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const state = { browser, ...(await openPage(browser)) };
  const search = makeSearch(state);

  // 1. Facet snapshot (site-reported counts for every filter).
  log('Fetching facet counts…');
  const root = await search({ ppath: BAGS, page: 1, size: 1, filterSwitch: '1' });
  const facets = { date: DATE, total: root.count, tabs: root.tabs, fastTabs: root.fastTabs };
  fs.writeFileSync(path.join(OUT, 'facets.json'), JSON.stringify(facets, null, 1));
  log(`Bags total reported by site: ${root.count}`);

  // 2. Main crawl, partitioned by sub-category (labels each item).
  const catTab = (root.fastTabs || []).concat(root.tabs || []).find(t => String(t.id) === '5');
  const subcats = flatTabs(catTab);
  const items = new Map();
  const snapFile = path.join(OUT, 'handbags.jsonl');
  const labelsOnly = !!args['labels-only'];
  if (labelsOnly) {
    // Add labels to an existing snapshot without re-crawling it.
    for (const line of fs.readFileSync(snapFile, 'utf8').split('\n')) if (line.trim()) { const it = JSON.parse(line); items.set(it.id, it); }
    log(`Loaded ${items.size} handbags from ${snapFile}`);
  }
  for (const sc of labelsOnly ? [] : subcats) {
    log(`Sub-category ${sc.name} (${sc.count})`);
    const r = await crawl(search, `${BAGS}5:${sc.id};`, p => {
      const prev = items.get(p.id);
      if (prev) { prev.subcategories = [...new Set([...prev.subcategories, sc.name])]; return; }
      items.set(p.id, { ...p, subcategory: sc.name, subcategoryId: sc.id, subcategories: [sc.name] });
    }, sc.name);
    log(`  got ${r.seen} of ${r.total}; unique so far ${items.size}`);
  }
  // Catch anything not in a sub-category.
  if (args.sweep && !labelsOnly) {
    log('Sweep of whole category for uncategorised items…');
    const r = await crawl(search, BAGS, p => { if (!items.has(p.id)) items.set(p.id, { ...p, subcategory: '', subcategoryId: '', subcategories: [] }); }, 'all bags');
    log(`  sweep saw ${r.seen}; unique ${items.size}`);
  }

  // 3. Enrichment: label items by filter value (ID-only use of the results).
  for (const key of ENRICH) {
    const tabId = ENRICH_TABS[key];
    const tab = (root.tabs || []).find(t => String(t.id) === tabId);
    if (!tab) { log(`No filter tab for ${key}, skipping`); continue; }
    const values = flatTabs(tab).filter(v => v.count > 0);
    log(`Enrichment ${key}: ${values.length} values`);
    for (const it of items.values()) delete it[key];
    for (const v of values) {
      await crawl(search, `${BAGS}${tabId}:${v.id};`, p => {
        const it = items.get(p.id);
        if (!it) return;
        it[key] = it[key] ? (it[key].split(' | ').includes(v.name) ? it[key] : it[key] + ' | ' + v.name) : v.name;
      }, `${key}=${v.name}`);
    }
  }

  const scrapedAt = new Date().toISOString();
  const lines = [...items.values()].map(it => JSON.stringify({ scrapedAt, ...it }));
  fs.writeFileSync(snapFile + '.tmp', lines.join('\n') + '\n');
  fs.renameSync(snapFile + '.tmp', snapFile);
  log(`Wrote ${lines.length} handbags to ${OUT}`);
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
