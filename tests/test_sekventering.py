"""Test: sekventeringsoptimering — soegerum, realisme-tjek, husstandskobling."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sekventering as sk
import husstand
import satser_2026
from engine import generer_udbetalingstabel, jaevn_niveau_realt, _fp_venteprocent

PASS, FAIL = [], []
def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

profil = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension',    'opsparing': 800000, 'skat_type': 'S'},
        {'selskab': 'PFA',    'produkttype': 'aldersopsparing','opsparing': 200000, 'skat_type': 'F'},
    ],
}
parametre = {'pensionsalder': 65, 'afkast_pct': 6.0, 'udbetaling_aar': 20}


print("\n=== Søgerum: ratepensions-periode, buffer-egnet engangsbeløb OG opsætning inkluderet ===")
baseline = generer_udbetalingstabel(profil, parametre)
noegler, dims = sk._byg_soegerum(baseline['produkter'], baseline['fp_alder'], baseline['pensionsalder'], 0)
check("Engangsbeloeb (aldersopsparing, default i_buffer=True) FÅR nu en start-alder-dimension",
      'start:PFA_aldersopsparing' in noegler, f"noegler={noegler}")
check("Både den løbende ratepension og engangsbeløbet får en start-alder-dimension",
      len([n for n in noegler if n.startswith('start:')]) == 2, f"noegler={noegler}")
check("Ratepensionen FÅR nu en periode-dimension",
      'periode:Danica_ratepension' in noegler, f"noegler={noegler}")
check("Aldersopsparingen (engangsbeløb) FÅR OGSÅ nu en periode-dimension "
      "(spred over flere år i stedet for udbetalt samlet, se engine.py's kapital_rate)",
      'periode:PFA_aldersopsparing' in noegler, f"noegler={noegler}")
periode_dim = dims[noegler.index('periode:Danica_ratepension')]
periode_vaerdier = [v for _, _, v in periode_dim]
check("Ratepensionens periode-dimension respekterer det lovmæssige gulv på 10 år",
      min(periode_vaerdier) >= sk.MIN_RATEPENSION_UDB_AAR, f"periode_vaerdier={sorted(periode_vaerdier)}")
engangs_periode_dim = dims[noegler.index('periode:PFA_aldersopsparing')]
engangs_periode_vaerdier = [v for _, _, v in engangs_periode_dim]
check("Engangsbeløbets periode-dimension inkluderer 0 (nuværende plan: udbetalt samlet)",
      0 in engangs_periode_vaerdier, f"periode_vaerdier={sorted(engangs_periode_vaerdier)}")
check("Engangsbeløbets periode-dimension respekterer samme 10-års-gulv når spredt (0 undtaget)",
      min(v for v in engangs_periode_vaerdier if v > 0) >= sk.MIN_RATEPENSION_UDB_AAR,
      f"periode_vaerdier={sorted(engangs_periode_vaerdier)}")
check("Opsaetnings-dimensionen findes",
      'opsaet:folkepension' in noegler)

print("\n=== Engangsbeløb der er FRAVALGT bufferen (i_buffer=False) forbliver ekskluderet ===")
parametre_ekskl = dict(parametre, produkt_i_buffer={'PFA_aldersopsparing': False})
baseline_ekskl = generer_udbetalingstabel(profil, parametre_ekskl)
noegler_ekskl, _ = sk._byg_soegerum(
    baseline_ekskl['produkter'], baseline_ekskl['fp_alder'], baseline_ekskl['pensionsalder'], 0
)
check("Engangsbeloeb med i_buffer=False får IKKE en start-alder-dimension",
      'start:PFA_aldersopsparing' not in noegler_ekskl, f"noegler={noegler_ekskl}")


print("\n=== Søgerummet inkluderer ALTID brugerens egne start-alder-valg ===")
parametre_egen = dict(parametre, produkt_start_aldre={'Danica_ratepension': 80})
baseline_egen = generer_udbetalingstabel(profil, parametre_egen)
_, dims_egen = sk._byg_soegerum(baseline_egen['produkter'], baseline_egen['fp_alder'], baseline_egen['pensionsalder'], 0)
start_kandidater = [v for slag, _, v in dims_egen[0]]
check("80 (brugerens egen, langt-udenfor-standard værdi) er med i søgerummet",
      80 in start_kandidater, f"kandidater={start_kandidater}")


print("\n=== Søgerummet ekskluderer aldrig brugerens eget valgte pensionsalder ===")
profil_tidlig = {
    'person': {'alder': 55, 'foedselsaar': 1970},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension', 'opsparing': 800_000, 'skat_type': 'S'},
    ],
}
parametre_tidlig = {'pensionsalder': 62, 'afkast_pct': 5.0, 'udbetaling_aar': 20}
baseline_tidlig = generer_udbetalingstabel(profil_tidlig, parametre_tidlig)
check("Den lovmæssige tommelfingerregel (fp_alder-5) ligger over 62 i dette tilfælde",
      sk.tidligste_private_pensionsalder(baseline_tidlig['fp_alder']) > 62,
      f"fp_alder={baseline_tidlig['fp_alder']}")
_, dims_tidlig = sk._byg_soegerum(baseline_tidlig['produkter'], baseline_tidlig['fp_alder'], 62, 0)
check("62 år er alligevel med i søgerummet",
      62 in [v for slag, _, v in dims_tidlig[0]])


print("\n=== Multi-start: store søgerum bruger spredte, deterministiske udgangspunkter ===")
profil_stor = {
    'person': {'alder': 58, 'foedselsaar': 1966},
    'pensionsprodukter': [
        {'selskab': f'Sel{i}', 'produkttype': 'ratepension', 'opsparing': 500_000, 'skat_type': 'S'}
        for i in range(4)
    ],
}
parametre_stor = {'pensionsalder': 62, 'afkast_pct': 5.0, 'udbetaling_aar': 20}
baseline_stor = generer_udbetalingstabel(profil_stor, parametre_stor)
_, dims_stor = sk._byg_soegerum(baseline_stor['produkter'], baseline_stor['fp_alder'], 62, 0)
default_vektor_stor = sk._default_vektor(baseline_stor['produkter'], 0)
mulige_stor = 1
for _d in dims_stor:
    mulige_stor *= len(_d)
startvektorer = sk._byg_startvektorer(dims_stor, default_vektor_stor)
check("Det fulde søgerum er stort (multi-start er dermed relevant, ikke udtømmende søgning)",
      mulige_stor > 500, f"mulige={mulige_stor}")
check("Der genereres flere end ét udgangspunkt (default + kvantiler)",
      len(startvektorer) > 1, f"fik {len(startvektorer)} startvektorer")
foerste_dim_vaerdier = {v[0][2] for v in startvektorer}
check("Den FØRSTE dimension har reel spredning på tværs af udgangspunkterne (ikke alle identiske)",
      len(foerste_dim_vaerdier) > 1, f"unikke værdier for første dimension: {sorted(foerste_dim_vaerdier)}")
check("Default-vektoren (brugerens nuværende plan) indgår altid som udgangspunkt",
      default_vektor_stor in startvektorer)
check("To kald med samme input giver nøjagtig samme udgangspunkter (ingen tilfældighed)",
      sk._byg_startvektorer(dims_stor, default_vektor_stor) == startvektorer)


print("\n=== Optimeringen må ALDRIG gøre et overlappende år ringere end default-planens eget ===")
# Reproducerer den brugerrapporterede fejl: et forløb med flere produkter,
# hvor optimeringen tidligere kunne finde på at udskyde noget for at presse
# et højere (men urealistisk) udjævnet tal frem. Det tidligere "bufferunderskud"-
# tjek var virkningsløst netop for denne profil-type (et stort pre-pensions
# engangsbeløb holder bufferen ikke-negativ for ALLE kandidater).
profil_lumpsum = {
    'person': {'alder': 58, 'foedselsaar': 1966},
    'pensionsprodukter': [
        {'selskab': 'A', 'produkttype': 'aldersopsparing',   'opsparing': 3_000_000, 'skat_type': 'F'},
        {'selskab': 'B', 'produkttype': 'livsvarig pension', 'opsparing': 250_000,   'skat_type': 'S'},
        {'selskab': 'C', 'produkttype': 'ratepension',       'opsparing': 1_500_000, 'skat_type': 'S'},
    ],
}
parametre_lumpsum = {'pensionsalder': 62, 'afkast_pct': 9.0, 'udbetaling_aar': 20}
baseline_lumpsum = generer_udbetalingstabel(profil_lumpsum, parametre_lumpsum)
res_lumpsum = sk.optimer(profil_lumpsum, parametre_lumpsum)
check("Der findes gennemførlige planer (mindst default-planen selv)", res_lumpsum.antal_gennemfoerlige > 0)
if res_lumpsum.bedste:
    check("Vinderplanen starter alle løbende produkter ved pensionsalderen (ingen kunstig udskydelse)",
          all(v == 62 for v in res_lumpsum.bedste.produkt_start_aldre.values()),
          f"fik {res_lumpsum.bedste.produkt_start_aldre}")
    raa_pr_alder = sk._raa_indkomst_pr_alder(res_lumpsum.bedste.resultat)
    baseline_raa = sk._raa_indkomst_pr_alder(baseline_lumpsum)
    check("Vinderplanens laveste rå alder-indkomst er ikke under default-planens eget værste punkt",
          min(raa_pr_alder.values()) >= min(baseline_raa.values()) * sk.RAA_AAR_TOLERANCE,
          f"vinder-min={min(raa_pr_alder.values())}, baseline-min={min(baseline_raa.values())}")


def _baseline_hardship(resultat):
    raa = sk._raa_indkomst_pr_alder(resultat)
    jaevn = jaevn_niveau_realt(resultat)
    graense = jaevn * sk.HARDSHIP_ANDEL_AF_JAEVN
    return sum(1 for v in raa.values() if v < graense)


def _syntetisk_resultat(raa_vaerdier, buffer_rest_vaerdier=None):
    """Bygger et minimalt resultat-dict der omgår engine.py helt, til at
    isolere ét enkelt tjek i score() ad gangen fra de andre."""
    buffer_rest_vaerdier = buffer_rest_vaerdier or [0] * len(raa_vaerdier)
    return {
        "pensionsalder": 65,
        "jaevn_tabel": [
            {"fase": "pension", "alder": 65 + i, "jaevn_mdr_real": 1000, "jaevn_mdr": 1000,
             "normal_mdr_real": v, "normal_mdr": v, "buffer_rest": b}
            for i, (v, b) in enumerate(zip(raa_vaerdier, buffer_rest_vaerdier))
        ],
        "tabel": [
            {"alder": 65 + i, "total_netto_mdr": v} for i, v in enumerate(raa_vaerdier)
        ],
    }


print("\n=== Direkte test af det absolutte værste-mod-værste-tjek (score) ===")
baseline_laveste_raa = min(sk._raa_indkomst_pr_alder(baseline).values())
baseline_hardship = _baseline_hardship(baseline)
baseline_laveste_buffer = sk._laveste_buffer_rest(baseline)
baseline_buffer_hul = sk._antal_buffer_hul_aar(baseline)
score_ok, jaevn_ok = sk.score(
    baseline, sk.Objektiv(), baseline_laveste_raa, baseline_hardship, baseline_laveste_buffer, baseline_buffer_hul
)
check("Basisplanen er selv netop gennemførlig mod sin egen reference",
      score_ok != float('-inf'), f"score={score_ok}")
check("Score er identisk med jaevn_niveau_realt når planen er gennemførlig",
      score_ok == jaevn_niveau_realt(baseline))

resultat_udskudt = generer_udbetalingstabel(
    profil_tidlig, dict(parametre_tidlig, produkt_start_aldre={'Danica_ratepension': 75})
)
baseline_laveste_raa_tidlig = min(sk._raa_indkomst_pr_alder(baseline_tidlig).values())
baseline_hardship_tidlig = _baseline_hardship(baseline_tidlig)
baseline_laveste_buffer_tidlig = sk._laveste_buffer_rest(baseline_tidlig)
baseline_buffer_hul_tidlig = sk._antal_buffer_hul_aar(baseline_tidlig)
score_udskudt, _ = sk.score(
    resultat_udskudt, sk.Objektiv(), baseline_laveste_raa_tidlig, baseline_hardship_tidlig,
    baseline_laveste_buffer_tidlig, baseline_buffer_hul_tidlig,
)
check("En plan der udskyder alt og skaber et fuldstændigt indkomst-hul diskvalificeres",
      score_udskudt == float('-inf'), f"score={score_udskudt}")

print("\n=== Værste-mod-værste tillader legitim omlægning der en per-alder-sammenligning ville have blokeret ===")
# Konkret profil hvor det ISOLERET SET (dybde- og jaevn-niveau) ser ud som en
# forbedring at flytte én livsvarig pensions start-alder væk fra
# pensionsalderen — den gamle per-alder-sammenligning ville have
# diskvalificeret det for at gøre flere overlappende år isoleret set lavere.
# Det viser sig dog at denne SPECIFIKKE omlægning kræver at bufferen låner
# markant mere mod S1's endnu-ikke-modtagne udbetaling end default-planen
# allerede gør — langt over bufferdæknings-tjekkets tolerance. Det er altså
# IKKE en reel forbedring, og det nye tjek afviser den korrekt, blot af en
# anden (og mere præcis) grund end den gamle per-alder-sammenligning ville
# have gjort. Se den ISOLEREDE syntetiske test lige efter for et rendyrket
# eksempel på selve dybde-mekanismen, uden bufferens indblanding.
profil_omlaeg = {
    'person': {'alder': 55, 'foedselsaar': 1971},
    'pensionsprodukter': [
        {'selskab': 'S0', 'produkttype': 'livsvarig pension', 'opsparing': 1_500_000,'skat_type': 'S'},
        {'selskab': 'S1', 'produkttype': 'ratepension',       'opsparing': 300_000,  'skat_type': 'S'},
        {'selskab': 'S2', 'produkttype': 'livsvarig pension', 'opsparing': 800_000,  'skat_type': 'S'},
    ],
}
parametre_omlaeg = {'pensionsalder': 60, 'afkast_pct': 4.0, 'udbetaling_aar': 15, 'inflation_pct': 2.0}
baseline_omlaeg = generer_udbetalingstabel(profil_omlaeg, parametre_omlaeg)
baseline_raa_omlaeg = sk._raa_indkomst_pr_alder(baseline_omlaeg)
baseline_laveste_omlaeg = min(baseline_raa_omlaeg.values())
baseline_jaevn_omlaeg = jaevn_niveau_realt(baseline_omlaeg)

resultat_flyttet = generer_udbetalingstabel(
    profil_omlaeg, dict(parametre_omlaeg, produkt_start_aldre={'S1_ratepension': 61})
)
raa_flyttet = sk._raa_indkomst_pr_alder(resultat_flyttet)
jaevn_flyttet = jaevn_niveau_realt(resultat_flyttet)
gjorde_nogle_aldre_ringere = any(
    raa_flyttet.get(alder, 0) < vaerdi * sk.RAA_AAR_TOLERANCE
    for alder, vaerdi in baseline_raa_omlaeg.items()
)
check("Denne omlægning gør faktisk flere overlappende år ringere end default-planens eget (den gamle checks trigger)",
      gjorde_nogle_aldre_ringere)
check("Omlægningen ser isoleret ud til at give et højere jaevn-niveau end default-planen",
      jaevn_flyttet > baseline_jaevn_omlaeg, f"flyttet={jaevn_flyttet}, baseline={baseline_jaevn_omlaeg}")
check("Omlægningens eget værste punkt er stadig over default-planens værste punkt (dybde-tjekket alene ville acceptere)",
      min(raa_flyttet.values()) >= baseline_laveste_omlaeg * sk.RAA_AAR_TOLERANCE)
baseline_hardship_omlaeg = _baseline_hardship(baseline_omlaeg)
baseline_laveste_buffer_omlaeg = sk._laveste_buffer_rest(baseline_omlaeg)
baseline_buffer_hul_omlaeg = sk._antal_buffer_hul_aar(baseline_omlaeg)
check("Men omlægningen kræver et bufferunderskud langt dybere end default-planens (aldrig negativt)",
      sk._laveste_buffer_rest(resultat_flyttet) < baseline_laveste_buffer_omlaeg - baseline_jaevn_omlaeg,
      f"flyttet={sk._laveste_buffer_rest(resultat_flyttet)}, baseline={baseline_laveste_buffer_omlaeg}")
score_flyttet, _ = sk.score(
    resultat_flyttet, sk.Objektiv(), baseline_laveste_omlaeg, baseline_hardship_omlaeg,
    baseline_laveste_buffer_omlaeg, baseline_buffer_hul_omlaeg,
)
check("Bufferdæknings-tjekket afviser derfor korrekt denne urealistiske omlægning",
      score_flyttet == float('-inf'), f"score={score_flyttet}")


print("\n=== Isoleret test: dybde-tjekket (værste-mod-værste) alene accepterer en legitim per-alder-tradeoff ===")
# Syntetisk resultat der isolerer PRÆCIS dybde-mekanismen fra varighed og
# buffer (begge planer har identisk, neutral buffer_rest og lavt hardship-
# antal). Kandidaten har ét år (indeks 8) der er lavere end default-planens
# EGET tal for samme alder (910 < 1000*0.97=970) — den gamle per-alder-
# sammenligning ville forkaste dette — men kandidatens eget værste punkt
# (700) er ikke dybere end default-planens (også 700), og det samlede
# jaevn-niveau er rent faktisk højere.
syn_default_3 = _syntetisk_resultat([1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 700])
syn_kandidat_3 = _syntetisk_resultat([1000, 1000, 1200, 1200, 1200, 1200, 1200, 1200, 910, 700])
syn_kandidat_3["jaevn_tabel"] = [dict(r, jaevn_mdr_real=1010, jaevn_mdr=1010) for r in syn_kandidat_3["jaevn_tabel"]]
syn_default_3_raa = sk._raa_indkomst_pr_alder(syn_default_3)
syn_kandidat_3_raa = sk._raa_indkomst_pr_alder(syn_kandidat_3)
check("Kandidatens alder-indeks 8 (910) er lavere end default-planens eget tal for samme alder (1000) — gammelt tjek ville forkaste",
      syn_kandidat_3_raa[73] < syn_default_3_raa[73] * sk.RAA_AAR_TOLERANCE,
      f"kandidat={syn_kandidat_3_raa[73]}, default={syn_default_3_raa[73]}")
check("Men kandidatens værste punkt (700) er ikke dybere end default-planens eget (700)",
      min(syn_kandidat_3_raa.values()) >= min(syn_default_3_raa.values()) * sk.RAA_AAR_TOLERANCE)
syn_default_3_min = min(syn_default_3_raa.values())
syn_default_3_hardship = _baseline_hardship(syn_default_3)
syn_default_3_buffer = sk._laveste_buffer_rest(syn_default_3)
syn_default_3_buffer_hul = sk._antal_buffer_hul_aar(syn_default_3)
score_syn_3, jaevn_syn_3 = sk.score(
    syn_kandidat_3, sk.Objektiv(), syn_default_3_min, syn_default_3_hardship,
    syn_default_3_buffer, syn_default_3_buffer_hul,
)
check("Værste-mod-værste-tjekket accepterer kandidaten og giver den et højere jaevn-niveau",
      score_syn_3 != float('-inf') and jaevn_syn_3 > 1000, f"score={score_syn_3}, jaevn={jaevn_syn_3}")


print("\n=== Varigheds-tjek (hardship-år) fanger et flerårigt lavindkomst-hul et rent dybde-gulv ville acceptere ===")
# Reproducerer den bruger-rapporterede regression direkte: at udskyde TO
# produkter samtidig (i stedet for kun ét) skaber en flerårig periode med
# indkomst markant under det udjævnede niveau. For DENNE profil rammer
# dybde-gulvet også (én af årene falder dybere end noget baseline-år), så
# den overordnede diskvalifikation her viser SAMSPILLET mellem de to tjek —
# se den ISOLEREDE varigheds-test lige efter for et rendyrket eksempel hvor
# depth-gulvet alene IKKE ville have fanget det.
resultat_to_produkter = generer_udbetalingstabel(
    profil_omlaeg,
    dict(parametre_omlaeg, produkt_start_aldre={'S1_ratepension': 70, 'S2_livsvarig pension': 70}),
)
score_to_produkter, _ = sk.score(
    resultat_to_produkter, sk.Objektiv(), baseline_laveste_omlaeg, baseline_hardship_omlaeg,
    baseline_laveste_buffer_omlaeg, baseline_buffer_hul_omlaeg,
)
check("Den flerårige, kraftigt udskudte lavindkomst-periode diskvalificeres",
      score_to_produkter == float('-inf'), f"score={score_to_produkter}")

print("\n=== Isoleret test: varigheds-tjekket fanger et flerårigt hul SELV NÅR dybde-gulvet alene ville acceptere det ===")
# Syntetisk resultat der omgår engine.py helt, for at isolere de to tjek fra
# hinanden. Begge planer har PRÆCIS samme værste enkeltpunkt (700, i år 5) —
# depth-gulvet alene ville altså acceptere begge. Forskellen er hvor MANGE år
# der ligger under hardship-grænsen (85% af 1000 = 850): default har kunét
# (700), kandidaten har fem (700 fem gange i træk).
syntetisk_default = _syntetisk_resultat([1000, 1000, 1000, 1000, 700, 1000, 1000, 1000, 1000, 1000])
syntetisk_kandidat = _syntetisk_resultat([1000, 1000, 1000, 1000, 700, 700, 700, 700, 700, 1000])
syn_default_raa = sk._raa_indkomst_pr_alder(syntetisk_default)
syn_default_min = min(syn_default_raa.values())
syn_default_hardship = sum(1 for v in syn_default_raa.values() if v < jaevn_niveau_realt(syntetisk_default) * sk.HARDSHIP_ANDEL_AF_JAEVN)
syn_default_buffer = sk._laveste_buffer_rest(syntetisk_default)
syn_default_buffer_hul = sk._antal_buffer_hul_aar(syntetisk_default)
syn_kandidat_raa = sk._raa_indkomst_pr_alder(syntetisk_kandidat)
check("Begge syntetiske planer har nøjagtig samme værste enkeltpunkt (700) — depth-gulvet skelner dem ikke",
      min(syn_kandidat_raa.values()) == syn_default_min == 700)
score_syn, _ = sk.score(
    syntetisk_kandidat, sk.Objektiv(), syn_default_min, syn_default_hardship, syn_default_buffer, syn_default_buffer_hul
)
check("Varigheds-tjekket diskvalificerer alligevel kandidaten (5 hardship-år mod default-planens 1)",
      score_syn == float('-inf'), f"score={score_syn}, default_hardship={syn_default_hardship}")
score_syn_default, _ = sk.score(
    syntetisk_default, sk.Objektiv(), syn_default_min, syn_default_hardship, syn_default_buffer, syn_default_buffer_hul
)
check("Default-planen er selv gennemførlig mod sin egen reference",
      score_syn_default != float('-inf'), f"score={score_syn_default}")


print("\n=== Isoleret test: bufferdæknings-tjekket fanger et 'urealistisk' hul hverken dybde- eller varighedstjek ser ===")
# Syntetisk resultat hvor rå indkomst og hardship-antal er IDENTISK mellem
# default og kandidat (depth- og duration-tjek ser altså ingen forskel), men
# kandidatens buffer_rest dykker langt dybere i minus på et tidspunkt — dvs.
# den udjævnede udbetaling "låner" mod langt mere ikke-modtaget indkomst end
# default-planen nogensinde gør. Det er præcis den situation der viste sig i
# brugerens rapporterede tilfælde: et stort, isoleret "til buffer"-udsving
# lige efter en periode hvor bufferen reelt ikke havde dækning.
syn_default_2 = _syntetisk_resultat(
    [1000] * 10, buffer_rest_vaerdier=[0, 0, 0, -5000, -5000, 0, 0, 0, 0, 0]
)
syn_kandidat_2 = _syntetisk_resultat(
    [1000] * 10, buffer_rest_vaerdier=[0, 0, 0, -80000, -80000, 0, 0, 0, 0, 0]
)
syn_default_2_raa = sk._raa_indkomst_pr_alder(syn_default_2)
syn_default_2_min = min(syn_default_2_raa.values())
syn_default_2_hardship = _baseline_hardship(syn_default_2)
syn_default_2_buffer = sk._laveste_buffer_rest(syn_default_2)
syn_default_2_buffer_hul = sk._antal_buffer_hul_aar(syn_default_2)
check("De to syntetiske planer har identisk rå indkomst og hardship-antal (depth/duration ser ingen forskel)",
      sk._raa_indkomst_pr_alder(syn_kandidat_2) == syn_default_2_raa)
score_syn_2, _ = sk.score(
    syn_kandidat_2, sk.Objektiv(), syn_default_2_min, syn_default_2_hardship,
    syn_default_2_buffer, syn_default_2_buffer_hul,
)
check("Bufferdæknings-tjekket diskvalificerer alligevel kandidaten (buffer -80.000 mod default -5.000)",
      score_syn_2 == float('-inf'), f"score={score_syn_2}")
score_syn_2_default, _ = sk.score(
    syn_default_2, sk.Objektiv(), syn_default_2_min, syn_default_2_hardship,
    syn_default_2_buffer, syn_default_2_buffer_hul,
)
check("Default-planen er selv gennemførlig mod sin egen reference",
      score_syn_2_default != float('-inf'), f"score={score_syn_2_default}")


print("\n=== Isoleret test: buffer-varigheds-tjekket fanger flere år med bufferunderskud SELV NÅR bufferdæknings-tjekket alene ville acceptere det ===")
# Reproducerer den bruger-rapporterede regression direkte: en optimeret plan
# viste "et par år med hul" selvom bufferdæknings-tjekket (værste-mod-værste
# på buffer_rest) ikke fandt noget galt. Begge planer her har PRÆCIS samme
# værste buffer_rest (-5000, i år 3) — bufferdæknings-tjekket alene ville
# altså acceptere begge. Forskellen er hvor MANGE år bufferen reelt er
# negativ: default har kun ét (-5000), kandidaten har tre i træk.
syn_default_4 = _syntetisk_resultat(
    [1000] * 10, buffer_rest_vaerdier=[0, 0, 0, -5000, 0, 0, 0, 0, 0, 0]
)
syn_kandidat_4 = _syntetisk_resultat(
    [1000] * 10, buffer_rest_vaerdier=[0, 0, 0, -5000, -5000, -5000, 0, 0, 0, 0]
)
syn_default_4_raa = sk._raa_indkomst_pr_alder(syn_default_4)
syn_default_4_min = min(syn_default_4_raa.values())
syn_default_4_hardship = _baseline_hardship(syn_default_4)
syn_default_4_buffer = sk._laveste_buffer_rest(syn_default_4)
syn_default_4_buffer_hul = sk._antal_buffer_hul_aar(syn_default_4)
check("Begge syntetiske planer har nøjagtig samme værste buffer_rest (-5000) — bufferdæknings-tjekket alene skelner dem ikke",
      sk._laveste_buffer_rest(syn_kandidat_4) == syn_default_4_buffer == -5000)
check("Men kandidaten har tre buffer-hul-år mod default-planens ét",
      sk._antal_buffer_hul_aar(syn_kandidat_4) == 3 and syn_default_4_buffer_hul == 1)
score_syn_4, _ = sk.score(
    syn_kandidat_4, sk.Objektiv(), syn_default_4_min, syn_default_4_hardship,
    syn_default_4_buffer, syn_default_4_buffer_hul,
)
check("Buffer-varigheds-tjekket diskvalificerer alligevel kandidaten (3 buffer-hul-år mod default-planens 1)",
      score_syn_4 == float('-inf'), f"score={score_syn_4}")
score_syn_4_default, _ = sk.score(
    syn_default_4, sk.Objektiv(), syn_default_4_min, syn_default_4_hardship,
    syn_default_4_buffer, syn_default_4_buffer_hul,
)
check("Default-planen er selv gennemførlig mod sin egen reference",
      score_syn_4_default != float('-inf'), f"score={score_syn_4_default}")


print("\n=== Husstandskobling: optimeringen bruger partnerens reelle indkomst, ikke en husstands-uafhængig antagelse ===")
profil_husstand = {
    'person': {'alder': 67, 'foedselsaar': 1959},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension', 'opsparing': 1_200_000, 'skat_type': 'S'},
    ],
}
parametre_husstand = {'pensionsalder': 67, 'afkast_pct': 4.0, 'udbetaling_aar': 20, 'civilstand': 'gift_samlevende'}
profil_partner = {
    'person': {'alder': 63, 'foedselsaar': 1963},
    'pensionsprodukter': [
        {'selskab': 'PFA', 'produkttype': 'ratepension', 'opsparing': 400_000, 'skat_type': 'S'},
    ],
}
parametre_partner = {'pensionsalder': 68, 'afkast_pct': 4.0, 'udbetaling_aar': 20}

skat_koblet = husstand.skat_params_for_person(parametre_husstand, profil_partner, parametre_partner)
baseline_uden_kobling = generer_udbetalingstabel(profil_husstand, parametre_husstand)
baseline_med_kobling = generer_udbetalingstabel(profil_husstand, parametre_husstand, skat_koblet)
check("Husstandskoblingen ændrer rent faktisk baseline-tallet (partnerens indkomst tæller nu med)",
      jaevn_niveau_realt(baseline_uden_kobling) != jaevn_niveau_realt(baseline_med_kobling),
      f"uden={jaevn_niveau_realt(baseline_uden_kobling):.0f}, med={jaevn_niveau_realt(baseline_med_kobling):.0f}")

res_med_partner = sk.optimer(
    profil_husstand, parametre_husstand,
    profil_partner=profil_partner, parametre_partner=parametre_partner,
)
check("optimer() med profil_partner kører uden fejl og finder en løsning",
      res_med_partner.bedste is not None)
if res_med_partner.bedste:
    vinder_jaevn = res_med_partner.bedste.jaevn_netto_mdr
    afstand_koblet   = abs(vinder_jaevn - jaevn_niveau_realt(baseline_med_kobling))
    afstand_ukoblet  = abs(vinder_jaevn - jaevn_niveau_realt(baseline_uden_kobling))
    check("Vinderplanen er mindst lige så god som den husstands-koblede baseline",
          vinder_jaevn >= jaevn_niveau_realt(baseline_med_kobling) - 1,
          f"vinder={vinder_jaevn:.0f}, koblet baseline={jaevn_niveau_realt(baseline_med_kobling):.0f}")
    check("Vinderplanen ligger tættere på den koblede baseline end den ukoblede (partnerens indkomst er reelt talt med)",
          afstand_koblet < afstand_ukoblet,
          f"afstand til koblet={afstand_koblet:.0f}, afstand til ukoblet={afstand_ukoblet:.0f}")


print("\n=== Ratepensioners udbetalingsperiode er nu en fri søgevariabel (min. 10 år) ===")
parametre_multi = dict(parametre, produkt_start_aldre={'PFA_aldersopsparing': 80}, produkt_udb_aar={'Danica_ratepension': 12})
baseline_multi = generer_udbetalingstabel(profil, parametre_multi)
_, dims_multi = sk._byg_soegerum(baseline_multi['produkter'], baseline_multi['fp_alder'], baseline_multi['pensionsalder'], 0)
default_vektor_multi = sk._default_vektor(baseline_multi['produkter'], 0)
check("Brugerens egen periode (12 år, langt under standard-intervallet) indgår altid i default-vektoren",
      ('periode', 'Danica_ratepension', 12) in default_vektor_multi, f"fik {default_vektor_multi}")
res_multi = sk.optimer(profil, parametre_multi)
check("Optimeringen fandt en løsning", res_multi.bedste is not None)
if res_multi.bedste:
    check("Vinderplanens ratepensions-periode respekterer det lovmæssige 10-års-gulv",
          res_multi.bedste.produkt_udb_aar.get('Danica_ratepension', 0) >= sk.MIN_RATEPENSION_UDB_AAR,
          f"fik {res_multi.bedste.produkt_udb_aar}")
    check("Aldersopsparingens udbetalingsår INDGÅR i vinderplanens søgte start-aldre (buffer-egnet, default i_buffer=True)",
          'PFA_aldersopsparing' in res_multi.bedste.produkt_start_aldre,
          f"fik {res_multi.bedste.produkt_start_aldre}")
    # Aldersopsparingen har nu SIN EGEN periode-variabel (0 = udbetalt samlet,
    # >=10 = spredt, se engine.py's "kapital_rate") — ved 6% afkast (parametre
    # foroven) og default 27%-buffer-skat (r_buffer=4.38%) er PAL-fordelen ved
    # at sprede reel, og optimeringen forventes derfor rent faktisk at vælge
    # at sprede den, ikke bare have adgang til muligheden.
    engangs_periode = res_multi.bedste.produkt_udb_aar.get('PFA_aldersopsparing')
    check("Aldersopsparingen har en gyldig periode-værdi i vinderplanen (0 eller mindst 10 år)",
          engangs_periode == 0 or (engangs_periode is not None and engangs_periode >= sk.MIN_RATEPENSION_UDB_AAR),
          f"fik {res_multi.bedste.produkt_udb_aar}")
    check("Ved dette afkastniveau (PAL-vækst > buffer-vækst) vælger optimeringen rent faktisk at sprede aldersopsparingen",
          engangs_periode is not None and engangs_periode > 0,
          f"fik {res_multi.bedste.produkt_udb_aar}")

print("\n=== Et engangsbeløb der er eksplicit FRAVALGT bufferen forbliver brugerens eget faste valg ===")
parametre_ekskl_multi = dict(
    parametre,
    produkt_start_aldre={'PFA_aldersopsparing': 80},
    produkt_i_buffer={'PFA_aldersopsparing': False},
)
res_ekskl_multi = sk.optimer(profil, parametre_ekskl_multi)
check("Optimeringen fandt en løsning", res_ekskl_multi.bedste is not None)
if res_ekskl_multi.bedste:
    check("Det fravalgte engangsbeløbs udbetalingsår (80) er UÆNDRET i vinderplanen (arvet som fast værdi, ikke søgt)",
          res_ekskl_multi.bedste.produkt_start_aldre.get('PFA_aldersopsparing') == 80,
          f"fik {res_ekskl_multi.bedste.produkt_start_aldre}")


print("\n=== engine.py: ATP indgår korrekt i indtægtsgrundlaget under FP-opsætning ===")
profil_opsaet = {
    'person': {'alder': 60, 'foedselsaar': 1957},
    'pensionsprodukter': [
        {'selskab': 'Danica', 'produkttype': 'ratepension', 'opsparing': 900_000, 'skat_type': 'S'},
    ],
}
r_opsaet = generer_udbetalingstabel(profil_opsaet, {
    'pensionsalder': 67, 'afkast_pct': 4.0, 'udbetaling_aar': 20, 'folkepension_opsaettelse_aar': 3,
})
fp_alder_opsaet = r_opsaet['fp_alder']
row_under_opsaettelse = next(r for r in r_opsaet['tabel'] if r['alder'] == fp_alder_opsaet)
check("ATP tælles med i indtægtsgrundlaget selv i et udskudt folkepensions-år",
      row_under_opsaettelse['atp_mdr_brutto'] > 0 and
      row_under_opsaettelse['indtaegtsgrundlag_aar'] >= row_under_opsaettelse['atp_mdr_brutto'] * 12,
      f"atp_mdr_brutto={row_under_opsaettelse['atp_mdr_brutto']}, indtaegtsgrundlag_aar={row_under_opsaettelse['indtaegtsgrundlag_aar']}")


print("\n=== _fp_venteprocent: nøjagtig tabel (67 år, <=10 år) vs. flad tilnærmelse ===")
check("3 års opsætning fra 67 år bruger den nøjagtige, kildehenviste tabelværdi (18,75%), ikke den flade sats",
      abs(_fp_venteprocent(67, 3) - (1 + satser_2026.VENTEPROCENT_LIVSVARIG_2026_FRA_67[3])) < 1e-9,
      f"fik {_fp_venteprocent(67, 3)}, tabellen siger {1 + satser_2026.VENTEPROCENT_LIVSVARIG_2026_FRA_67[3]}")
check("Tabellen er reelt IKKE-lineær (år 10 giver mere end 10x år 1's tillæg)",
      satser_2026.VENTEPROCENT_LIVSVARIG_2026_FRA_67[10] > 10 * satser_2026.VENTEPROCENT_LIVSVARIG_2026_FRA_67[1],
      f"år1={satser_2026.VENTEPROCENT_LIVSVARIG_2026_FRA_67[1]}, år10={satser_2026.VENTEPROCENT_LIVSVARIG_2026_FRA_67[10]}")
check("En anden folkepensionsalder end 67 falder tilbage til den flade tilnærmelse",
      abs(_fp_venteprocent(68, 3) - (1 + satser_2026.FOLKEPENSION_VENTEPROCENT_PR_AAR.vaerdi * 3)) < 1e-9,
      f"fik {_fp_venteprocent(68, 3)}")
check("Mere end 10 års opsætning fra 67 år falder tilbage til den flade tilnærmelse (uden for tabellen)",
      abs(_fp_venteprocent(67, 11) - (1 + satser_2026.FOLKEPENSION_VENTEPROCENT_PR_AAR.vaerdi * 11)) < 1e-9,
      f"fik {_fp_venteprocent(67, 11)}")
check("Ingen opsætning giver ingen multiplikator, uanset alder",
      _fp_venteprocent(67, 0) == 1.0 and _fp_venteprocent(70, 0) == 1.0)


print("\n=== engine.py: real-terms deflatering virker selv uden et eksplicit 'alder'-felt ===")
profil_kun_foedselsdato = {'person': {'foedselsdato': '01.01.1968'}, 'pensionsprodukter': [
    {'selskab': 'Danica', 'produkttype': 'ratepension', 'opsparing': 800_000, 'skat_type': 'S'},
]}
r_fdato = generer_udbetalingstabel(profil_kun_foedselsdato, {
    'pensionsalder': 62, 'afkast_pct': 5.0, 'udbetaling_aar': 20, 'inflation_pct': 2.0,
})
pension_rows_fdato = [r for r in r_fdato['jaevn_tabel'] if r['fase'] == 'pension']
check("jaevn_mdr_real er konstant (deflateres korrekt) selvom profilen mangler et eksplicit alder-felt",
      pension_rows_fdato[0]['jaevn_mdr_real'] == pension_rows_fdato[-1]['jaevn_mdr_real'],
      f"først={pension_rows_fdato[0]['jaevn_mdr_real']}, sidst={pension_rows_fdato[-1]['jaevn_mdr_real']}")
check("jaevn_mdr_real er forskelligt fra jaevn_mdr (dvs. deflateringen er faktisk aktiv)",
      pension_rows_fdato[-1]['jaevn_mdr_real'] != pension_rows_fdato[-1]['jaevn_mdr'],
      f"real={pension_rows_fdato[-1]['jaevn_mdr_real']}, nominel={pension_rows_fdato[-1]['jaevn_mdr']}")


print("\n=== engine.py: Tabel 4 (scenarier) viser nu også et realt tal ===")
from engine import generer_scenarier, SkatParametre
scenarier = generer_scenarier(profil_kun_foedselsdato, {
    'pensionsalder': 62, 'afkast_pct': 5.0, 'udbetaling_aar': 20, 'inflation_pct': 2.0,
}, None)
check("Hvert scenarie har nu et jaevn_mdr_real-felt",
      all('jaevn_mdr_real' in s for s in scenarier), f"fik {scenarier}")


print("\n=== Koordinat-søgning finder en forbedring ét enkelt (default) startpunkt misser ===")
# Reproducerer den bruger-rapporterede fejl direkte på algoritme-niveau: en
# bruger flyttede manuelt ÉN tidslinje-bjælke og fik et højere beløb, men
# optimeringen havde konkluderet "din nuværende plan er allerede den bedste"
# — fordi kun default-vektoren blev evalueret. Dette bygges som en syntetisk
# score-tabel (uafhængig af engine.py) hvor det globale optimum kun findes
# ved at flytte to dimensioner, hver til en værdi et koordinat-skridt fra
# udgangspunktet finder — præcis den situation _koordinat_udvid er bygget
# til at dække, uanset søgerummets størrelse.
dims_syntetisk = [
    [("d0", None, v) for v in range(6)],
    [("d1", None, v) for v in range(6)],
]
def _syntetisk_score(vektor):
    d0 = vektor[0][2]
    d1 = vektor[1][2]
    # Monoton stigende i begge retninger fra (0,0) mod det globale optimum
    # (5,5) — grådig koordinat-optimering finder det garanteret; kun at
    # evaluere default-vektoren gør det ikke.
    return d0 + d1 + (2 if (d0, d1) == (5, 5) else 0)

default_vektor_syntetisk = (("d0", None, 0), ("d1", None, 0))

evalueret_syntetisk = {}
def _evaluer_syntetisk(vektor):
    if vektor not in evalueret_syntetisk:
        evalueret_syntetisk[vektor] = _syntetisk_score(vektor)

# Uden koordinat-søgning: kun default-vektoren evalueret.
score_kun_default = {default_vektor_syntetisk: _syntetisk_score(default_vektor_syntetisk)}
check("Uden koordinat-søgning findes det globale optimum ikke",
      max(score_kun_default.values()) < 12, f"fik {score_kun_default}")

# Med koordinat-søgning, forankret i samme default-vektor:
sk._koordinat_udvid(dims_syntetisk, default_vektor_syntetisk, _evaluer_syntetisk, evalueret_syntetisk)
bedste_vektor_syntetisk = max(evalueret_syntetisk, key=evalueret_syntetisk.get)
check("Koordinat-søgningen finder det globale optimum (5,5) fra et enkelt (0,0)-udgangspunkt",
      bedste_vektor_syntetisk == (("d0", None, 5), ("d1", None, 5)),
      f"fik {bedste_vektor_syntetisk} med score {evalueret_syntetisk[bedste_vektor_syntetisk]}")
check("Den fundne score er strengt bedre end default-vektorens egen",
      evalueret_syntetisk[bedste_vektor_syntetisk] > evalueret_syntetisk[default_vektor_syntetisk])


print("\n=== optimer(): koordinat-søgningen indgår i den fulde pipeline (ikke kun isoleret) ===")
# Samme lumpsum-profil som ovenfor, men med et kunstigt lille evaluerings-loft.
# En allerede igangværende koordinat-søgning må altid fuldføre sine runder
# (se _koordinat_udvid) — budgettet styrer kun hvor mange EKSTRA udgangspunkter
# der får lov at starte, så optimer() skal stadig evaluere MERE end det
# kunstigt lave loft alene ville tillade.
res_lille_loft = sk.optimer(profil_lumpsum, parametre_lumpsum, maks_evalueringer=1)
check("optimer() evaluerer flere kombinationer end det (kunstigt lave) evaluerings-loft alene ville give",
      res_lille_loft.evalueret > 1, f"evalueret={res_lille_loft.evalueret}")
check("optimer() finder stadig en gennemførlig plan selv med et evaluerings-loft på 1",
      res_lille_loft.antal_gennemfoerlige > 0)


print("\n" + "=" * 44)
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    print("Fejlede:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
