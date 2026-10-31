"""Выгрузка для страницы обзвона (web/data.json + web/shots/*.jpg) и Excel (export/SiteHunt_база.xlsx).

На страницу идут только живые сайты с баллом от MIN_SCORE — остальные клиенту нечего предложить.
"""
import json
import logging
import shutil
from datetime import datetime

from . import config
from .collect import SKIP
from .db import connect

log = logging.getLogger("export")
MIN_SCORE = 15
WEB = config.ROOT / "web"


def rows(con):
    out = []
    for r in con.execute("SELECT * FROM sites WHERE status='ok' AND score>=? ORDER BY score DESC, host", (MIN_SCORE,)):
        if SKIP.search(r["host"]):  # справочники и госсайты, попавшие в базу до расширения SKIP
            continue
        f = json.loads(r["facts"] or "{}")
        out.append({
            "id": r["host"], "u": r["final_url"] or r["url"], "n": r["name"] or r["host"], "ni": r["niche"], "c": r["city"],
            "sc": r["score"], "r": [[t, p] for _, t, p in json.loads(r["reasons"] or "[]")],
            "ph": json.loads(r["phones"] or "[]"), "em": json.loads(r["emails"] or "[]"),
            "so": json.loads(r["socials"] or "{}"), "ad": r["address"], "shot": r["shot"],
            "y": f.get("year"), "ld": f.get("load"), "https": f.get("https"), "mob": bool(f.get("viewport")),
        })
    return out


def run():
    con = connect()
    data = rows(con)
    (WEB / "shots").mkdir(parents=True, exist_ok=True)
    keep = set()
    for d in data:
        if d["shot"] and (config.SHOTS / d["shot"]).exists():
            shutil.copy2(config.SHOTS / d["shot"], WEB / "shots" / d["shot"])
            keep.add(d["shot"])
        else:
            d["shot"] = None
    for p in (WEB / "shots").glob("*.jpg"):
        if p.name not in keep:
            p.unlink()
    total = con.execute("SELECT count(*), sum(status='ok') FROM sites").fetchone()
    meta = {"updated": datetime.now().strftime("%d.%m.%Y %H:%M"), "found": total[0], "alive": total[1] or 0,
            "min": MIN_SCORE, "sites": data}
    (WEB / "data.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    xlsx(data)
    log.info("на страницу: %d сайтов (из %d найденных, живых %d), скриншотов %d", len(data), total[0], total[1] or 0, len(keep))
    return len(data)


def xlsx(data):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = "Сайты"
    head = ["Балл", "Компания", "Ниша", "Сайт", "Телефоны", "E-mail", "Адрес", "Что не так с сайтом", "Год в подвале", "Загрузка, с"]
    ws.append(head)
    for d in data:
        ws.append([d["sc"], d["n"], d["ni"], d["u"], ", ".join(d["ph"]), ", ".join(d["em"]), d["ad"] or "",
                   "\n".join(f"• {t}" for t, _ in d["r"]), d["y"], d["ld"]])
    for c in ws[1]:
        c.font = Font(bold=True)
    for col, w in zip("ABCDEFGHIJ", (6, 30, 14, 34, 28, 28, 30, 70, 10, 10)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        row[7].alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    wb.save(config.EXPORT / "SiteHunt_база.xlsx")
