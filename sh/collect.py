"""Сбор сайтов компаний: запросы «ниша + город» в DuckDuckGo и Bing через настоящий Chrome.

Из выдачи берём только собственные сайты компаний: агрегаторы, справочники, карты, соцсети и доски объявлений
отбрасываем (SKIP). Каждый запрос × поисковик × страница выполняется один раз (таблица queries).
"""
import asyncio
import base64
import logging
import random
import re
from urllib.parse import quote, urlparse, parse_qs, unquote

from . import config
from .db import connect

log = logging.getLogger("collect")

SKIP = re.compile(
    r"(yandex|ya\.ru|google|bing|duckduckgo|2gis|zoon|flamp|yell|spr\.ru|orgpage|rusprofile|list-org|checko|"
    r"avito|youla|drom|drive2|auto\.ru|autoru|profi\.ru|youdo|uslugio|kudago|tripadvisor|otzovik|irecommend|"
    r"prodoctorov|napopravku|docdoc|sberhealth|sberzdorovie|doctu|krasdoctor|stomatologii|stomdoc|medbooking|"
    r"vk\.com|vk\.ru|ok\.ru|t\.me|telegram|instagram|facebook|youtube|dzen|rutube|tiktok|pinterest|"
    r"wikipedia|hh\.ru|superjob|zarplata|rabota|gorodrabot|trudvsem|habr|pikabu|reddit|"
    r"mail\.ru|rambler|ngs|krsk\.au|newslab|prmira|sibnovosti|dela\.ru|gnkk|krasrab|kp\.ru|"
    r"jsprav|cataloxy|blizko|pulscen|tiu\.ru|satom|allbiz|bizly|regmarkets|uslugi\.yandex|"
    r"sravni|banki|tbank|sber|gosuslugi|nalog|mos\.ru|krskstate|admkrsk|"
    r"wildberries|ozon|market\.|aliexpress|exist\.ru|emex|autodoc|zzap|"
    r"apple\.com|microsoft|wordpress\.org|tilda\.cc|wix\.com|"
    r"kleos|stom-firms|like\.doctor|topdent|autoservice-list|avtoservice|uslugi|otzyv|reiting|rating|top\d|"
    r"spravker|krasnoyarsk\.|krsk\.|^msk\.|moscow\.|spb\.|"
    # 26.10: госсайты, вузы, СМИ, справочники и федеральные сервисы из первого полного сбора
    r"gov\.ru|sudrf|krasu\.ru|krasgmu|kraszdrav|szn24|kgsp\d|gto\.ru|rsv\.ru|prezident\.org|pasportniystol|"
    r"tutu\.ru|vc\.ru|ivi\.ru|urait|sky\.pro|telega\.in|fandom|meteo|gornovosti|afisha|^59\.ru|152rus|gogov|"
    r"catalog|katalog|rubrikator|kompas|mestam|etomesto|tomesto|wikibus|multigo|calculatorov|meshok|asktel|vincardata|"
    r"apteka|pharmeconom|econom-apt|medsi|gemotest|energogarant|aquaphor|rusklimat|shinotorg|glazalazer|"
    r"gdeavtoservis|gdestomat|vse-avtoservisy|dent-list|uremont|stofix|101sauna|delaemitochka|azbykaylubki|r-hockey)", re.I)
# заголовки каталогов: «в 15 клиниках», «отзывы и адреса», «все врачи», «рейтинг автосервисов»
AGGR_TITLE = re.compile(r"в \d+ (клиник|стоматолог|автосервис|сервис)|отзыв\w* и адрес|все врачи|рейтинг|лучши[ех] \d*|"
                        r"\d+ отзыв|адреса,? (цены|телефоны)|казино|casino", re.I)


def host_of(url):
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def bing_real(url):
    if "bing.com/ck/" not in url:
        return url
    u = parse_qs(urlparse(url).query).get("u", [""])[0]
    if u.startswith("a1"):
        b = u[2:] + "=" * (-len(u[2:]) % 4)
        try:
            return base64.urlsafe_b64decode(b).decode("utf-8", "ignore")
        except ValueError:
            return url
    return url


async def search(page, engine, query, n):
    """Ссылки одной страницы выдачи (n — номер страницы с 0). None — капча/ошибка."""
    try:
        if engine == "bing":
            first = f"&first={n * 10 + 1}" if n else ""
            await page.goto(f"https://www.bing.com/search?q={quote(query)}&setlang=ru&cc=RU{first}",
                            wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector("li.b_algo h2 a, .b_no", timeout=8000)
            except Exception:
                body = (await page.content())[:20000].lower()
                return None if ("captcha" in body or "verify" in body) else []
            urls = await page.eval_on_selector_all("li.b_algo h2 a", "as => as.map(a => [a.href, a.textContent])")
            return [(bing_real(u), t) for u, t in urls]
        # DuckDuckGo: первая страница, следующие — кнопкой «Больше результатов»
        await page.goto(f"https://duckduckgo.com/?q={quote(query)}&kl=ru-ru", wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector('a[data-testid="result-title-a"]', timeout=8000)
        except Exception:
            body = (await page.content())[:20000].lower()
            return None if ("anomaly" in body or "captcha" in body or "challenge" in body) else []
        for _ in range(n):
            more = await page.query_selector("#more-results")
            if not more:
                break
            await more.click()
            await page.wait_for_timeout(1500)
        urls = await page.eval_on_selector_all('a[data-testid="result-title-a"]', "as => as.map(a => [a.href, a.textContent])")
        return urls[n * 10:] if n else urls
    except Exception as e:
        log.debug("%s %s: %s", engine, query, e)
        return None


async def run(limit_queries=None):
    from playwright.async_api import async_playwright
    con = connect()
    jobs = []
    for city in config.CITIES:
        for niche, q in config.queries(city):
                for n in range(config.PAGES):
                    for eng in config.ENGINES:
                        if not con.execute("SELECT 1 FROM queries WHERE q=? AND engine=? AND page=?", (q, eng, n)).fetchone():
                            jobs.append((city, niche, q, eng, n))
    random.shuffle(jobs)
    if limit_queries:
        jobs = jobs[:limit_queries]
    log.info("запросов к выполнению: %d", len(jobs))
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(str(config.DATA / "search-profile"),
                                                          viewport={"width": 1200, "height": 850}, **config.browser_options())
        await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        await ctx.route(re.compile(r".*\.(png|jpe?g|gif|webp|svg|woff2?|ttf|mp4)(\?.*)?$"), lambda r: r.abort())
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        rest = {"ddg": 0, "bing": 0}   # до какого времени поисковик «отдыхает» после капчи
        new_total = 0
        loop = asyncio.get_running_loop()
        for city, niche, q, eng, n in jobs:
            if rest[eng] > loop.time():
                continue
            res = await search(page, eng, q, n)
            if res is None:
                rest[eng] = loop.time() + 900
                log.warning("%s: капча/ошибка, отдых 15 мин", eng)
                continue
            new = 0
            for url, title in res:
                url = unquote(url)
                h = host_of(url)
                if not h or SKIP.search(h) or AGGR_TITLE.search(title or "") or not url.startswith("http"):
                    continue
                cur = con.execute("INSERT OR IGNORE INTO sites (host, url, title, niche, city, query, engine) VALUES (?,?,?,?,?,?,?)",
                                  ("".join(h.split()), f"{urlparse(url).scheme}://{urlparse(url).netloc}/", (title or "").strip()[:200],
                                   niche, city, q, eng))
                new += cur.rowcount
            con.execute("INSERT OR REPLACE INTO queries (q, engine, page, found) VALUES (?,?,?,?)", (q, eng, n, new))
            con.commit()
            new_total += new
            log.info("%s p%d «%s»: %d ссылок, новых сайтов %d", eng, n + 1, q, len(res), new)
            await asyncio.sleep(3 + random.random() * 4)
        await ctx.close()
    log.info("готово: новых сайтов %d, всего %d", new_total, con.execute("SELECT count(*) FROM sites").fetchone()[0])
