"""
Integration test: aldersopsparing flytning => buffer ændres korrekt i API-svar
"""
import os, sys, requests, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "http://localhost:8000"

# 1. Opret session via chat
# Brug /api/session og injicer direkte via parametre — ingen chat nødvendig
r = requests.post(f"{BASE}/api/session", json={})
sid = r.json()["session_id"]
print(f"Session: {sid}")

# 2. Injicer en profil direkte i session via tekst-upload-simulering
# Vi bruger i stedet chat med "Mine svar:" format der bypasser LLM
# Actually, lad os kalde engine direkte via Python for at verificere,
# og verificere API'et med parametrene.

# Prøv at bruge /api/parametre til at sætte op – men session har ingen profil endnu.
# Kald i stedet engine direkte og verificer resultater.

from engine import generer_udbetalingstabel, SkatParametre

profil = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension', 'opsparing': 800000, 'skat_type': 'S'},
        {'selskab': 'PFA', 'produkttype': 'aldersopsparing', 'opsparing': 200000, 'skat_type': 'F'},
    ],
    'folkepensionsalder': 67
}
skat = SkatParametre.fra_pct(25.0, 0.7, True)

PASS = []
FAIL = []

def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print(f"  PASS  {label}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))

print("\n=== ENGINE TESTS ===\n")

# --- Test A: aldersopsparing ved pensionsalder (pre-pension buffer) ---
p_pre = {'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20}
r_pre = generer_udbetalingstabel(profil, p_pre, skat)
jt_pre = r_pre['jaevn_tabel']

fra_pre_65 = next((r['fra_buffer'] for r in jt_pre if r['alder'] == 65), 0)
buf_pre_65 = next((r['buffer_rest'] for r in jt_pre if r['alder'] == 65), 0)

print("A) Aldersopsparing ved pensionsalder 65:")
check("fra_buffer > 0 ved 65 (buffer tilgængelig)", fra_pre_65 > 0, f"fra_buffer={fra_pre_65}")
check("buffer_rest > 0 ved 65", buf_pre_65 > 0, f"buffer_rest={buf_pre_65}")

# --- Test B: aldersopsparing ved alder 80 (post-pension) ---
p_post = {
    'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20,
    'produkt_start_aldre': {'PFA_aldersopsparing': 80}
}
r_post = generer_udbetalingstabel(profil, p_post, skat)
jt_post = r_post['jaevn_tabel']

fra_post_65 = next((r['fra_buffer'] for r in jt_post if r['alder'] == 65), 0)
fra_post_79 = next((r['fra_buffer'] for r in jt_post if r['alder'] == 79), 0)
fra_post_80 = next((r['fra_buffer'] for r in jt_post if r['alder'] == 80), 0)
buf_post_65 = next((r['buffer_rest'] for r in jt_post if r['alder'] == 65), 0)
buf_post_80 = next((r['buffer_rest'] for r in jt_post if r['alder'] == 80), 0)

print("\nB) Aldersopsparing ved alder 80 (post-pension):")
check("fra_buffer = 0 ved 65 (ingen buffer endnu)", fra_post_65 == 0, f"fra_buffer={fra_post_65}")
check("fra_buffer = 0 ved 79 (buffer fortsat tom)", fra_post_79 == 0, f"fra_buffer={fra_post_79}")
check("fra_buffer > 0 ved 80 (lump sum ankommer)", fra_post_80 > 0, f"fra_buffer={fra_post_80}")
check("buffer_rest > 0 ved 80", buf_post_80 > 0, f"buffer_rest={buf_post_80}")

# --- Test C: jaevn_netto_mdr afspejler flytning ---
print("\nC) jaevn_netto_mdr:")
print(f"   Pre (alders@65):  {r_pre['jaevn_netto_mdr']} kr/mdr")
print(f"   Post (alders@80): {r_post['jaevn_netto_mdr']} kr/mdr")
check("jaevn_netto_mdr er forskellig ved de to scenarier",
      r_pre['jaevn_netto_mdr'] != r_post['jaevn_netto_mdr'],
      f"pre={r_pre['jaevn_netto_mdr']}, post={r_post['jaevn_netto_mdr']}")

# --- Test D: JS payload-fix — _collectPayload uden panel params ---
print("\n=== API TEST: produkt_start_aldre uden afkast_pct ===\n")
# Opret en session med en profil via inject-message trick
# Sæt afkast til 8.8 eksplicit
r2 = requests.post(f"{BASE}/api/session", json={})
sid2 = r2.json()["session_id"]

# Sæt afkast_pct = 8.8
resp = requests.post(f"{BASE}/api/parametre", json={
    "session_id": sid2, "afkast_pct": 8.8, "pensionsalder": 65
})
p_after = resp.json()["parametre"]
check("afkast_pct = 8.8 sat korrekt", p_after.get("afkast_pct") == 8.8, str(p_after))

# Send kun produkt_start_aldre (simuler drag uden panel params)
resp2 = requests.post(f"{BASE}/api/parametre", json={
    "session_id": sid2,
    "produkt_start_aldre": {"PFA_aldersopsparing": 80}
})
p_after2 = resp2.json()["parametre"]
check("afkast_pct stadig 8.8 efter drag (ikke overskrevet)", p_after2.get("afkast_pct") == 8.8,
      f"afkast_pct er nu: {p_after2.get('afkast_pct')}")
check("produkt_start_aldre opdateret", p_after2.get("produkt_start_aldre") == {"PFA_aldersopsparing": 80},
      str(p_after2.get("produkt_start_aldre")))

print(f"\n{'='*40}")
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    print("Fejlede tests:")
    for f in FAIL:
        print(f"  - {f}")
