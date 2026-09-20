"""
Deploy test: diagram double-count fix + tabel alignment fix
"""
import os, sys, requests, json

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
BASE = "http://localhost:8000"

PASS = []
FAIL = []

def check(label, condition, detail=""):
    sym = "PASS" if condition else "FAIL"
    (PASS if condition else FAIL).append(label)
    print(f"  {sym}  {label}" + (f" — {detail}" if detail else ""))

# ── Engine tests ──────────────────────────────────────────────────────────────
from engine import generer_udbetalingstabel, SkatParametre

profil = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension',    'opsparing': 800000, 'skat_type': 'S'},
        {'selskab': 'PFA',   'produkttype': 'aldersopsparing', 'opsparing': 200000, 'skat_type': 'F'},
    ],
    'folkepensionsalder': 67
}
skat = SkatParametre.fra_pct(25.0, 0.7, True)

print("\n=== 1. BUFFER FRA_BUFFER KUN NÅR POSITIV ===\n")

# Aldersopsparing ved 65 (pre-pension) — buffer positiv fra start
r1 = generer_udbetalingstabel(profil, {'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20}, skat)
jt1 = r1['jaevn_tabel']
buf65 = next((r['buffer_rest'] for r in jt1 if r['alder'] == 65), 0)
fra65 = next((r['fra_buffer'] for r in jt1 if r['alder'] == 65), 0)
check("Pre-pension: buffer_rest > 0 ved 65", buf65 > 0, f"buffer_rest={buf65}")
check("Pre-pension: fra_buffer > 0 ved 65 (buffer tilgængelig)", fra65 > 0, f"fra_buffer={fra65}")

# Aldersopsparing ved 80 (post-pension) — ingen buffer 65-79
r2 = generer_udbetalingstabel(profil, {
    'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20,
    'produkt_start_aldre': {'PFA_aldersopsparing': 80}
}, skat)
jt2 = r2['jaevn_tabel']
buf65_post = next((r['buffer_rest'] for r in jt2 if r['alder'] == 65), 0)
fra65_post = next((r['fra_buffer'] for r in jt2 if r['alder'] == 65), 0)
fra79_post = next((r['fra_buffer'] for r in jt2 if r['alder'] == 79), 0)
fra80_post = next((r['fra_buffer'] for r in jt2 if r['alder'] == 80), 0)
buf80_post = next((r['buffer_rest'] for r in jt2 if r['alder'] == 80), 0)

check("Post-pension: buffer_rest < 0 ved 65 (negativ)", buf65_post < 0, f"buffer_rest={buf65_post}")
check("Post-pension: fra_buffer = 0 ved 65", fra65_post == 0, f"fra_buffer={fra65_post}")
check("Post-pension: fra_buffer = 0 ved 79", fra79_post == 0, f"fra_buffer={fra79_post}")
check("Post-pension: fra_buffer > 0 ved 80 (lump sum ankommer)", fra80_post > 0, f"fra_buffer={fra80_post}")
check("Post-pension: buffer_rest > 0 ved 80", buf80_post > 0, f"buffer_rest={buf80_post}")

print("\n=== 2. API: AFKAST_PCT IKKE OVERSKREVET AF DRAG ===\n")

r = requests.post(f"{BASE}/api/session", json={})
sid = r.json()["session_id"]
requests.post(f"{BASE}/api/parametre", json={"session_id": sid, "afkast_pct": 8.8, "pensionsalder": 65})
r2 = requests.post(f"{BASE}/api/parametre", json={"session_id": sid, "produkt_start_aldre": {"PFA_aldersopsparing": 80}})
p = r2.json()["parametre"]
check("afkast_pct 8.8 bevaret efter drag", p.get("afkast_pct") == 8.8, str(p.get("afkast_pct")))
check("produkt_start_aldre sat", p.get("produkt_start_aldre") == {"PFA_aldersopsparing": 80})

print("\n=== 3. HTML: INGEN ENGANGS-SPIKE I CHART-KODE ===\n")

with open(os.path.join(REPO_ROOT, "static", "index.html"), encoding="utf-8") as f:
    html = f.read()

check("Spike-blok fjernet (engNetto/12 ikke i chart datasets)",
      "engNetto / 12" not in html and "engNetto/12" not in html,
      "engNetto/12 stadig i kode!")
check("Kommentar om dobbelt-tælling til stede",
      "vises ikke som separat spike" in html)

print("\n=== 4. HTML: TABEL ALIGNMENT PARSER ===\n")

check("renderTable tager headerLine, separatorLine, bodyRows",
      "function renderTable(headerLine, separatorLine, bodyRows)" in html)
check("Alignment parsed fra separator (/:$/)",
      "/:$/.test(s)" in html or "/:$/." in html)
check("tbl-sep CSS klasse defineret",
      "tr.tbl-sep td" in html)
check("tbl-total CSS klasse defineret",
      "tr.tbl-total td" in html)
check("isSepRow bruges til at identificere mid-table separatorer",
      "isSepRow" in html)

print(f"\n{'='*42}")
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    print("Fejlede:")
    for f in FAIL:
        print(f"  - {f}")
