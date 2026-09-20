"""
Test: spredning af et engangsbeløb (kapitalpension/aldersopsparing) over flere
år i stedet for udbetalt samlet ("kapital_rate", engine.py) — det brugeren bad
om efter sin fars påstand om at en engangsudbetaling kørt over 10 år nyder
godt af pensionsafkastskat (PAL) i stedet for almindelig kapitalindkomstskat.

Samme uformelle print-baserede PASS/FAIL-mønster som de øvrige _test_*.py.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import generer_udbetalingstabel, A_SKAT_NETTO_FAKTOR
import sekventering as sk

PASS, FAIL = [], []
def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))


profil_kapital = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'PFA', 'produkttype': 'kapitalpension', 'opsparing': 1_000_000, 'skat_type': 'A'},
    ],
}
profil_alders = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'PFA', 'produkttype': 'aldersopsparing', 'opsparing': 1_000_000, 'skat_type': 'F'},
    ],
}
parametre_basis = {'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20}
KEY = 'PFA_kapitalpension'
KEY_ALDERS = 'PFA_aldersopsparing'


print("\n=== Uden override: uændret, eksisterende adfærd (regression) ===")
r_default = generer_udbetalingstabel(profil_kapital, parametre_basis)
prod_default = next(p for p in r_default['produkter'] if p['key'] == KEY)
check("udb_type er stadig 'engangsbeloeb' uden override",
      prod_default['udb_type'] == 'engangsbeloeb', f"fik {prod_default['udb_type']}")
check("udb_aar er 0 uden override", prod_default['udb_aar'] == 0)
check("Produktet indgår i result['engangsbeloeb']",
      any(p['key'] == KEY for p in r_default['engangsbeloeb']))
check("Ingen advarsel om spredning uden override",
      not any('spredt over' in a for a in r_default['advarsler']))

r_tom_dict = generer_udbetalingstabel(profil_kapital, dict(parametre_basis, produkt_udb_aar={}))
check("Et TOMT produkt_udb_aar-dict giver samme resultat som slet intet override",
      r_tom_dict['produkter'] == r_default['produkter'])


print("\n=== Med override: produktet spredes i stedet for udbetalt samlet ===")
parametre_spredt = dict(parametre_basis, produkt_udb_aar={KEY: 10})
r_spredt = generer_udbetalingstabel(profil_kapital, parametre_spredt)
prod_spredt = next(p for p in r_spredt['produkter'] if p['key'] == KEY)
check("udb_type bliver 'kapital_rate' med override",
      prod_spredt['udb_type'] == 'kapital_rate', f"fik {prod_spredt['udb_type']}")
check("udb_aar matcher overridet (10)", prod_spredt['udb_aar'] == 10)
check("stopper_ved_alder = start_alder + 10",
      prod_spredt['stopper_ved_alder'] == prod_spredt['start_alder'] + 10,
      f"start={prod_spredt['start_alder']}, stopper={prod_spredt['stopper_ved_alder']}")
check("mdr_brutto er positiv (annuiseret udbetaling, ikke længere ét samlet beløb)",
      prod_spredt['mdr_brutto'] > 0, f"fik {prod_spredt['mdr_brutto']}")
check("Produktet indgår IKKE længere i result['engangsbeloeb']",
      not any(p['key'] == KEY for p in r_spredt['engangsbeloeb']))
check("Der er en advarsel der forklarer spredningen og nævner PAL",
      any('spredt over 10 år' in a and 'PAL' in a for a in r_spredt['advarsler']),
      f"advarsler={r_spredt['advarsler']}")


print("\n=== Skattebehandling er uændret af spredningen — kun timingen ændres ===")
row_med_udb = next(
    row for row in r_spredt['tabel']
    if row['produkter'].get(KEY, {}).get('mdr_brutto', 0) > 0
)
data_kapital = row_med_udb['produkter'][KEY]
faktisk_ratio = data_kapital['mdr_netto'] / data_kapital['mdr_brutto']
check("Kapitalpension (skat_type A) beskattes stadig med den flade 40%-afgift, ikke personlig indkomstskat",
      abs(faktisk_ratio - A_SKAT_NETTO_FAKTOR) < 0.001,
      f"fik ratio {faktisk_ratio:.4f}, ventede {A_SKAT_NETTO_FAKTOR}")

r_alders_spredt = generer_udbetalingstabel(profil_alders, dict(parametre_basis, produkt_udb_aar={KEY_ALDERS: 10}))
row_alders = next(
    row for row in r_alders_spredt['tabel']
    if row['produkter'].get(KEY_ALDERS, {}).get('mdr_brutto', 0) > 0
)
data_alders = row_alders['produkter'][KEY_ALDERS]
check("Aldersopsparing (skat_type F) forbliver skattefri: netto == brutto",
      abs(data_alders['mdr_netto'] - data_alders['mdr_brutto']) < 0.01,
      f"netto={data_alders['mdr_netto']:.2f}, brutto={data_alders['mdr_brutto']:.2f}")

check("Spredningen udløser ikke mellemskat/topskat (kun 'S'-typer og FP/ATP/tillæg tæller til personlig indkomst)",
      row_med_udb.get('over_topskat', False) is False, f"fik {row_med_udb.get('over_topskat')}")


print("\n=== Den reelle fordel: PAL-vækst (r) vs. buffer-vækst (r_buffer) på den ikke-udbetalte rest ===")
# jaevn_niveau_realt (samme mål optimeringens egen score() bruger til at
# sammenligne planer) er det rigtige sammenligningsgrundlag her — et simpelt
# nominelt sum-af-udbetalinger ville uundgåeligt favorisere spredning uanset
# rente, fordi en talrække udbetalt over tid, der selv bliver ved med at
# forrentes, altid nominelt summerer til mere endét beløb i dag med samme
# nutidsværdi (tidsværdien af penge, ikke en reel fordel i sig selv).
from engine import jaevn_niveau_realt

# Ved 6% afkast og default 27% buffer-skat er r_buffer = 6% * 0.73 = 4.38% —
# lavere end r. At lade pengene blive i ordningen og vokse til r i stedet for
# at forlade den med det samme og vokse til r_buffer bør derfor give et
# reelt højere fast månedsniveau, ikke kun en anden fordeling over tid.
jaevn_lump   = jaevn_niveau_realt(r_default)
jaevn_spredt = jaevn_niveau_realt(r_spredt)
check("Det faste, reale månedsniveau er højere når beløbet spredes (PAL) end når det tages som ét engangsbeløb (buffer-vækst)",
      jaevn_spredt > jaevn_lump, f"engangsbeløb={jaevn_lump} kr/mdr, spredt={jaevn_spredt} kr/mdr")

# Modsat: uden nogen buffer-skat (r_buffer == r, en kunstig kontrol) er der
# INGEN rente-forskel at hente — spredning bør derfor være omtrent neutral,
# ikke en selvstændig fordel uanset rente-sammenhæng.
parametre_lav_afkast = dict(parametre_basis, afkast_pct=2.0, engangs_buffer_skat_pct=0.0)
jaevn_kontrol_lump   = jaevn_niveau_realt(generer_udbetalingstabel(profil_kapital, parametre_lav_afkast))
jaevn_kontrol_spredt = jaevn_niveau_realt(generer_udbetalingstabel(
    profil_kapital, dict(parametre_lav_afkast, produkt_udb_aar={KEY: 10})
))
check("Uden buffer-skat (r_buffer == r) er spredning omtrent neutral — bekræfter at gevinsten ovenfor kommer fra rente-forskellen, ikke fra selve kodestien",
      abs(jaevn_kontrol_spredt - jaevn_kontrol_lump) < jaevn_kontrol_lump * 0.02,
      f"lump={jaevn_kontrol_lump} kr/mdr, spredt={jaevn_kontrol_spredt} kr/mdr")


print("\n=== sekventering.py: optimeringen kan selv finde og vælge at sprede ===")
# Kapitalpensionen alene (uden andet produkt) er et degenereret søgerum for
# OPTIMERINGEN specifikt: udbetalt som ét engangsbeløb giver et fuldstændigt
# indkomsthul fra pensionsalder til folkepensionsalderen (70 år, jf.
# foedselsaar 1970) — som scoren() altid diskvalificerer helt uafhængigt af
# hvor godt bufferen ellers udjævner det (se score()'s ubetingede hul-tjek) —
# så selv baseline-planen (periode 0) er infeasible mod sig selv. Det er ikke
# en fejl i kapital_rate, men et eksisterende, generelt vilkår i
# sekventering.py (se _test_sekventering.py) og ville ramme ratepension
# nøjagtigt lige så hårdt hvis DEN var det eneste produkt i profilen. Tilføj
# derfor en lille ratepension der selv dækker hullet, så testen isolerer
# spørgsmålet den faktisk stiller: vælger optimeringen at sprede kapital-
# pensionen NÅR det er en reel mulighed, ikke en nødvendighed for overhovedet
# at finde en gennemførlig plan.
profil_med_bro = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'PFA', 'produkttype': 'kapitalpension', 'opsparing': 1_000_000, 'skat_type': 'A'},
        {'selskab': 'Danica', 'produkttype': 'ratepension', 'opsparing': 800_000, 'skat_type': 'S'},
    ],
}
res = sk.optimer(profil_med_bro, parametre_basis)
check("Optimeringen fandt en løsning", res.bedste is not None)
if res.bedste:
    valgt_periode = res.bedste.produkt_udb_aar.get(KEY)
    check("Ved 6% afkast (PAL-fordel reel) vælger optimeringen at sprede kapitalpensionen frem for ét engangsbeløb",
          valgt_periode is not None and valgt_periode > 0,
          f"fik produkt_udb_aar={res.bedste.produkt_udb_aar}")


print("\n=== Aldersopsparing-loft (PBL § 16 stk. 1, satser_2026.ALDERSOPSPARING_LOFT*) håndhæves ===")
# estimated_pmt sat eksplicit langt over begge lofter, så klampningen er
# utvetydig — uafhængigt af den sædvanlige pv/total_opsparing-udledning.
import satser_2026

profil_alders_fjern = {
    'person': {'alder': 45, 'foedselsaar': 1980},
    'pensionsprodukter': [
        {'selskab': 'PFA', 'produkttype': 'aldersopsparing', 'opsparing': 100_000,
         'estimated_pmt': 100_000, 'skat_type': 'F'},
    ],
}
# fp_alder for foedselsaar 1980 er 70 (se folkepension_alder) — pensionsalder
# 60 giver 10 års afstand, uden for ALDERSOPSPARING_FORHOEJET_AAR_FOER_FOLKEPENSION
# (7 år) — produktets start-alder (default = pensionsalder) rammer derfor
# grundloftet, ikke det forhøjede.
r_fjern_fp = generer_udbetalingstabel(profil_alders_fjern, {'pensionsalder': 60, 'afkast_pct': 4.0, 'udbetaling_aar': 25})
prod_fjern = next(p for p in r_fjern_fp['produkter'] if 'aldersopsparing' in p['produkttype'].lower())
check(f"Langt fra folkepensionsalderen: pmt klampes til grundloftet ({satser_2026.ALDERSOPSPARING_LOFT.vaerdi:,.0f} kr)".replace(",", "."),
      prod_fjern['pmt'] == satser_2026.ALDERSOPSPARING_LOFT.vaerdi, f"fik pmt={prod_fjern['pmt']}")

profil_alders_naer_fp = {
    'person': {'alder': 60, 'foedselsaar': 1965},
    'pensionsprodukter': [
        {'selskab': 'PFA', 'produkttype': 'aldersopsparing', 'opsparing': 100_000,
         'estimated_pmt': 100_000, 'skat_type': 'F'},
    ],
}
# fp_alder for foedselsaar 1965 (67 år) og pensionsalder 65 -> kun 2 år til fp,
# inden for det forhøjede loftets 7-års-vindue.
r_naer_fp = generer_udbetalingstabel(profil_alders_naer_fp, {'pensionsalder': 65, 'afkast_pct': 4.0, 'udbetaling_aar': 20})
prod_naer = next(p for p in r_naer_fp['produkter'] if 'aldersopsparing' in p['produkttype'].lower())
check(f"Inden for {satser_2026.ALDERSOPSPARING_FORHOEJET_AAR_FOER_FOLKEPENSION} år af folkepensionsalderen: "
      f"pmt klampes til det FORHØJEDE loft ({satser_2026.ALDERSOPSPARING_LOFT_FORHOEJET.vaerdi:,.0f} kr)".replace(",", "."),
      prod_naer['pmt'] == satser_2026.ALDERSOPSPARING_LOFT_FORHOEJET.vaerdi, f"fik pmt={prod_naer['pmt']}")
check("Det forhøjede loft er reelt højere end grundloftet (ellers ville testen ovenfor ikke skelne dem)",
      satser_2026.ALDERSOPSPARING_LOFT_FORHOEJET.vaerdi > satser_2026.ALDERSOPSPARING_LOFT.vaerdi)


print("\n" + "=" * 44)
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    print("Fejlede:")
    for f in FAIL:
        print(f"  - {f}")
