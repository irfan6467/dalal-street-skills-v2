"""Paper position tracker started by Claude: runs `ticket.py track` once a minute until 14:50 IST or the position
is closed. Paper ledger only; nothing is sent to any broker."""
import datetime, json, subprocess, sys, time
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
STOP_AT = datetime.time(14, 50)
def open_qty():
    try:
        pf = json.load(open("nse/portfolio.json", encoding="utf-8"))
    except Exception:
        return None
    pos = pf.get("positions") or {}
    items = pos.values() if isinstance(pos, dict) else pos
    return sum(float(p.get("qty") or p.get("shares") or 0) for p in items if "BEL" in json.dumps(p))
with open("nse/track.log", "a", encoding="utf-8") as log:
    while True:
        now = datetime.datetime.now(IST)
        r = subprocess.run([sys.executable, "-u", "nse-broker/ticket.py", "track"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        q = open_qty()
        log.write("=== %s  open BEL qty: %s\n%s%s\n" % (now.strftime("%H:%M:%S"), q, r.stdout, r.stderr))
        log.flush()
        if q == 0 or now.time() >= STOP_AT:
            log.write("=== tracker stopped at %s (%s)\n" % (now.strftime("%H:%M:%S"), "position closed" if q == 0 else "14:50"))
            break
        time.sleep(60)
open("nse/track.done", "w").write("DONE")
