"use strict";
// Car Hunter extension: when YOU click it, reads the advert in the current tab (nothing runs otherwise,
// nothing is sent anywhere) and can open your dashboard's "Add car" form filled in with what it found.
const DASHBOARD = "https://kevamid.github.io/Car-Hunter/";

/* Runs inside the advert page (injected on click). Must be self-contained: no outside variables.
   Order of trust: the page's structured listing data (schema.org JSON-LD, Open Graph), then its text. */
async function extractAdvert() {
  const ld = [];
  const walk = o => {
    if (!o || typeof o !== "object") return;
    if (Array.isArray(o)) { o.forEach(walk); return; }
    ld.push(o);
    ["@graph", "itemOffered", "mainEntity", "about"].forEach(k => o[k] && walk(o[k]));
  };
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => { try { walk(JSON.parse(s.textContent)); } catch (e) {} });
  const typeIs = (o, re) => [].concat(o["@type"] || []).some(t => re.test(String(t)));
  const car = ld.find(o => typeIs(o, /^(Car|Vehicle|Motorcycle)$/)) || ld.find(o => typeIs(o, /^(Product|IndividualProduct)$/)) || {};
  const offer = [].concat(car.offers || ld.find(o => typeIs(o, /Offer$/)) || [])[0] || {};
  const val = v => v && typeof v === "object" ? (v.value ?? v["@value"] ?? v.name) : v;
  const num = s => { const n = Number(String(s ?? "").replace(/[^\d.]/g, "")); return isFinite(n) && n > 0 ? Math.round(n) : null; };
  const meta = n => { const m = document.querySelector(`meta[property="${n}"], meta[name="${n}"], meta[itemprop="${n}"]`); return m ? m.content : null; };
  const yearIn = s => { const m = String(s ?? "").match(/\b(19[5-9]\d|20[0-4]\d)\b/); return m ? Number(m[1]) : null; };

  const clean = s => String(s || "").replace(/\s+/g, " ").trim();
  const visible = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden";
  // the advert: the topmost open overlay (e.g. a Facebook Marketplace item opened from search), else the main area
  const dialogs = [...document.querySelectorAll('[role=dialog], [aria-modal="true"]')]
    .filter(d => visible(d) && (d.innerText || "").length > 200);
  const root = dialogs[dialogs.length - 1] || document.querySelector("main, article, [role=main]") || document.body;
  const generic = /^(home|marketplace|notifications?|chats?|messages|menu|search( results)?|facebook|browse all|inbox|today's picks)$/i;
  const headingsIn = el => [...el.querySelectorAll('h1, [role=heading][aria-level="1"]')].filter(visible).map(h => clean(h.innerText))
    .filter(t => t.length >= 4 && t.length <= 150 && !generic.test(t));
  const headings = headingsIn(root).length ? headingsIn(root) : headingsIn(document);
  const tabTitle = clean(document.title).replace(/^\(\d+\)\s*/, "").replace(/^Marketplace\s*[–-]\s*/i, "")
    .replace(/\s*[|–-]\s*Facebook( Marketplace)?$/i, "");
  const title = clean(car.name || headings[0] || meta("og:title") || (generic.test(tabTitle) ? "" : tabTitle));
  // descriptions cut short behind "See more" (Facebook): open them, inside the advert only, then read
  const more = [...root.querySelectorAll('[role=button], button, span, a')].filter(e => /^(see|show|read) more$/i.test(clean(e.innerText)) && visible(e));
  if (more.length) { more.slice(0, 3).forEach(e => e.click()); await new Promise(r => setTimeout(r, 400)); }
  const text = (root.innerText || "").slice(0, 40000);
  const at = title ? text.indexOf(title) : -1;
  const after = at >= 0 ? text.slice(at) : text;  // the advert's own details come after its title

  // price: structured first; else the first £ amount after the title that isn't finance ("£199 per month", "deposit")
  let price = num(offer.price) || num(val(offer.priceSpecification && offer.priceSpecification.price)) ||
              num(meta("product:price:amount")) || num(meta("og:price:amount")) || num(meta("price"));
  const PRICE = /£\s?(\d{1,3}(?:,\d{3})+|\d{3,6})(?!\.\d)(.{0,24})/g;
  if (!price) {
    for (const m of [...after.matchAll(PRICE), ...text.matchAll(PRICE)]) {
      if (/month|p\/?m\b|pcm|deposit|per week|finance|apr/i.test(m[2])) continue;
      if (num(m[1]) >= 300) { price = num(m[1]); break; }
    }
  }
  let mileage = num(val(car.mileageFromOdometer)), mileageNote = null;
  if (mileage && /^(KMT|km)$/i.test(String((car.mileageFromOdometer || {}).unitCode || ""))) mileage = Math.round(mileage * 0.621371);
  if (!mileage) {
    const m = after.match(/(\d{1,3}(?:,\d{3})+|\d{1,6})\s*(k)?\s*(miles|mi|km|kms|kilometres|kilometers)\b/i);
    if (m) {
      mileage = m[2] ? Math.round(Number(m[1].replace(/,/g, "")) * 1000) : num(m[1]);
      // Facebook labels every UK car's mileage "km" ("Driven 143,000 km") but it's really miles: keep it as is
      if (/^k/i.test(m[3]) && !/(^|\.)facebook\.com$/i.test(window.location.hostname)) {
        mileageNote = `The advert says ${m[0]}: converted to miles`;
        mileage = Math.round(mileage * 0.621371);
      }
    }
  }
  const year = yearIn(car.vehicleModelDate || car.modelDate || car.productionDate || car.dateVehicleFirstRegistered) ||
               yearIn(title) || yearIn((text.match(/\b(?:year|registered|first registration)\b[^\n\d]{0,20}\d{4}/i) || [])[0]);
  const pick = (structured, re) => {
    const s = val(structured); if (s) return String(s).replace(/^https?:\/\/schema\.org\//, "");
    const m = after.match(re); return m ? m[1][0].toUpperCase() + m[1].slice(1).toLowerCase() : null;
  };
  const transmission = pick(car.vehicleTransmission, /\b(manual|automatic|semi-auto)\b/i);
  const fuel = pick(car.fuelType, /\b(petrol|diesel|hybrid|electric)\b/i);
  // UK plates in the current format (AB12 CDE), only if written on the page
  const regs = [...new Set([...text.matchAll(/\b([A-Z]{2}\d{2}) ?([A-Z]{3})\b/g)].map(m => m[1] + " " + m[2]))];

  const place = offer.availableAtOrFrom || (offer.seller && offer.seller.address) || (car.offers && car.offers.seller && car.offers.seller.address) || {};
  const addr = place.address || place;
  // the seller's text: structured; else the page's description section; else everything after the title
  const describe = () => {
    const tidy = s => String(s || "").split("\n").map(l => l.trim()).filter(Boolean).join("\n").replace(/\n(see|show|read) less$/i, "").trim();
    if (clean(car.description)) return tidy(car.description).slice(0, 4000);
    const stop = /\s(see|show|read) less\b|\n[^\n]*location is approximate|\n\s*(seller information|seller details|meet the seller|message (the )?seller|send seller a message|send a message|similar (cars|items|listings|adverts)|today's picks|report (this )?(listing|advert|ad)|safety tips|sponsored)\b/i;
    for (const marker of [/(?:^|\n)\s*(seller'?s description|vehicle description|description|the car)\s*\n/i, /(?:^|\n)\s*(about this (car|vehicle)|overview|key (details|features))\s*\n/i]) {
      const m = after.match(marker); if (!m) continue;
      const body = after.slice(m.index + m[0].length); const end = body.search(stop);
      const textOut = tidy(end >= 0 ? body.slice(0, end) : body);
      if (textOut.length > 30) return textOut.slice(0, 4000);
    }
    if (clean(meta("og:description")).length > 40) return tidy(meta("og:description")).slice(0, 4000);
    const end = after.search(stop);
    return tidy(end >= 0 ? after.slice(0, end) : after).slice(0, 4000);
  };
  const listedIn = after.match(/\bListed .{0,40}? in ([A-Z][\w' .-]{1,40}?)\s*(?:\n|,|$)/);  // Facebook: "Listed 3 days ago in Leeds"
  const where = (typeof addr === "string" ? addr : addr.addressLocality || addr.addressRegion || null) || (listedIn ? listedIn[1] : null);

  // photos: structured, Open Graph, then big pictures on the page (not logos/icons)
  const images = [];
  const add = u => { u = val(u) || (u && (u.url || u.contentUrl)); if (typeof u !== "string") return;
    try { u = new URL(u, window.location.href).href; } catch (e) { return; }
    if (/^https:/.test(u) && !/\.svg(\?|$)|logo|icon|sprite|avatar/i.test(u) && !images.includes(u)) images.push(u); };
  [].concat(car.image || []).forEach(add);
  add(meta("og:image"));
  const pics = root.querySelectorAll("img").length ? root.querySelectorAll("img") : document.querySelectorAll("img");
  pics.forEach(img => { if (img.naturalWidth >= 400 && img.naturalHeight >= 250) add(img.currentSrc || img.src); });

  return {
    url: window.location.href, site: window.location.hostname.replace(/^www\./, ""), title, price, mileage, year, transmission, fuel,
    regs, location: where, description: describe(), mileageNote,
    images: images.slice(0, 25), structured: Boolean(car.name || offer.price),
    debug: { area: dialogs.length ? "overlay" : root === document.body ? "body" : root.tagName.toLowerCase(),
             tab: clean(document.title), og: meta("og:title"), headings: headings.slice(0, 8), structuredBlocks: ld.length,
             text: after.slice(0, 1500) },
  };
}

/* ---------- popup ---------- */
const out = document.getElementById("out");
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const say = (msg, cls = "warn") => { out.replaceChildren(el("div", cls, msg)); };

function encode(obj) {  // URL-safe base64 of UTF-8 JSON, read by the dashboard
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  let bin = ""; bytes.forEach(b => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function summary(a, reg) {
  return [a.title, a.price && `£${a.price.toLocaleString("en-GB")}`, a.mileage && `${a.mileage.toLocaleString("en-GB")} miles`,
          a.year, a.transmission, a.fuel, reg && `Reg ${reg}`, a.location, a.url].filter(Boolean).join("\n");
}

function render(a) {
  const reg = a.regs.length === 1 ? a.regs[0] : null;
  const nodes = [el("h1", null, `Car Hunter · ${a.site}`)];
  if (a.images[0]) {
    const img = el("img", "photo"); img.src = a.images[0]; img.alt = ""; img.referrerPolicy = "no-referrer"; nodes.push(img);
  }
  nodes.push(el("div", "price", a.price ? `£${a.price.toLocaleString("en-GB")}` : "No price found"), el("div", "title", a.title || "No title found"));
  const facts = el("div", "facts");
  [["", a.year], ["", a.mileage && `${a.mileage.toLocaleString("en-GB")} miles`], ["", a.transmission], ["", a.fuel], ["", a.location],
   ["Photos ", a.images.length]].forEach(([k, v]) => { if (v) { const s = el("span", null, k); s.append(el("b", null, String(v))); facts.append(s); } });
  if (reg) { const s = el("span", null, "Reg "); s.append(el("span", "reg", reg)); facts.append(s); }
  nodes.push(facts);
  if (a.mileageNote) nodes.push(el("div", "warn", a.mileageNote));
  if (a.regs.length > 1) nodes.push(el("div", "muted", `Possible registrations on the page: ${a.regs.slice(0, 4).join(", ")}`));
  if (!a.structured) nodes.push(el("div", "muted", "This site gives no structured listing data, so these were read from the page's text: check them in the form."));

  const actions = el("div", "actions");
  const addBtn = el("button", "primary", "Add to my dashboard");
  addBtn.onclick = () => {
    const prefill = { link: a.url, title: a.title, price: a.price, mileage: a.mileage, year: a.year, transmission: a.transmission,
                      fuel: a.fuel, location: a.location, reg, images: a.images.slice(0, 20), description: a.description,
                      note: a.mileageNote };
    chrome.tabs.create({ url: `${DASHBOARD}#add=${encode(prefill)}` });
    window.close();
  };
  const copyBtn = el("button", "secondary", "Copy details");
  copyBtn.onclick = async () => {
    try { await navigator.clipboard.writeText(summary(a, reg)); copyBtn.textContent = "Copied"; }
    catch (e) { copyBtn.textContent = "Couldn't copy"; }
  };
  const debugBtn = el("button", "secondary", "Copy debug info");
  debugBtn.title = "What the extension saw on this page (may include the seller's name): paste it to get this site fixed";
  debugBtn.onclick = async () => {
    const { debug, ...found } = a;
    try { await navigator.clipboard.writeText(JSON.stringify({ found: { ...found, description: found.description.slice(0, 200) }, ...debug }, null, 1));
          debugBtn.textContent = "Copied"; }
    catch (e) { debugBtn.textContent = "Couldn't copy"; }
  };
  actions.append(addBtn, copyBtn, debugBtn);
  nodes.push(actions, el("div", "muted", "Opens your dashboard's Add car form filled in: check it, then Save."));
  out.replaceChildren(...nodes);
}

(async function start() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https?:/.test(tab.url || "")) return say("Open a car advert first, then click Car Hunter.");
  try {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractAdvert });
    if (!result || (!result.title && !result.price)) return say("Couldn't find an advert on this page.");
    render(result);
  } catch (e) {
    say("The browser doesn't let extensions read this page.");
  }
})();
