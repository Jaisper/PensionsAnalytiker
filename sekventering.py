"""
Sekventeringsoptimering — udtømmende (beskåret) søgning over per-produkt
start-aldre og folkepensions-opsættelse.

Port af pension-core's sekventering.ts (optimer/score/harLoesning). Hver
kandidat-kombination er et selvstændigt kald til engine.generer_udbetalingstabel
(ren funktion, ingen delt tilstand), scoret som NPV af nettorådighed minus en
proportional straf for underdækning under et valgfrit månedligt mål — med en
HÅRD diskvalifikation (score = -inf) af enhver plan med blot ét år under en
minimumsgrænse. "Ingen løsning" er et gyldigt svar: kald altid har_loesning()
før resultatet præsenteres, og vis aldrig den næstbedste-men-utilstrækkelige
plan som var den brugbar.

Ratepensioners udbetalingsperiode og engangsbeløbs udbetalingsår behandles
BEGGE som brugerens egne, allerede trufne valg (fra rapporten eller et
tidligere manuelt valg) — ligesom i pension-core er det kun START-alderen og
folkepensions-opsætningen der reelt er frie beslutningsvariable her.

Folkepensions-opsætningens ventetillæg er en bevidst forenkling (samme skøn
som kildepakken selv bruger, jf. satser_2026.FOLKEPENSION_VENTEPROCENT_PR_AAR)
— IKKE en juridisk præcis beregning.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from itertools import islice, product
from typing import Any, Optional

from engine import generer_udbetalingstabel, tidligste_private_pensionsalder

MAKS_EVALUERINGER_DEFAULT = 3000
STANDARD_OPSAETTELSE = [0, 1, 2, 3, 5]
STANDARD_EKSTRA_AAR  = 10  # søg start-aldre op til pensionsalder + dette


@dataclass
class Objektiv:
    """Scoringsmål. Alle felter er valgfrie — uden dem falder optimeringen
    tilbage til fornuftige defaults (se optimer())."""
    maal_mdr: Optional[float] = None
    haard_minimum_mdr: Optional[float] = None
    diskonteringsrente: Optional[float] = None


@dataclass
class Kandidat:
    produkt_start_aldre: dict[str, int]
    produkt_udb_aar: dict[str, int]
    folkepension_opsaettelse_aar: int
    score: float
    npv_netto_raadighed: float
    samlet_underdaekning: float
    resultat: Optional[dict] = None  # kun udfyldt for top-kandidater, se optimer()


@dataclass
class OptimeringsResultat:
    evalueret: int
    antal_gennemfoerlige: int
    bedste: Optional[Kandidat]
    top: list[Kandidat] = field(default_factory=list)
    foelsomhed: dict[str, float] = field(default_factory=dict)


def har_loesning(r: OptimeringsResultat) -> bool:
    """Findes der overhovedet en plan der opfylder den hårde grænse?"""
    return r.antal_gennemfoerlige > 0


def _byg_soegerum(baseline_produkter: list[dict], fp_alder: int, pensionsalder: int):
    """Bygger (noegler, dimensioner) — én dimension pr. beslutningsvariabel,
    mønster fra sekventering.ts's `noegler`/`dimensioner`. Engangsbeløb
    (aldersopsparing/kapitalpension) får IKKE en start-alder-dimension — det
    er brugerens egen, bevidst valgte udbetalingsår (fx sat via tidslinje-
    trækket), og optimeringen må ikke flytte rundt på et allerede truffet
    valg. Ratepensionens udbetalingsperiode er PÅ SAMME MÅDE brugerens eget
    valg (fra rapporten eller et tidligere manuelt valg) — kun START-alderen
    pr. løbende produkt og folkepensions-opsætningen er reelt frie
    beslutningsvariable her (ATP er slet ikke en del af `produkter`-listen
    og indgår derfor aldrig, samme udelukkelse som i sekventering.ts)."""
    # Den generelle lovmæssige tommelfingerregel (fp_alder - 5) er kun en
    # DEFAULT-hjælp i interviewet — brugeren kan frit have valgt en tidligere
    # pensionsalder end den (fx fordi deres konkrete produkter allerede
    # tillader det). Søgerummet skal ALDRIG ekskludere den alder brugeren
    # faktisk har valgt, ellers "optimerer" den brugeren væk fra deres egen
    # ønskede pensionsalder og over i et senere, ubedt starttidspunkt.
    tidligst = min(tidligste_private_pensionsalder(fp_alder), pensionsalder)
    start_aldre = list(range(tidligst, pensionsalder + STANDARD_EKSTRA_AAR + 1))

    noegler: list[str] = []
    dimensioner: list[list[tuple]] = []
    for pr in baseline_produkter:
        if pr["udb_type"] == "engangsbeloeb":
            continue
        key = pr["key"]
        dimensioner.append([("start", key, a) for a in start_aldre])
        noegler.append(f"start:{key}")
    dimensioner.append([("opsaet", None, o) for o in STANDARD_OPSAETTELSE])
    noegler.append("opsaet:folkepension")
    return noegler, dimensioner


def _parametre_for_vektor(base_parametre: dict, vektor: tuple[tuple, ...]) -> tuple[dict, dict, int]:
    # Start med brugerens EGNE eksisterende start-alder-/periode-valg (bl.a.
    # engangsbeløbenes bevidst valgte udbetalingsår og ratepensionens
    # udbetalingsperiode, som ingen af dem er en del af søgerummet — se
    # _byg_soegerum) — vektorens "start"-indgange overskriver kun de løbende
    # produkter der rent faktisk indgår i optimeringen.
    produkt_start_aldre: dict[str, int] = dict(base_parametre.get("produkt_start_aldre", {}))
    produkt_udb_aar: dict[str, int] = dict(base_parametre.get("produkt_udb_aar", {}))
    opsaettelse_aar = 0
    for slag, key, vaerdi in vektor:
        if slag == "start":
            produkt_start_aldre[key] = vaerdi
        elif slag == "opsaet":
            opsaettelse_aar = vaerdi
    p = dict(base_parametre)
    p["produkt_start_aldre"] = produkt_start_aldre
    p["produkt_udb_aar"] = produkt_udb_aar
    p["folkepension_opsaettelse_aar"] = opsaettelse_aar
    return p, produkt_start_aldre, produkt_udb_aar


def score(resultat: dict, obj: Objektiv, inflation_pct: float) -> tuple[float, float, float]:
    """Returnerer (score, npv, samlet_underdaekning). Score = -inf hvis et
    år er under den hårde minimumsgrænse — direkte port af score()/
    STANDARD_OBJEKTIV fra sekventering.ts.

    Scores på `jaevn_tabel` (det buffer-udjævnede, faktisk oplevede
    rådighedsbeløb pr. måned) — IKKE den rå `tabel`, som naturligt svinger
    år for år før udjævning (fx før et produkt starter) og derfor ville gøre
    stort set enhver plan "urealistisk" mod en fast bundgrænse. Kun
    pensionsfasen (`fase == "pension"`) tæller med — pre-pension-år er
    stadig lønår, som denne tabel ikke modellerer disponibel indkomst for.

    VIGTIGT: `jaevn_mdr` er en fremadskuende udjævning af HELE den resterende
    løbende indkomst (ratepension/livsvarig/FP/ATP), og forudsætter i sin
    natur at et tidligt, lavere rå beløb er en NORMAL del af et ellers sundt
    forløb (fx ratepension alene før folkepensionen supplerer) — det er ikke
    i sig selv et tegn på et urealistisk hul, og bundgrænsen skal derfor
    fortsat tjekkes mod det udjævnede tal for den almindelige straf for
    underdækning. Men udjævningen kan IKKE gøre et fuldstændigt indkomst-hul
    (reelt nul kr. hele året, fx fordi et produkt er udskudt langt ud i
    fremtiden) usynligt — det er en anden og strengere ting end "lavere end
    gennemsnittet", og tjekkes derfor separat mod den RÅ `tabel`."""
    jaevn_tabel = [r for r in resultat["jaevn_tabel"] if r["fase"] == "pension"]
    diskonto = obj.diskonteringsrente if obj.diskonteringsrente is not None else resultat["parametre"].get("afkast_pct", 4.0) / 100

    if obj.haard_minimum_mdr is not None:
        for row in jaevn_tabel:
            check_val = row["jaevn_mdr_real"] if row["jaevn_mdr_real"] is not None else row["jaevn_mdr"]
            if check_val < obj.haard_minimum_mdr:
                return float("-inf"), 0.0, 0.0

    pensionsalder = resultat["pensionsalder"]
    for row in resultat["tabel"]:
        if row["alder"] >= pensionsalder and row["total_netto_mdr"] <= 0:
            return float("-inf"), 0.0, 0.0

    npv = 0.0
    samlet_underdaekning = 0.0
    for i, row in enumerate(jaevn_tabel):
        diskonteringsfaktor = (1 + diskonto) ** i
        aar_beloeb = row["jaevn_mdr"] * 12
        npv += aar_beloeb / diskonteringsfaktor
        if obj.maal_mdr is not None:
            maal_aar = obj.maal_mdr * 12 * (1 + inflation_pct) ** i
            if aar_beloeb < maal_aar:
                samlet_underdaekning += (maal_aar - aar_beloeb) / diskonteringsfaktor

    UNDERDAEKNINGSSTRAF = 3.0
    return npv - samlet_underdaekning * UNDERDAEKNINGSSTRAF, npv, samlet_underdaekning


def optimer(
    profil: dict,
    parametre: dict,
    obj: Optional[Objektiv] = None,
    maks_evalueringer: int = MAKS_EVALUERINGER_DEFAULT,
) -> OptimeringsResultat:
    if obj is None:
        obj = Objektiv()

    baseline = generer_udbetalingstabel(profil, parametre)
    if obj.haard_minimum_mdr is None:
        # Ingen mål angivet af brugeren: beskyt mod at optimeringen finder en
        # plan der er drastisk værre i et enkelt år end den nuværende, uden at
        # kræve brugeren selv opfinder et tal.
        obj = Objektiv(
            maal_mdr=obj.maal_mdr,
            haard_minimum_mdr=round(baseline["jaevn_netto_mdr"] * 0.7),
            diskonteringsrente=obj.diskonteringsrente,
        )

    alle_produkter = baseline["produkter"]
    inflation_pct = float(parametre.get("inflation_pct", 0.0)) / 100

    if not alle_produkter:
        return OptimeringsResultat(evalueret=0, antal_gennemfoerlige=0, bedste=None)

    noegler, dimensioner = _byg_soegerum(alle_produkter, baseline["fp_alder"], baseline["pensionsalder"])

    evaluerede: list[tuple[tuple, float, float, float]] = []  # (vektor, score, npv, underdaekning)
    for vektor in islice(product(*dimensioner), maks_evalueringer):
        p, _, _ = _parametre_for_vektor(parametre, vektor)
        resultat = generer_udbetalingstabel(profil, p)
        s, npv, underdaekning = score(resultat, obj, inflation_pct)
        evaluerede.append((vektor, s, npv, underdaekning))

    evaluerede.sort(key=lambda x: x[1], reverse=True)
    gennemfoerlige = [e for e in evaluerede if e[1] != float("-inf")]

    if not gennemfoerlige:
        return OptimeringsResultat(evalueret=len(evaluerede), antal_gennemfoerlige=0, bedste=None)

    # Kun de bedste 5 får et fuldt genberegnet resultat vedhæftet (til visning/
    # "Anvend denne plan") — at gemme den fulde generer_udbetalingstabel-output
    # for op til 3.000 kandidater undervejs ville bruge unødig hukommelse, når
    # kun vinderen reelt skal bruges bagefter.
    top_kandidater: list[Kandidat] = []
    for vektor, s, npv, underdaekning in gennemfoerlige[:5]:
        p, start_aldre, udb_aar = _parametre_for_vektor(parametre, vektor)
        opsaettelse = next((v for slag, _, v in vektor if slag == "opsaet"), 0)
        resultat = generer_udbetalingstabel(profil, p)
        top_kandidater.append(Kandidat(
            produkt_start_aldre=start_aldre,
            produkt_udb_aar=udb_aar,
            folkepension_opsaettelse_aar=opsaettelse,
            score=s, npv_netto_raadighed=npv, samlet_underdaekning=underdaekning,
            resultat=resultat,
        ))

    # Følsomhed: spænd (max-min) i gennemsnitlig score pr. beslutningsdimension,
    # kun blandt gennemførlige kandidater — samme mønster som sekventering.ts.
    foelsomhed: dict[str, float] = {}
    for dim_idx, noegle in enumerate(noegler):
        grupper: dict[Any, list[float]] = {}
        for vektor, s, _, _ in gennemfoerlige:
            vaerdi = vektor[dim_idx][2]
            grupper.setdefault(vaerdi, []).append(s)
        gennemsnit = [sum(v) / len(v) for v in grupper.values() if v]
        foelsomhed[noegle] = (max(gennemsnit) - min(gennemsnit)) if gennemsnit else 0.0

    return OptimeringsResultat(
        evalueret=len(evaluerede),
        antal_gennemfoerlige=len(gennemfoerlige),
        bedste=top_kandidater[0],
        top=top_kandidater,
        foelsomhed=foelsomhed,
    )
