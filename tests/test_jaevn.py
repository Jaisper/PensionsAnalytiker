"""Test: jævn-linje nulstilles korrekt i chart (buffer_rest < 0 ved alle over-linjeer)"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import generer_udbetalingstabel, SkatParametre

profil = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension',    'opsparing': 800000, 'skat_type': 'S'},
        {'selskab': 'PFA',    'produkttype': 'aldersopsparing','opsparing': 200000, 'skat_type': 'F'},
    ],
    'folkepensionsalder': 67
}
skat = SkatParametre.fra_pct(25.0, 0.7, True)

PASS, FAIL = [], []
def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

def chart_jaevn_val(jr, jaevn):
    """Simuler chart JS-logik: returner None (null) hvis buffer negativ og ingen fra_buffer"""
    if jr is None: return None
    if (jr.get('buffer_rest', 0) or 0) < 0 and (jr.get('fra_buffer', 0) or 0) == 0:
        return None
    return jaevn

print("\n=== Scenario 1: aldersopsparing post-pension (alder 80) ===")
r = generer_udbetalingstabel(profil, {
    'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20,
    'produkt_start_aldre': {'PFA_aldersopsparing': 80}
}, skat)

jaevn = r['jaevn_netto_mdr']
jt    = {row['alder']: row for row in r['jaevn_tabel']}

# Verificer at alle "over-linjeer" har buffer_rest < 0 (JS nuller dem)
over_with_positive_buf = []
for row in r['tabel']:
    alder = row['alder']
    if alder < 65: continue
    jr = jt.get(alder)
    bar_top = row['total_netto_mdr'] + (jr.get('fra_buffer', 0) if jr else 0)
    if jaevn > bar_top + 1:
        chart_val = chart_jaevn_val(jr, jaevn)
        if chart_val is not None:
            over_with_positive_buf.append((alder, round(jaevn), round(bar_top), jr.get('buffer_rest')))

check("Chart nuller jævn-linje ved alle over-søjle-år",
      len(over_with_positive_buf) == 0,
      f"Aldre hvor linje stadig vises over søjle: {over_with_positive_buf}")
check("fra_buffer = 0 ved 65 (tom buffer)", jt.get(65, {}).get('fra_buffer', 0) == 0)
check("fra_buffer > 0 ved 80 (lump sum ankommer)", jt.get(80, {}).get('fra_buffer', 0) > 0)
check("buffer_rest < 0 ved 65 (negativ — JS nuller linjen)", (jt.get(65, {}).get('buffer_rest', 0) or 0) < 0,
      f"buffer_rest={jt.get(65, {}).get('buffer_rest')}")

# Vis hvad chart linje-data faktisk viser
jaevn_chart = [(alder, chart_jaevn_val(jt.get(alder), jaevn)) for alder in range(65, 86)]
visible = [(a, v) for a, v in jaevn_chart if v is not None]
null_ages = [a for a, v in jaevn_chart if v is None]
print(f"  Jævn-linje: null ved aldre {null_ages}")
print(f"  Jævn-linje: synlig ved aldre {[a for a, v in visible]}")

print("\n=== Scenario 2: aldersopsparing pre-pension (alder 65) ===")
r2 = generer_udbetalingstabel(profil, {
    'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20
}, skat)
jaevn2 = r2['jaevn_netto_mdr']
jt2    = {row['alder']: row for row in r2['jaevn_tabel']}

over2 = []
for row in r2['tabel']:
    alder = row['alder']
    if alder < 65: continue
    jr = jt2.get(alder)
    bar_top = row['total_netto_mdr'] + (jr.get('fra_buffer', 0) if jr else 0)
    if jaevn2 > bar_top + 1:
        cv = chart_jaevn_val(jr, jaevn2)
        if cv is not None:
            over2.append((alder, round(jaevn2), round(bar_top), jr.get('buffer_rest')))

check("Scenario 2: chart nuller jævn ved alle over-søjle-år",
      len(over2) == 0,
      f"Stadig synlig over søjle: {over2}")

print(f"\n{'='*42}")
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    for f in FAIL: print(f"  - {f}")
