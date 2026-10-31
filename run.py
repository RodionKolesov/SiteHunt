"""SiteHunt — поиск компаний с устаревшими сайтами.

  python run.py collect [N]   — собрать сайты из поиска (N — не больше N запросов за запуск)
  python run.py check [N]     — проверить непроверенные сайты (N — сколько)
  python run.py export        — выгрузить базу для страницы обзвона и Excel
  python run.py stats         — сколько всего найдено/проверено
"""
import asyncio
import logging
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sh.config import DATA

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S",
                    handlers=[logging.StreamHandler(), logging.FileHandler(DATA / "sitehunt.log", encoding="utf-8")])

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if cmd == "collect":
        from sh import collect
        asyncio.run(collect.run(n))
    elif cmd == "check":
        from sh import check
        asyncio.run(check.run(n))
    elif cmd == "export":
        from sh import export
        export.run()
    else:
        from sh.db import connect
        con = connect()
        for row in con.execute("SELECT niche, count(*), sum(checked_at IS NOT NULL), sum(status='ok'), "
                               "sum(score>=40), sum(phones IS NOT NULL AND phones<>'[]') FROM sites GROUP BY niche"):
            print(f"{row[0]}: найдено {row[1]}, проверено {row[2]}, живых {row[3]}, устаревших (балл ≥40) {row[4]}, с телефоном {row[5]}")
