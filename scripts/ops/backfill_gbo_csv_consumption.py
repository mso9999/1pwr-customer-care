#!/usr/bin/env python3
"""Match GBO CSV customer-name columns to CC account numbers (and optionally backfill)."""
import csv, io, os, sys, unicodedata, collections, datetime
import psycopg2, psycopg2.extras

CSV="/tmp/gbo.csv"
APPLY="--apply" in sys.argv

def fix_enc(s):
    if not s: return ""
    try: return s.encode("latin-1").decode("utf-8")
    except Exception: return s

def norm(s):
    s=fix_enc(s or "")
    s=unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode("ascii")
    s=s.upper()
    toks=[t for t in ''.join(c if c.isalnum() else ' ' for c in s).split() if t]
    # drop trailing duplicate-marker numbers like "2","3" handled by caller
    return frozenset(toks)

db=os.environ["DATABASE_URL"]
conn=psycopg2.connect(db); cur=conn.cursor()

# Build name -> (account, meter_serial) from the Koios report itself (same source
# as the CSV), across several recent days to capture all customers.
import requests
base=os.environ.get("KOIOS_BASE_URL","https://www.sparkmeter.cloud")
kk=os.environ.get("KOIOS_API_KEY_BN") or os.environ.get("KOIOS_WRITE_API_KEY_BN") or ""
ks=os.environ.get("KOIOS_API_SECRET_BN") or os.environ.get("KOIOS_WRITE_API_SECRET_BN") or ""
SITE="a23c334e-33f7-473d-9ae3-9e631d5336e4"
name2acct={}; acct_meter={}
for d in ("2025-08-15","2025-09-15","2025-10-15","2025-11-15","2025-12-15",
          "2026-01-15","2026-02-15","2026-03-15","2026-04-15","2026-05-15","2026-05-30"):
    rr=requests.get(f"{base}/api/v2/report",headers={"X-API-KEY":kk,"X-API-SECRET":ks},
        params={"granularity":"daily","type":"readings","site_id":SITE,"date":d},timeout=180)
    if rr.status_code!=200: continue
    for row in csv.DictReader(io.StringIO(rr.text)):
        nm=(row.get("meter/customer/name") or "").strip()
        code=(row.get("meter/customer/code") or "").strip()
        ser=(row.get("meter/serial") or "").strip()
        if nm and code:
            name2acct.setdefault(norm(nm), code)
            if ser: acct_meter[code]=ser
cc=[(acct, toks, "") for toks,acct in [(k,v) for k,v in name2acct.items()]]
meter=acct_meter

with open(CSV, newline='', encoding='latin-1') as f:
    header=next(csv.reader(f))
cols=header[1:]

# build CC lookup by token-set
matched={}  # col_index -> account
unmatched_cols=[]
used=set()
for i,col in enumerate(cols):
    ctoks=norm(col)
    # try exact token-set, then subset/superset
    best=None
    for acct,toks,full in cc:
        if acct in used: continue
        if ctoks==toks: best=acct; break
    if not best:
        for acct,toks,full in cc:
            if acct in used: continue
            if toks and ctoks and (toks<=ctoks or ctoks<=toks) and len(toks & ctoks)>=2:
                best=acct; break
    if best:
        matched[i]=best; used.add(best)
    else:
        unmatched_cols.append(col)

print(f"CSV columns: {len(cols)} | Koios name map: {len(cc)}")
print(f"matched: {len(matched)} | unmatched CSV cols: {len(unmatched_cols)}")

# pre-2025-08 kWh coverage by matched vs unmatched columns
def parse_dt0(ts):
    ts=ts.strip()
    for fmt in ("%m/%d/%Y %H:%M:%S","%Y-%m-%d %H:%M:%S","%m/%d/%Y %H:%M","%Y-%m-%d %H:%M"):
        try: return datetime.datetime.strptime(ts,fmt)
        except ValueError: pass
    try:
        d,t=ts.split(); p=d.split('/')
        if len(p)==3:
            a,b,y=int(p[0]),int(p[1]),int(p[2])
            if a>12: return datetime.datetime(y,b,a,int(t.split(':')[0]),0)
    except Exception: pass
    return None
CUT=datetime.datetime(2025,8,1)
kwh_m=0.0; kwh_u=0.0
with open(CSV,newline='',encoding='latin-1') as f:
    r=csv.reader(f); next(r)
    for line in r:
        if not line or not line[0].strip(): continue
        dt=parse_dt0(line[0])
        if dt is None or dt>=CUT: continue
        for i in range(len(cols)):
            if i+1>=len(line): continue
            v=line[i+1].strip()
            if not v: continue
            try: k=float(v)
            except ValueError: continue
            if i in matched: kwh_m+=k
            else: kwh_u+=k
print(f"pre-2025-08 kWh: matched={kwh_m:.1f}  unmatched={kwh_u:.1f}  coverage={100*kwh_m/(kwh_m+kwh_u or 1):.1f}%")
print("unmatched CSV columns:", "; ".join(unmatched_cols[:60]))

if not APPLY:
    sys.exit(0)

# Backfill pre-2025-08 only, for matched columns
def parse_dt(ts):
    ts=ts.strip()
    for fmt in ("%m/%d/%Y %H:%M:%S","%Y-%m-%d %H:%M:%S","%m/%d/%Y %H:%M","%Y-%m-%d %H:%M"):
        try: return datetime.datetime.strptime(ts,fmt)
        except ValueError: pass
    # mixed D/M
    try:
        d,t=ts.split(); p=d.split('/')
        if len(p)==3:
            a,b,y=int(p[0]),int(p[1]),int(p[2])
            if a>12: return datetime.datetime(y,b,a,int(t.split(':')[0]),0)
    except Exception: pass
    return None

CUTOFF=datetime.datetime(2025,8,1)
rows=[]
with open(CSV,newline='',encoding='latin-1') as f:
    r=csv.reader(f); next(r)
    for line in r:
        if not line or not line[0].strip(): continue
        dt=parse_dt(line[0])
        if dt is None or dt>=CUTOFF: continue
        hour=dt.replace(minute=0,second=0,microsecond=0)
        for i,acct in matched.items():
            if i+1>=len(line): continue
            v=line[i+1].strip()
            if not v: continue
            try: kwh=float(v)
            except ValueError: continue
            mid=meter.get(acct) or acct
            rows.append((acct, mid, hour, round(kwh,6), 'GBO'))
print(f"pre-2025-08 rows to insert: {len(rows)}")
if rows:
    psycopg2.extras.execute_values(cur,
        """INSERT INTO hourly_consumption (account_number, meter_id, reading_hour, kwh, community, source)
           VALUES %s ON CONFLICT (meter_id, reading_hour) DO UPDATE SET kwh=EXCLUDED.kwh""",
        rows, template="(%s,%s,%s,%s,%s,'koios'::transaction_source)", page_size=2000)
    conn.commit()
    print("inserted/updated", len(rows))
conn.close()
