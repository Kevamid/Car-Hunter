"use strict";
// Car Hunter extension: when YOU click it, reads the advert in the current tab (nothing runs otherwise,
// nothing is sent anywhere) and can open your dashboard's "Add car" form filled in with what it found.
const DASHBOARD = "https://kevamid.github.io/Car-Hunter/";

/* Runs inside the advert page (injected on click). Must be self-contained: no outside variables.
   Order of trust: the page's structured listing data (schema.org JSON-LD, Open Graph), then its text. */
function extractAdvert() {
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

  const h1 = document.querySelector("h1");
  const title = String(car.name || (h1 && h1.innerText) || meta("og:title") || document.title || "").replace(/\s+/g, " ").trim();
  const text = ((document.querySelector("main, article, [role=main]") || document.body).innerText || "").slice(0, 40000);

  // price: structured first; else the first £ amount that isn't finance ("£199 per month", "deposit")
  let price = num(offer.price) || num(val(offer.priceSpecification && offer.priceSpecification.price)) ||
              num(meta("product:price:amount")) || num(meta("og:price:amount")) || num(meta("price"));
  if (!price) {
    for (const m of text.matchAll(/£\s?(\d{1,3}(?:,\d{3})+|\d{3,6})(?!\.\d)(.{0,24})/g)) {
      if (/month|p\/?m\b|pcm|deposit|per week|finance|apr/i.test(m[2])) continue;
      if (num(m[1]) >= 300) { price = num(m[1]); break; }
    }
  }
  let mileage = num(val(car.mileageFromOdometer));
  if (!mileage) {
    const m = text.match(/(\d{1,3}(?:,\d{3})+|\d{1,6})\s*(k)?\s*(?:miles|mi)\b/i);
    if (m) mileage = m[2] ? Math.round(Number(m[1].replace(/,/g, "")) * 1000) : num(m[1]);
  }
  const year = yearIn(car.vehicleModelDate || car.modelDate || car.productionDate || car.dateVehicleFirstRegistered) ||
               yearIn(title) || yearIn((text.match(/\b(?:year|registered|first registration)\b[^\n\d]{0,20}\d{4}/i) || [])[0]);
  const pick = (structured, re) => {
    const s = val(structured); if (s) return String(s).replace(/^https?:\/\/schema\.org\//, "");
    const m = text.match(re); return m ? m[1][0].toUpperCase() + m[1].slice(1).toLowerCase() : null;
  };
  const transmission = pick(car.vehicleTransmission, /\b(manual|automatic|semi-auto)\b/i);
  const fuel = pick(car.fuelType, /\b(petrol|diesel|hybrid|electric)\b/i);
  // UK plates in the current format (AB12 CDE), only if written on the page
  const regs = [...new Set([...text.matchAll(/\b([A-Z]{2}\d{2}) ?([A-Z]{3})\b/g)].map(m => m[1] + " " + m[2]))];

  const place = offer.availableAtOrFrom || (offer.seller && offer.seller.address) || (car.offers && car.offers.seller && car.offers.seller.address) || {};
  const addr = place.address || place;
  const where = (typeof addr === "string" ? addr : addr.addressLocality || addr.addressRegion || null);

  // photos: structured, Open Graph, then big pictures on the page (not logos/icons)
  const images = [];
  const add = u => { u = val(u) || (u && (u.url || u.contentUrl)); if (typeof u !== "string") return;
    try { u = new URL(u, window.location.href).href; } catch (e) { return; }
    if (/^https:/.test(u) && !/\.svg(\?|$)|logo|icon|sprite|avatar/i.test(u) && !images.includes(u)) images.push(u); };
  [].concat(car.image || []).forEach(add);
  add(meta("og:image"));
  document.querySelectorAll("img").forEach(img => { if (img.naturalWidth >= 400 && img.naturalHeight >= 250) add(img.currentSrc || img.src); });

  return {
    url: window.location.href, site: window.location.hostname.replace(/^www\./, ""), title, price, mileage, year, transmission, fuel,
    regs, location: where, description: String(car.description || meta("og:description") || "").trim().slice(0, 3000),
    images: images.slice(0, 25), structured: Boolean(car.name || offer.price),
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
  if (a.regs.length > 1) nodes.push(el("div", "muted", `Possible registrations on the page: ${a.regs.slice(0, 4).join(", ")}`));
  if (!a.structured) nodes.push(el("div", "muted", "This site gives no structured listing data, so these were read from the page's text: check them in the form."));

  const actions = el("div", "actions");
  const addBtn = el("button", "primary", "Add to my dashboard");
  addBtn.onclick = () => {
    const prefill = { link: a.url, title: a.title, price: a.price, mileage: a.mileage, year: a.year, transmission: a.transmission,
                      fuel: a.fuel, location: a.location, reg, images: a.images.slice(0, 20), description: a.description };
    chrome.tabs.create({ url: `${DASHBOARD}#add=${encode(prefill)}` });
    window.close();
  };
  const copyBtn = el("button", "secondary", "Copy details");
  copyBtn.onclick = async () => {
    try { await navigator.clipboard.writeText(summary(a, reg)); copyBtn.textContent = "Copied"; }
    catch (e) { copyBtn.textContent = "Couldn't copy"; }
  };
  actions.append(addBtn, copyBtn);
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
