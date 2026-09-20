import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

# Test 1: aldersopsparing ved pension (65 = default)
params1 = {'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20}
r1 = generer_udbetalingstabel(profil, params1, skat)
jt1 = r1['jaevn_tabel']
fra1 = [row for row in jt1 if row.get('fra_buffer', 0) > 0]

print("=== Test 1: aldersopsparing ved pensionsalder 65 (pre-pension) ===")
print("buffer_ved_pension:", r1.get('buffer_ved_pension'))
print("jaevn_netto_mdr:", r1.get('jaevn_netto_mdr'))
print("Aldre med fra_buffer:", [r['alder'] for r in fra1[:8]])
print("fra_buffer v65:", next((r['fra_buffer'] for r in jt1 if r['alder']==65), 'N/A'))
print("buffer_rest v65:", next((r['buffer_rest'] for r in jt1 if r['alder']==65), 'N/A'))
print()

# Test 2: aldersopsparing flyttes til 80 (post-pension)
params2 = {
    'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20,
    'produkt_start_aldre': {'PFA_aldersopsparing': 80}
}
r2 = generer_udbetalingstabel(profil, params2, skat)
jt2 = r2['jaevn_tabel']
fra2 = [row for row in jt2 if row.get('fra_buffer', 0) > 0]

print("=== Test 2: aldersopsparing ved 80 (post-pension) ===")
print("buffer_ved_pension:", r2.get('buffer_ved_pension'))
print("jaevn_netto_mdr:", r2.get('jaevn_netto_mdr'))
print("Aldre med fra_buffer:", [r['alder'] for r in fra2[:10]])
print("fra_buffer v65:", next((r['fra_buffer'] for r in jt2 if r['alder']==65), 'N/A'))
print("buffer_rest v65:", next((r['buffer_rest'] for r in jt2 if r['alder']==65), 'N/A'))
print("buffer_rest v79:", next((r['buffer_rest'] for r in jt2 if r['alder']==79), 'N/A'))
print("buffer_rest v80:", next((r['buffer_rest'] for r in jt2 if r['alder']==80), 'N/A'))
print()
print("Konklusion:")
if r1.get('buffer_ved_pension', 0) != r2.get('buffer_ved_pension', 0):
    print("OK: buffer_ved_pension er forskellig!")
else:
    print("PROBLEM: buffer_ved_pension er SAMME!")
if fra1 and fra2 and fra1[0]['alder'] != fra2[0]['alder']:
    print("OK: fra_buffer starter ved forskellig alder!")
else:
    print("PROBLEM: fra_buffer starter ved SAMME alder!")
