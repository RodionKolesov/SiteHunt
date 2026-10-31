"""Проверка сайта: открываем главную в Chrome как с телефона, считаем признаки устаревания, снимаем скриншот,
собираем контакты (главная + страница «Контакты»).

Балл 0–100 — насколько сайт устарел: чем больше, тем сильнее компании нужен новый сайт.
Каждая причина пишется так, чтобы её можно было сказать клиенту по телефону.
"""
import asyncio
import datetime as dt
import json
import logging
import re
import ssl
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx

from . import config
from .db import connect

log = logging.getLogger("check")
YEAR = dt.date.today().year

UA_MOBILE = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/130.0.0.0 Mobile Safari/537.36")
PARKED = re.compile(r"for sale|продаётся|продается|домен.{0,30}(продаж|свободен|припаркован)|parking|default web page|"
                    r"index of /|сайт заблокирован|account suspended|site is under construction|coming soon|"
                    r"buy this domain|сайт временно недоступен|услуга хостинга|хостинг.{0,20}(приостановлен|заблокирован)", re.I)
SOCIAL = re.compile(r"https?://(?:www\.|m\.)?(t\.me|vk\.com|vk\.ru|instagram\.com|wa\.me|api\.whatsapp\.com|ok\.ru|youtube\.com|dzen\.ru)/"
                    r"(?!share|join|widget|away|intent|sharer|wall|video|feed|search|js|rtrg|im|login)(@?[A-Za-z0-9_.\-]{2,})", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.(?:ru|com|net|org|рф|info|pro|su|biz)\b")
PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
BAD_EMAIL = ("example", "sentry", "wixpress", "domain.", "@2x", ".png", ".jpg", "yourmail", "email@", "test@", "noreply", "no-reply")

# То, что собираем в браузере одним вызовом
PROBE = r"""() => {
  const t = document.body ? document.body.innerText : "";
  const vp = document.querySelector('meta[name="viewport"]');
  const gen = document.querySelector('meta[name="generator"]');
  const og = document.querySelector('meta[property="og:site_name"]');
  const cs = document.body ? getComputedStyle(document.body) : null;
  const res = performance.getEntriesByType("resource");
  const nav = performance.getEntriesByType("navigation")[0];
  const bytes = res.reduce((s, r) => s + (r.transferSize || r.encodedBodySize || 0), 0) + (nav ? nav.transferSize || 0 : 0);
  const tables = [...document.querySelectorAll("table")].filter(x => x.querySelector("table") || x.getAttribute("width") || x.getAttribute("bgcolor")).length;
  const links = [...document.querySelectorAll("a[href]")].map(a => [a.href, (a.textContent || "").trim().slice(0, 40)]);
  return {
    viewport: vp ? vp.content : null,
    sw: document.documentElement.scrollWidth, iw: window.innerWidth,
    font: cs ? parseFloat(cs.fontSize) : null,
    jq: window.jQuery && window.jQuery.fn ? window.jQuery.fn.jquery : null,
    gen: gen ? gen.content : null,
    site_name: og ? og.content : null,
    old_tags: document.querySelectorAll("font, marquee, center, frameset, frame, blink").length,
    flash: document.querySelectorAll('object, embed[src$=".swf"], embed[type*="flash"]').length,
    tables, bytes, imgs: document.images.length,
    tel: document.querySelectorAll('a[href^="tel:"]').length,
    text: t.slice(0, 60000), html_len: document.documentElement.outerHTML.length,
    links: links.slice(0, 400),
  };
}"""


def cert_ok(host):
    """True — https с действительным сертификатом, False — нет https или сертификат плохой."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as s, ctx.wrap_socket(s, server_hostname=host):
            return True
    except Exception:
        return False


def contacts_from(html_or_text, links=()):
    socials, emails, phones = {}, [], []
    blob = html_or_text + " " + " ".join(h for h, _ in links)
    for host, handle in SOCIAL.findall(blob):
        host = host.lower().replace("vk.ru", "vk.com").replace("api.whatsapp.com", "wa.me")
        if host == "wa.me" and len(re.sub(r"\D", "", handle)) < 10:
            continue
        socials.setdefault(host, f"https://{host}/{handle.rstrip('.')}")
    for e in EMAIL.findall(blob):
        e = e.lower()
        if not any(b in e for b in BAD_EMAIL) and e not in emails:
            emails.append(e)
    for p in PHONE.findall(blob):
        d = re.sub(r"\D", "", p)
        d = "+7" + d[1:] if len(d) == 11 else None
        if d and d not in phones and len(set(d[2:])) > 2:
            phones.append(d)
    return socials, emails[:4], phones[:4]


def score(f):
    """Причины устаревания: [(код, понятный текст, баллы)]."""
    r = []
    add = lambda code, text, pts: r.append((code, text, pts))
    if not f["https"]:
        add("https", "нет защищённого соединения (https) — браузер пишет «Не защищено», часть клиентов уходит", 20)
    if not f["viewport"]:
        add("mobile", "сайт не адаптирован под телефон — на смартфоне всё мелкое, приходится увеличивать", 25)
    elif f["sw"] > f["iw"] + 8:
        add("hscroll", "на телефоне страница шире экрана — листается вбок", 12)
    y = f.get("year")
    if y and y <= YEAR - 6:
        add("year", f"в подвале сайта {y} год — сайт давно не обновлялся", 20)
    elif y and y <= YEAR - 3:
        add("year", f"в подвале сайта {y} год — сайт не обновлялся несколько лет", 12)
    jq = f.get("jq") or ""
    if re.match(r"1\.(\d|1[01])\.", jq):
        add("jquery", f"старые технологии (jQuery {jq}, 2010–2015 годы)", 8)
    if f["old_tags"] or f["flash"]:
        add("tags", "устаревшая вёрстка (теги font/center/Flash)", 10)
    if f["tables"] >= 2:
        add("tables", "вёрстка таблицами, как в начале 2000-х", 10)
    if f["load"] >= 10:
        add("slow", f"главная грузится {f['load']:.0f} с на телефоне — люди не дожидаются", 15)
    elif f["load"] >= 6:
        add("slow", f"главная грузится {f['load']:.0f} с на телефоне", 8)
    if f["bytes"] >= 8_000_000:
        add("heavy", f"страница весит {f['bytes'] / 1e6:.0f} МБ — тяжело для мобильного интернета", 5)
    if f["font"] and f["font"] < 13 and f["viewport"]:
        add("font", "мелкий шрифт на телефоне", 5)
    g = (f.get("gen") or "").lower()
    if re.search(r"joomla! ?(1|2|3)\.|wordpress [1-4]\.|drupal [5-7]|ucoz|narod|nethouse", g):
        add("cms", f"старая версия движка ({f['gen']})", 8)
    if f["phones"] and not f["tel"]:
        add("tel", "телефон на сайте нельзя нажать, чтобы позвонить", 5)
    if not f["phones"] and not f["emails"]:
        add("nocontacts", "на главной не видно телефона и почты", 5)
    return r, min(100, sum(p for *_, p in r))


def footer_year(text):
    ys = []
    for m in re.finditer(r"(?:©|\(c\)|&copy;|copyright)\s*(?:\d{4}\s*[-–—]\s*)?((?:19|20)\d\d)", text, re.I):
        ys.append(int(m.group(1)))
    if not ys:  # «2012–2019 гг.» внизу без значка ©
        tail = text[-1500:]
        ys = [int(y) for y in re.findall(r"\b(20[0-2]\d)\b", tail)]
    ys = [y for y in ys if 1995 <= y <= YEAR]
    return max(ys) if ys else None


async def check_one(ctx, http, row):
    host, url = row["host"], row["url"]
    page = await ctx.new_page()
    out = {"status": "dead"}
    try:
        t0 = time.time()
        resp = None
        for u in (url, url.replace("https://", "http://") if url.startswith("https") else url.replace("http://", "https://")):
            try:
                resp = await page.goto(u, wait_until="load", timeout=25000)
                break
            except Exception as e:
                log.debug("%s: %s", u, e)
        if resp is None:
            return out
        load = time.time() - t0
        await page.wait_for_timeout(1200)
        f = await page.evaluate(PROBE)
        text = f.pop("text")
        links = f.pop("links")
        final = page.url
        title = (await page.title()).strip()[:200]
        if resp.status >= 400 or len(text.strip()) < 80 or PARKED.search(text[:3000] + " " + title):
            out = {"status": "parked", "final_url": final, "title": title}
            return out
        # закрыть баннер cookie / всплывающее окно, чтобы на скриншоте был сам сайт
        for sel in ['button:text-matches("^(ok|ок|принять|согласен|понятно|хорошо|закрыть|accept)$", "i")',
                    'a:text-matches("^(ok|ок|принять|согласен|понятно)$", "i")']:
            try:
                b = page.locator(sel).first
                if await b.is_visible(timeout=300):
                    await b.click(timeout=1000)
                    await page.wait_for_timeout(400)
            except Exception:
                pass
        shot = re.sub(r"[^a-z0-9.\-]", "_", host) + ".jpg"
        await page.screenshot(path=str(config.SHOTS / shot), type="jpeg", quality=55)
        html = await page.content()
        socials, emails, phones = contacts_from(html, links)
        # страница «Контакты» — там обычно все телефоны, почта и адрес
        cpage = next((h for h, t in links if urlparse(h).netloc.endswith(host) and
                      re.search(r"contact|kontakt|контакт", h + " " + t, re.I)), None)
        address = None
        if cpage:
            try:
                r = await http.get(cpage)
                s2, e2, p2 = contacts_from(r.text)
                for k, v in s2.items():
                    socials.setdefault(k, v)
                emails = list(dict.fromkeys(emails + e2))[:4]
                phones = list(dict.fromkeys(phones + p2))[:4]
                m = re.search(r"(?:г\.\s*Красноярск[,\s]*)?(?:ул\.|улица|пр\.|проспект|пер\.)\s*[А-ЯЁ][^<\n]{3,60}?\d+[а-яА-Я]?", r.text)
                address = m.group(0).strip() if m else None
            except Exception:
                pass
        if not address:
            m = re.search(r"(?:ул\.|улица|пр\.|проспект|пер\.)\s*[А-ЯЁ][^\n]{3,60}?\d+[а-яА-Я]?", text)
            address = m.group(0).strip() if m else None
        f.update(https=await asyncio.to_thread(cert_ok, urlparse(final).hostname or host) and final.startswith("https"),
                 load=load, year=footer_year(text), phones=phones, emails=emails)
        reasons, sc = score(f)
        facts = {k: f[k] for k in ("https", "viewport", "sw", "iw", "font", "jq", "gen", "old_tags", "flash", "tables",
                                   "bytes", "imgs", "tel", "year", "html_len")}
        facts["load"] = round(load, 1)
        name = (f.get("site_name") or re.split(r"\s[|—–\-:]\s", title)[0] or host).strip()[:80]
        out = {"status": "ok", "final_url": final, "title": title, "score": sc, "reasons": json.dumps(reasons, ensure_ascii=False),
               "facts": json.dumps(facts, ensure_ascii=False), "name": name, "phones": json.dumps(phones),
               "emails": json.dumps(emails), "socials": json.dumps(socials, ensure_ascii=False), "address": address, "shot": shot}
        return out
    except Exception as e:
        log.debug("%s: %s", host, e)
        return out
    finally:
        try:
            await asyncio.wait_for(page.close(), 10)
        except Exception:
            pass


async def run(limit=None, recheck=False):
    from playwright.async_api import async_playwright
    con = connect()
    q = "SELECT host, url FROM sites" + ("" if recheck else " WHERE checked_at IS NULL") + " ORDER BY found_at"
    rows = con.execute(q).fetchall()[: limit or None]
    log.info("сайтов к проверке: %d", len(rows))
    http = httpx.AsyncClient(headers={"User-Agent": UA_MOBILE}, timeout=12, follow_redirects=True, verify=False)
    async with async_playwright() as pw:
        opts = config.browser_options(); opts.pop("locale", None)
        browser = await pw.chromium.launch(**opts)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                        device_scale_factor=1, user_agent=UA_MOBILE, ignore_https_errors=True, locale="ru-RU")
        sem = asyncio.Semaphore(config.CHECK_TABS)
        done = 0

        async def one(row):
            nonlocal done
            async with sem:
                try:
                    # общий предел на сайт: иначе зависший сайт навсегда занимает вкладку
                    res = await asyncio.wait_for(check_one(ctx, http, row), 90)
                except asyncio.TimeoutError:
                    res = {"status": "dead"}
            cols = ", ".join(f"{k}=?" for k in res)
            con.execute(f"UPDATE sites SET {cols}, checked_at=datetime('now') WHERE host=?", (*res.values(), row["host"]))
            con.commit()
            done += 1
            log.info("[%d/%d] %s → %s %s", done, len(rows), row["host"], res["status"], res.get("score", ""))

        await asyncio.gather(*(one(r) for r in rows))
        await browser.close()
    await http.aclose()
