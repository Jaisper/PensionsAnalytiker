"""
Test: skat/samspil-nøjagtighed (Fase A) — porterede golden-cases fra
pension-core/test/golden.test.ts (G-02, G-03, G-40, G-41, G-42, G-43).

Samme uformelle print-baserede PASS/FAIL-mønster som de øvrige _test_*.py.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import (
    beregn_tillaegsprocent,
    beregn_tillaegsstyrede_ydelser,
    folkepension_pensionstillaeg_aar,
    _progressiv_skat_total_aar,
    _vaelg_pensionstillaeg_regel,
    _format_skatteeksempel,
    generer_udbetalingstabel,
    folkepension_alder,
    SkatParametre,
)
import satser_2026

PASS, FAIL = [], []
def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))


print("\n=== G-02: mellemskat rammer først over 641.200 kr. ===")
under = _progressiv_skat_total_aar(641_200, 0.25)
over  = _progressiv_skat_total_aar(741_200, 0.25)
check("Ingen skat ved præcis 641.200 kr", under == 0, f"fik {under}")
check("100.000 kr over grænsen giver 7.5% mellemskat (ingen loft ved 25% kommune)",
      abs(over - 100_000 * 0.075) < 0.01, f"fik {over}")

print("\n=== G-03: topskat lægges oven i mellemskatten (under skatteloftet) ===")
r = _progressiv_skat_total_aar(900_000, 0.25)
forventet = (900_000 - 641_200) * 0.075 + (900_000 - 777_900) * 0.075
# 12.01 + 25 + 7.5 + 7.5 = 52.01% < 52.07% topskatteloft — ingen nedslag her
check("900.000 kr: mellemskat + topskat stacker korrekt, ingen loft-nedslag",
      abs(r - forventet) < 0.01, f"fik {r}, ventede {forventet}")

print("\n=== Skatteloft-nedslag udløses når marginalsatsen overstiger loftet ===")
# 12.01 + 25.71(kom+kirke) + 7.5 + 7.5 = 52.72% > 52.07% topskatteloft
med_loft = _progressiv_skat_total_aar(900_000, 0.2571)
uden_nedslag = (900_000 - 641_200) * 0.075 + (900_000 - 777_900) * 0.075
check("Nedslag reducerer den samlede progressive skat under det urene beløb",
      med_loft < uden_nedslag, f"med_loft={med_loft}, uden_nedslag={uden_nedslag}")


print("\n=== G-40: tillægsprocenten er 100 op til 35.700 kr. og 0 ved 99.200 kr. (enlig) ===")
check("0 kr -> 100%",     beregn_tillaegsprocent(0, True) == 100)
check("35.700 kr -> 100%", beregn_tillaegsprocent(35_700, True) == 100)
check("36.335 kr -> 99% (ét trin på 635 kr koster 1 point)",
      beregn_tillaegsprocent(36_335, True) == 99)
check("99.200 kr -> 0%",  beregn_tillaegsprocent(99_200, True) == 0)
check("200.000 kr -> 0%", beregn_tillaegsprocent(200_000, True) == 0)
check("Nulpunktet rammer præcis efter 100 trin",
      (99_200 - 35_700) / 635 == 100)

print("\n=== G-41: gift-varianten aftrappes over 70.600-198.800 kr. i 100 trin á 1.282 kr. ===")
check("Nulpunkt efter præcis 100 trin", (198_800 - 70_600) / 1282 == 100)
check("70.600 kr -> 100%", beregn_tillaegsprocent(70_600, False) == 100)
check("198.800 kr -> 0%",  beregn_tillaegsprocent(198_800, False) == 0)

print("\n=== G-42: tillægsprocenten er IKKE en afledning af pensionstillæggets aftrapning ===")
skat = SkatParametre.fra_pct(25.0, 0.7, True)
tp_ved_99200  = beregn_tillaegsprocent(99_200, True)
tillaeg_99200 = folkepension_pensionstillaeg_aar(99_200, skat)
check("Tillægsprocent er 0 ved 99.200 kr", tp_ved_99200 == 0, f"fik {tp_ved_99200}")
check("Pensionstillægget er urørt ved samme indkomst (bundfradraget)",
      abs(tillaeg_99200 - 104_748) < 1, f"fik {round(tillaeg_99200)}")
ydelser_99200 = beregn_tillaegsstyrede_ydelser(tp_ved_99200, 0)
check("Ældrecheck er 0 når tillægsprocenten er 0",
      ydelser_99200["aeldrecheck"] == 0)

print("\n=== G-43: formuegrænsen er en hård tærskel — kun for ældrechecken ===")
under_formue = beregn_tillaegsstyrede_ydelser(100, 108_000)
over_formue  = beregn_tillaegsstyrede_ydelser(100, 108_001)
check("Ældrecheck fuld værdi ved formue = grænsen",
      abs(under_formue["aeldrecheck"] - satser_2026.AELDRECHECK.vaerdi) < 1,
      f"fik {under_formue['aeldrecheck']}")
check("Ældrecheck bortfalder helt én krone over grænsen",
      over_formue["aeldrecheck"] == 0, f"fik {over_formue['aeldrecheck']}")
check("formue_ok=False registreres korrekt", over_formue["formue_ok"] is False)
check("Mediecheck er IKKE formueafhængig (fortsat > 0 over grænsen)",
      over_formue["mediecheck"] > 0, f"fik {over_formue['mediecheck']}")


print("\n=== Fase B: civilstand vælger det rigtige af de TRE aftrapningstrin ===")
skat_enlig = SkatParametre.fra_pct(25.0, 0.7, enlig=True)
skat_gift  = SkatParametre.fra_pct(25.0, 0.7, enlig=False)
check("Enlig -> TILLAEG_ENLIG uanset partner_er_fp",
      _vaelg_pensionstillaeg_regel(skat_enlig, True) is satser_2026.TILLAEG_ENLIG and
      _vaelg_pensionstillaeg_regel(skat_enlig, False) is satser_2026.TILLAEG_ENLIG)
check("Gift, partner IKKE folkepensionist -> TILLAEG_GIFT_PARTNER_IKKE_PENSIONIST",
      _vaelg_pensionstillaeg_regel(skat_gift, False) is satser_2026.TILLAEG_GIFT_PARTNER_IKKE_PENSIONIST)
check("Gift, partner ER folkepensionist -> TILLAEG_GIFT_BEGGE_PENSIONISTER (tidligere dødt trin)",
      _vaelg_pensionstillaeg_regel(skat_gift, True) is satser_2026.TILLAEG_GIFT_BEGGE_PENSIONISTER)

indtaegt = 300_000
tillaeg_ikke_pensionist = folkepension_pensionstillaeg_aar(indtaegt, skat_gift, partner_er_fp=False)
tillaeg_begge_pensionister = folkepension_pensionstillaeg_aar(indtaegt, skat_gift, partner_er_fp=True)
check("16% aftrapning (begge pensionister) giver højere tillæg end 32% (partner ikke pensionist) ved samme indkomst",
      tillaeg_begge_pensionister > tillaeg_ikke_pensionist,
      f"32%: {round(tillaeg_ikke_pensionist)}, 16%: {round(tillaeg_begge_pensionister)}")


print("\n=== Fase B: overgangsår — regel skifter automatisk når partner når egen folkepensionsalder ===")
profil_b = {
    'person': {'alder': 60, 'foedselsaar': 1966},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension', 'opsparing': 2_500_000, 'skat_type': 'S'},
    ],
}
parametre_b = {
    'pensionsalder': 65, 'udbetaling_aar': 25, 'afkast_pct': 3.0,
    'kommuneskat_pct': 25.0, 'kirkeskat_pct': 0.7,
    'civilstand': 'gift_samlevende', 'partner_alder': 55,  # foedselsaar 1971 -> egen fp-alder 70
}
r_b = generer_udbetalingstabel(profil_b, parametre_b)
fp_alder_b = r_b['fp_alder']
check("Primær persons fp-alder er 68 (foedselsaar 1966)", fp_alder_b == 68, f"fik {fp_alder_b}")
partner_fp_alder = folkepension_alder(2026 - 55)
check("Partnerens egen fp-alder er 70 (foedselsaar 1971)", partner_fp_alder == 70, f"fik {partner_fp_alder}")
# Partner (alder 55 ved primær alder 60) naar egen fp-alder 70 naar primær er 75.
row74 = next(row for row in r_b['tabel'] if row['alder'] == 74)
row75 = next(row for row in r_b['tabel'] if row['alder'] == 75)

def _indtaegtsgrundlag(row):
    s_brutto = sum(d['mdr_brutto'] * 12 for d in row['produkter'].values())
    return s_brutto + row.get('atp_mdr_brutto', 0.0) * 12

skat_gift_check = SkatParametre.fra_pct(25.0, 0.7, enlig=False)
forventet_74 = folkepension_pensionstillaeg_aar(_indtaegtsgrundlag(row74), skat_gift_check, partner_er_fp=False) / 12
forventet_75 = folkepension_pensionstillaeg_aar(_indtaegtsgrundlag(row75), skat_gift_check, partner_er_fp=True) / 12
check("Alder 74 (partner endnu ikke fp): tillæg matcher 32%-aftrapning",
      abs(row74['tillaeg_mdr'] - forventet_74) < 1, f"fik {row74['tillaeg_mdr']:.0f}, ventede {forventet_74:.0f}")
check("Alder 75 (partner netop blevet fp): tillæg matcher 16%-aftrapning",
      abs(row75['tillaeg_mdr'] - forventet_75) < 1, f"fik {row75['tillaeg_mdr']:.0f}, ventede {forventet_75:.0f}")
check("Overgangen giver et synligt hop i tillægget (16% er lempeligere end 32%)",
      row75['tillaeg_mdr'] > row74['tillaeg_mdr'],
      f"74: {row74['tillaeg_mdr']:.0f}, 75: {row75['tillaeg_mdr']:.0f}")


print("\n=== Fase B: partnerens indkomst tælles KUN med når partneren selv er folkepensionist ===")
def _tillaeg_ved_alder(alder_maal, partner_indkomst):
    p = dict(parametre_b, partner_indkomst_aar=partner_indkomst)
    r = generer_udbetalingstabel(profil_b, p)
    row = next(row for row in r['tabel'] if row['alder'] == alder_maal)
    return row['tillaeg_mdr']

foer_0   = _tillaeg_ved_alder(70, 0)        # partner ikke fp endnu (fp ved 75)
foer_100k = _tillaeg_ved_alder(70, 100_000)
check("Før partner er fp: partner-indkomst paavirker IKKE tillægget",
      abs(foer_0 - foer_100k) < 1, f"0 kr: {foer_0:.0f}, 100.000 kr: {foer_100k:.0f}")

efter_0   = _tillaeg_ved_alder(80, 0)        # partner er fp fra 75
efter_300k = _tillaeg_ved_alder(80, 300_000)  # over bundfradraget på 198.800 kr for gifte
check("Efter partner er fp: partner-indkomst over bundfradraget REDUCERER tillægget",
      efter_300k < efter_0, f"0 kr: {efter_0:.0f}, 300.000 kr: {efter_300k:.0f}")


print("\n=== Fase B: enlig (default) er uændret af civilstand-udvidelsen ===")
parametre_enlig = dict(parametre_b)
del parametre_enlig['civilstand']; del parametre_enlig['partner_alder']
r_enlig = generer_udbetalingstabel(profil_b, parametre_enlig)
row74_enlig = next(row for row in r_enlig['tabel'] if row['alder'] == 74)
forventet_enlig = folkepension_pensionstillaeg_aar(_indtaegtsgrundlag(row74_enlig), SkatParametre.fra_pct(25.0, 0.7, True)) / 12
check("Uden civilstand angivet: falder korrekt tilbage til enlig-satsen",
      abs(row74_enlig['tillaeg_mdr'] - forventet_enlig) < 1,
      f"fik {row74_enlig['tillaeg_mdr']:.0f}, ventede {forventet_enlig:.0f}")

print("\n=== Skattegrænser eskaleres for fremtidige år (satsregulerings-antagelse) ===")
# Bruger-rapporteret: en profil med mange aar til pensionsalderen viste
# STADIG 2026-niveauets raa skattegraense i skatteeksemplet/over_topskat,
# uanset hvor langt ude i fremtiden det paagaeldende aar faktisk laa —
# inkonsistent med at FP/ATP allerede satsreguleres. _progressiv_skat_total_aar
# skal nu tage en escalering-faktor der skalerer samtlige PROGRESSION-graenser.
raa_mellemskat_graense = satser_2026.PROGRESSION[0].bundgraense
skat_uden_eskalering = _progressiv_skat_total_aar(raa_mellemskat_graense + 50_000, 0.25)
check("Uden escalering-parameter (default 1.0): uaendret opfoersel (bagudkompatibelt)",
      skat_uden_eskalering > 0, f"fik {skat_uden_eskalering}")

skat_med_eskalering = _progressiv_skat_total_aar(raa_mellemskat_graense + 50_000, 0.25, escalering=1.20)
check("Med escalering=1.20: samme PI rammer IKKE laengere mellemskattegraensen (graensen er ogsaa 20% hoejere)",
      skat_med_eskalering == 0, f"fik {skat_med_eskalering}")

skat_over_eskaleret_graense = _progressiv_skat_total_aar(raa_mellemskat_graense * 1.20 + 50_000, 0.25, escalering=1.20)
check("Med escalering=1.20: PI over DEN ESKALEREDE graense giver stadig mellemskat",
      skat_over_eskaleret_graense > 0, f"fik {skat_over_eskaleret_graense}")

# Fuld integrationstest: en person der er 55 i dag men foerst gaar paa
# pension som 70-aarig (15 aars eskalering ved 2% inflation) skal have en
# markant HOJERE mellemskattegraense i sit "aar 1"-skatteeksempel end den
# ueskalerede 2026-graense — ellers ville et rigt fjernt fremtidsaar
# fejlagtigt blive vist som ramt af mellem-/topskat naesten hver gang.
profil_esk = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension', 'opsparing': 6_000_000, 'skat_type': 'S'},
    ],
}
parametre_esk_fjern = {'pensionsalder': 70, 'afkast_pct': 5.0, 'udbetaling_aar': 20, 'inflation_pct': 2.0}
r_fjern = generer_udbetalingstabel(profil_esk, parametre_esk_fjern)
loebende_fjern = [p for p in r_fjern['produkter'] if p['udb_type'] != 'engangsbeloeb']
skatte_row_fjern = next(row for row in r_fjern['tabel'] if row['total_netto_mdr'] > 0)
skatteeksempel_tekst = _format_skatteeksempel(skatte_row_fjern, loebende_fjern, r_fjern['parametre'], r_fjern['fp_alder'])
forventet_eskaleret_graense_str = f"{raa_mellemskat_graense * (1.02 ** 15):,.0f}".replace(",", ".")
raa_2026_graense_str = f"{raa_mellemskat_graense:,.0f}".replace(",", ".")
check("Skatteeksemplet for et 15-år-fjernt pensionsår viser den ESKALEREDE grænse, ikke 2026-tallet",
      forventet_eskaleret_graense_str in skatteeksempel_tekst,
      f"forventede '{forventet_eskaleret_graense_str}' et sted i teksten")
check("2026-rå-grænsen (641.200) optræder IKKE som den anvendte grænse i eksemplet",
      f"PI over {raa_2026_graense_str} kr" not in skatteeksempel_tekst,
      "den rå 2026-grænse blev brugt direkte i stedet for den eskalerede")

print("\n" + "=" * 44)
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    print("Fejlede:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
