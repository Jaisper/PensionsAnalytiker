"""Test: fuld partner-profil / husstands-orkestrering (Fase D)."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from engine import generer_udbetalingstabel, folkepension_alder
import husstand

PASS, FAIL = [], []
def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

profil_a = {
    'person': {'alder': 67, 'foedselsaar': 1959},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension', 'opsparing': 1_200_000, 'skat_type': 'S'},
    ],
}
parametre_a = {'pensionsalder': 67, 'afkast_pct': 4.0, 'udbetaling_aar': 20, 'civilstand': 'gift_samlevende'}

profil_b = {
    'person': {'alder': 63, 'foedselsaar': 1963},
    'pensionsprodukter': [
        {'selskab': 'PFA', 'produkttype': 'ratepension', 'opsparing': 400_000, 'skat_type': 'S'},
    ],
}
parametre_b = {'pensionsalder': 68, 'afkast_pct': 4.0, 'udbetaling_aar': 20, 'civilstand': 'gift_samlevende'}


print("\n=== Solo-kørsel matcher eksisterende adfærd (regression) ===")
solo_a_direkte = generer_udbetalingstabel(profil_a, parametre_a)
solo_a_via_helper = husstand._indtaegtsgrundlag_pr_kalenderaar(
    generer_udbetalingstabel(profil_a, parametre_a), 67
)
check("Solo-grundlag for A er identisk uanset hvordan det udtrækkes",
      solo_a_direkte['tabel'][0]['indtaegtsgrundlag_aar'] ==
      solo_a_via_helper[date.today().year],
      f"fik {solo_a_via_helper.get(date.today().year)}")


print("\n=== Symmetrisk kobling: begge retninger tæller den ANDENS reelle indkomst med ===")
r = husstand.beregn_husstand(profil_a, parametre_a, profil_b, parametre_b)
resultat_a, resultat_b = r['a'], r['b']

fp_alder_a = resultat_a['fp_alder']
fp_alder_b = resultat_b['fp_alder']
check("A's fp-alder er 67 (foedselsaar 1959)", fp_alder_a == 67, f"fik {fp_alder_a}")
check("B's fp-alder er 68 (foedselsaar 1963)", fp_alder_b == 68, f"fik {fp_alder_b}")

# B naar sin egen fp-alder (68) ved kalenderaar 2026+(68-63)=2031, hvilket
# svarer til A's alder 67+(2031-2026)=72. Foer da: A's grundlag er kun egen
# indkomst. Fra da: A's grundlag inkluderer B's reelle indkomst.
row_a_foer = next(row for row in resultat_a['tabel'] if row['alder'] == 71)
row_a_efter = next(row for row in resultat_a['tabel'] if row['alder'] == 72)
check("A's grundlag stiger i det aar B selv bliver folkepensionist",
      row_a_efter['indtaegtsgrundlag_aar'] > row_a_foer['indtaegtsgrundlag_aar'],
      f"71 aar: {row_a_foer['indtaegtsgrundlag_aar']:.0f}, 72 aar: {row_a_efter['indtaegtsgrundlag_aar']:.0f}")

solo_b = generer_udbetalingstabel(profil_b, parametre_b)
b_egen_indkomst_ved_72 = next(
    row['indtaegtsgrundlag_aar'] for row in solo_b['tabel']
    if row['alder'] == 63 + ((date.today().year + (72 - 67)) - date.today().year)
)
check("Stigningen i A's grundlag svarer til B's egen (solo) indkomst det aar",
      abs((row_a_efter['indtaegtsgrundlag_aar'] - row_a_foer['indtaegtsgrundlag_aar']) - b_egen_indkomst_ved_72) < 1,
      f"stigning={row_a_efter['indtaegtsgrundlag_aar'] - row_a_foer['indtaegtsgrundlag_aar']:.0f}, B's egen={b_egen_indkomst_ved_72:.0f}")

# B's foerste raekke (alder 68) svarer til kalenderaar 2031, hvor A (fp fra 67)
# allerede er folkepensionist -- B's grundlag skal derfor inkludere A's indkomst
# fra allerførste raekke.
row_b_foerste = resultat_b['tabel'][0]
solo_a_ved_samme_aar = next(
    row['indtaegtsgrundlag_aar'] for row in solo_a_direkte['tabel']
    if row['alder'] == 67 + (68 - 63)
)
check("B's grundlag inkluderer A's indkomst fra B's allerfoerste raekke (A er allerede fp)",
      row_b_foerste['indtaegtsgrundlag_aar'] > solo_b['tabel'][0]['indtaegtsgrundlag_aar'],
      f"husstand: {row_b_foerste['indtaegtsgrundlag_aar']:.0f}, solo: {solo_b['tabel'][0]['indtaegtsgrundlag_aar']:.0f}")


print("\n=== Kalenderaars-noegler bruger foedselsdags-UAFHAENGIG alder (ikke person.alder) ===")
# B's foedselsdag (15. november) er IKKE passeret paa test-koersels-dato, saa
# pdf_parser-stil alder = today.year - 1963 - 1 (foedselsdags-justeret), mens
# den kalenderaars-baserede alder (year-of-birth) er today.year - 1963 (uden
# det ekstra fradrag). Foer rettelsen blev person.alder (fodselsdags-justeret)
# brugt til at bygge kalenderaars-noeglerne i partner_indkomst_pr_kalenderaar,
# som ikke matcher den alder_nu_kalenderaar engine.py nu slaar op med naar den
# ANDEN person (A) spoerger "hvilket kalenderaar er dette for B".
today = date.today()
b_alder_foedselsdags_justeret = today.year - 1963 - ((today.month, today.day) < (11, 15))
profil_b_med_foedselsdato = {
    'person': {'alder': b_alder_foedselsdags_justeret, 'foedselsdato': f"15.11.1963"},
    'pensionsprodukter': [
        {'selskab': 'PFA', 'produkttype': 'ratepension', 'opsparing': 400_000, 'skat_type': 'S'},
    ],
}
skat_a_med_foedselsdato = husstand.skat_params_for_person(
    parametre_a, profil_b_med_foedselsdato, parametre_b,
)
# Kalenderaaret for B's FOERSTE raekke (alder = pensionsalder_b = 68) skal
# vaere basisaar + (68 - (today.year - 1963)) — dvs. baseret paa
# fodselsAAR-differencen, ikke paa den fodselsdags-nedjusterede person.alder.
b_alder_kalenderaar_basis = today.year - 1963
forventet_foerste_kalenderaar = today.year + (68 - b_alder_kalenderaar_basis)
check("Partnerens kalenderaars-noegler er bygget paa foedselsaar, ikke fodselsdags-justeret alder",
      forventet_foerste_kalenderaar in skat_a_med_foedselsdato.partner_indkomst_pr_kalenderaar,
      f"forventede noeglen {forventet_foerste_kalenderaar} i {sorted(skat_a_med_foedselsdato.partner_indkomst_pr_kalenderaar.keys())[:3]}...")


print("\n=== Kalenderaar EFTER partnerens tabel-slutning bruger seneste kendte vaerdi, ikke 0 ===")
# B's tabel er bevidst kort (produkt_udb_aar=8 tvinger B's eneste ratepension
# til at stoppe ved 68+8=76), mens A's egen tabel (pensionsalder 67 +
# udbetaling_aar 20) strækker sig til kalenderaar der svarer til B's alder
# et godt stykke efter 76 — den slags aarsforskel der er helt normal naar de
# to har forskellige produkt-varigheder, ikke forskellig levetid.
profil_b_kort = {
    'person': {'alder': 63, 'foedselsaar': 1963},
    'pensionsprodukter': [
        {'selskab': 'PFA', 'produkttype': 'ratepension', 'opsparing': 400_000, 'skat_type': 'S'},
    ],
}
parametre_b_kort = dict(parametre_b, udbetaling_aar=8, produkt_udb_aar={'PFA_ratepension': 8})
solo_b_kort = generer_udbetalingstabel(profil_b_kort, parametre_b_kort)
sidste_b_alder = solo_b_kort['tabel'][-1]['alder']
sidste_b_indtaegtsgrundlag = solo_b_kort['tabel'][-1]['indtaegtsgrundlag_aar']
check("B's forkortede tabel slutter som forventet ved alder 76", sidste_b_alder == 76, f"fik {sidste_b_alder}")

r_kort = husstand.beregn_husstand(profil_a, parametre_a, profil_b_kort, parametre_b_kort)
resultat_a_kort = r_kort['a']
# A's alder svarende til et kalenderaar LÆNGE efter B's tabel er sluttet
# (B's sidste raekke er kalenderaar basisaar+(76-63)=basisaar+13, dvs.
# A's alder dette aar = 67+13=80 — vælg 85 for god margen).
row_a_efter_b_slut = next(row for row in resultat_a_kort['tabel'] if row['alder'] == 85)
row_a_solo_85 = next(row for row in solo_a_direkte['tabel'] if row['alder'] == 85)
tilskrevet_fra_b = row_a_efter_b_slut['indtaegtsgrundlag_aar'] - row_a_solo_85['indtaegtsgrundlag_aar']
check("A's grundlag laenge efter B's tabel er sluttet inkluderer stadig B's SENESTE kendte indkomst (ikke 0)",
      abs(tilskrevet_fra_b - sidste_b_indtaegtsgrundlag) < 1,
      f"tilskrevet fra B: {tilskrevet_fra_b:.0f}, B's sidste kendte: {sidste_b_indtaegtsgrundlag:.0f}")


print("\n=== Uden profil_partner: falder korrekt tilbage til Fase B's lette flow ===")
parametre_let = dict(parametre_a, partner_alder=63, partner_indkomst_aar=150_000)
del parametre_let['civilstand']
parametre_let['civilstand'] = 'gift_samlevende'
resultat_let = generer_udbetalingstabel(profil_a, parametre_let)
check("Fase B's flade partner_indkomst_aar virker stadig uden husstand.py involveret",
      resultat_let['tabel'][-1]['tillaeg_mdr'] >= 0)  # ren "kører uden fejl"-test


print("\n" + "=" * 44)
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    print("Fejlede:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
