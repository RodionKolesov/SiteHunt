"""Настройки SiteHunt: какие ниши и города собираем, пути, параметры браузера."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SHOTS = DATA / "shots"          # скриншоты главной на телефоне (jpg)
EXPORT = ROOT / "export"
DB = DATA / "sitehunt.db"
for d in (DATA, SHOTS, EXPORT):
    d.mkdir(parents=True, exist_ok=True)

# Ниша → запросы. {city} подставляется. Первая версия: один город, две ниши — проверить отклик, потом расширять.
# Верх выдачи занимают те, кто уже вкладывается в сайт; устаревшие сайты — глубже: поэтому районы и узкие услуги.
CITIES = ["Красноярск"]
DISTRICTS = ["Советский район", "Октябрьский район", "Центральный район", "Железнодорожный район", "Кировский район",
             "Ленинский район", "Свердловский район", "Взлётка", "Северный", "Солнечный", "Покровка", "Николаевка",
             "Академгородок", "Пашенный", "Черёмушки", "Ветлужанка"]
NICHES = {
    "Стоматология": {"base": ["стоматология", "стоматологическая клиника", "зубной врач"],
                     "extra": ["детская стоматология", "имплантация зубов", "лечение зубов", "протезирование зубов",
                               "отбеливание зубов", "брекеты", "удаление зуба", "стоматология недорого", "семейная стоматология"]},
    "Автосервис": {"base": ["автосервис", "ремонт автомобилей", "СТО"],
                   "extra": ["кузовной ремонт", "ремонт двигателя", "шиномонтаж", "ремонт АКПП", "замена ГРМ",
                             "автоэлектрик", "развал схождение", "покраска авто", "ремонт ходовой", "диагностика авто",
                             "ремонт иномарок", "ремонт грузовиков", "автосервис недорого"]},
}
ENGINES = ["ddg"]               # Bing отдаёт роботу пустую выдачу — пока выключен


def queries(city):
    """[(ниша, запрос)]: основные слова × районы + узкие услуги по городу."""
    out = []
    for niche, q in NICHES.items():
        for b in q["base"]:
            out.append((niche, f"{b} {city}"))
            out += [(niche, f"{b} {city} {d}") for d in DISTRICTS]
        out += [(niche, f"{e} {city}") for e in q["extra"]]
    return out


PAGES = 3                       # страниц выдачи на запрос в каждом поисковике
HEADLESS = True
CHECK_TABS = 2                  # параллельных вкладок проверки (у компьютера мало свободной памяти)


def browser_options(headless=None):
    """Параметры запуска Chrome (как в SellerRadar): в фоне подменяем user-agent без «HeadlessChrome»."""
    headless = HEADLESS if headless is None else headless
    args = ["--disable-blink-features=AutomationControlled"]
    if headless:
        major = "155"
        try:
            exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Item '{exe}').VersionInfo.ProductVersion"],
                                 capture_output=True, text=True, timeout=20).stdout.strip()
            major = out.split(".")[0] or major
        except Exception:
            pass
        args.append(f"--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")
    return dict(channel="chrome", headless=headless, locale="ru-RU",
                ignore_default_args=["--enable-automation"], args=args)
