"""
Deploy test: jævn-linje + buffer + diagram korrekthed
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import generer_udbetalingstabel, SkatParametre
import requests

BASE = "http://localhost:8000"
PASS, FAIL = [], []

def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

def chart_jaevn_val(jr, jaevn):
    if jr is None: return None
    if (jr.get('buffer_rest', 0) or 0) < 0 and (jr.get('fra_buffer', 0) or 0) == 0:
        return None
    return jaevn

profil = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension',    'opsparing': 800000, 'skat_type': 'S'},
        {'selskab': 'PFA',    'produkttype': 'aldersopsparing','opsparing': 200000, 'skat_type': 'F'},
    ],
    'folkepensionsalder': 67
}
skat = SkatParametre.fra_pct(25.0, 0.7, True)

# ── 1. Jævn-linje aldrig over søjler (alle scenarier) ─────────────────────────
print("\n=== 1. JÆVN-LINJE IKKE OVER SØJLER ===\n")

scenarier = [
    ("Ingen engangsbeloeb",
     {'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20},
     {**profil, 'pensionsprodukter': [profil['pensionsprodukter'][0]]}),
    ("Engangsbeloeb pre-pension (65)",
     {'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20},
     profil),
    ("Engangsbeloeb post-pension (80)",
     {'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20,
      'produkt_start_aldre': {'PFA_aldersopsparing': 80}},
     profil),
    ("Sen pensionsalder (68)",
     {'pensionsalder': 68, 'afkast_pct': 8.0, 'udbetaling_aar': 25},
     profil),
]

for navn, params, pf in scenarier:
    r = generer_udbetalingstabel(pf, params, skat)
    jaevn = r['jaevn_netto_mdr']
    jt    = {row['alder']: row for row in r['jaevn_tabel']}
    hasBuffer = any((row.get('fra_buffer', 0) or 0) > 0 or
                    (row.get('buffer_afkast_mdr', 0) or 0) > 0
                    for row in r['jaevn_tabel'])

    violations = []
    if jaevn > 0 and hasBuffer:
        for row in r['tabel']:
            alder = row['alder']
            if alder < params['pensionsalder']: continue
            jr = jt.get(alder)
            bar_top = row['total_netto_mdr'] + (jr.get('fra_buffer', 0) if jr else 0)
            if jaevn > bar_top + 1:
                cv = chart_jaevn_val(jr, jaevn)
                if cv is not None:
                    violations.append(alder)

    check(f"{navn}: ingen synlig jævn-linje over søjler", len(violations) == 0,
          f"Aldre: {violations[:5]}")

# ── 2. Buffer: fra_buffer kun når positiv ────────────────────────────────────
print("\n=== 2. FRA_BUFFER KORREKTHED ===\n")

r_post = generer_udbetalingstabel(profil, {
    'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20,
    'produkt_start_aldre': {'PFA_aldersopsparing': 80}
}, skat)
jt_post = {row['alder']: row for row in r_post['jaevn_tabel']}

check("fra_buffer=0 v65 (buffer tom ved pension-start)",
      jt_post.get(65, {}).get('fra_buffer', 0) == 0)
check("fra_buffer=0 v79 (buffer stadig tom)",
      jt_post.get(79, {}).get('fra_buffer', 0) == 0)
check("fra_buffer>0 v80 (lump sum ankommer)",
      (jt_post.get(80, {}).get('fra_buffer', 0) or 0) > 0)
check("buffer_rest<0 v65 (negativ — chart nuller linjen)",
      (jt_post.get(65, {}).get('buffer_rest', 0) or 0) < 0)

r_pre = generer_udbetalingstabel(profil, {
    'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20
}, skat)
jt_pre = {row['alder']: row for row in r_pre['jaevn_tabel']}
check("Pre-pension: fra_buffer>0 v65 (buffer aktiv fra start)",
      (jt_pre.get(65, {}).get('fra_buffer', 0) or 0) > 0)

# ── 3. pv_post_engangs ikke i jaevn ─────────────────────────────────────────
print("\n=== 3. JAEVN IKKE OPPUSTET AF POST-PENSION ENGANGSBELOEB ===\n")

r_pre2 = generer_udbetalingstabel(profil, {
    'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20
}, skat)
r_post2 = generer_udbetalingstabel(profil, {
    'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20,
    'produkt_start_aldre': {'PFA_aldersopsparing': 80}
}, skat)
# Post-pension engangsbeloeb bør IKKE øge jaevn_netto_mdr signifikant
# (det burde nu kun øges minimalt af lump sum der ankommer ved 80 og booster buffer)
jaevn_pre  = r_pre2['jaevn_netto_mdr']
jaevn_post = r_post2['jaevn_netto_mdr']
print(f"  jaevn pre  (alders@65): {jaevn_pre} kr/mdr")
print(f"  jaevn post (alders@80): {jaevn_post} kr/mdr")
check("Jaevn post-pension IKKE langt over jaevn pre-pension",
      abs(jaevn_post - jaevn_pre) < jaevn_pre * 0.5,
      f"forskel={abs(jaevn_post-jaevn_pre)} kr ({abs(jaevn_post-jaevn_pre)/jaevn_pre*100:.0f}%)")

# ── 4. API: afkast_pct bevaret efter drag ────────────────────────────────────
print("\n=== 4. API: DRAG BEVARER SESSION-PARAMETRE ===\n")

sid = requests.post(f"{BASE}/api/session", json={}).json()["session_id"]
requests.post(f"{BASE}/api/parametre", json={"session_id": sid, "afkast_pct": 8.8, "pensionsalder": 65})
p = requests.post(f"{BASE}/api/parametre", json={
    "session_id": sid, "produkt_start_aldre": {"PFA_aldersopsparing": 80}
}).json()["parametre"]
check("afkast_pct 8.8 bevaret efter drag", p.get("afkast_pct") == 8.8, str(p.get("afkast_pct")))
check("produkt_start_aldre sat korrekt", p.get("produkt_start_aldre") == {"PFA_aldersopsparing": 80})

# ── 5. HTML checks ───────────────────────────────────────────────────────────
print("\n=== 5. KODE-CHECKS ===\n")

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html"), encoding="utf-8") as f:
    html = f.read()

check("Jævn-linje null ved negativ buffer (buffer_rest < 0)",
      "buffer_rest" in html and "fra_buffer" in html and "null" in html)
check("hasBuffer guard på jævn-linje",
      "jaevn > 0 && hasBuffer" in html)
check("Ingen engNetto/12 spike i datasets",
      "engNetto / 12" not in html and "engNetto/12" not in html)
check("renderTable med alignment-support",
      "function renderTable(headerLine, separatorLine, bodyRows)" in html)
check("tbl-sep og tbl-total CSS",
      "tbl-sep" in html and "tbl-total" in html)

print(f"\n{'='*44}")
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    for f in FAIL: print(f"  - {f}")
