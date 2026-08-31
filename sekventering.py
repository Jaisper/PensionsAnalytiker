"""
Sekventeringsoptimering — udtømmende (beskåret) søgning over per-produkt
start-aldre og folkepensions-opsættelse.

Målet er IKKE at maksimere den samlede nutidsværdi af udbetalingerne (det
belønner lumpede, bagtunge forløb, fordi sen, stor kapital i teorien kan
"udjævnes" bagud over hele perioden) — målet er at maksimere det faste,
inflationskorrigerede månedsbeløb brugeren rent faktisk kan leve af hele
den ønskede periode, jf. den samme `jaevn_netto_mdr` som allerede vises i
det almindelige udbetalingsdiagram.

En kandidat-kombination er et selvstændigt kald til
engine.generer_udbetalingstabel (ren funktion, ingen delt tilstand). Ratepensioners
udbetalingsperiode og engangsbeløbs udbetalingsår behandles begge som
brugerens egne, allerede trufne valg — kun START-alderen pr. løbende produkt
og folkepensions-opsætningen er reelt frie beslutningsvariable her.

VIGTIGT om realisme: `jaevn_netto_mdr` er en fremadskuende udjævning af HELE
den resterende løbende indkomst — for ethvert forløb med et sent indkomst-løft
(fx folkepension der først starter år senere) vil dette tal helt naturligt
ligge over de tidlige års rå indkomst, også for den helt uoptimerede
default-plan. Det er IKKE i sig selv et problem. Det AFGØRENDE er om
optimeringen aktivt gør de tidlige år VÆRRE end de ville have været uden
indblanding, blot for at få et højere (men mindre reelt) udjævnet tal at
vise frem. Der tjekkes derfor at ingen kandidat nogensinde har et lavere
rå, faktisk udbetalt minimum end den uoptimerede default-plan selv har —
se `_mindste_raa_beloeb`.

"Ingen løsning" er et gyldigt svar: kald altid har_loesning() før resultatet
præsenteres, og vis aldrig den næstbedste-men-utilstrækkelige plan som var
den brugbar. Med denne kombination af tjek er default-planen dog altid selv
en gennemførlig kandidat, så "ingen løsning" i praksis kun opstår hvis
brugeren selv har sat en urealistisk høj bundgrænse.

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

# Kandidatens laveste rå månedsbeløb skal mindst udgøre denne andel af
# default-planens eget laveste rå beløb — lille tolerance for afrunding,
# ikke en invitation til at forringe de tidlige år mærkbart.
RAA_TOLERANCE = 0.95


@dataclass
class Objektiv:
    """Valgfri hård bundgrænse (kr/mdr) — uden den falder optimeringen
    tilbage til en fornuftig default (se optimer())."""
    haard_minimum_mdr: Optional[float] = None


@dataclass
class Kandidat:
    produkt_start_aldre: dict[str, int]
    produkt_udb_aar: dict[str, int]
    folkepension_opsaettelse_aar: int
    score: float
    jaevn_netto_mdr: float
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


def _mindste_raa_beloeb(resultat: dict) -> float:
    """Det laveste rå (ikke-udjævnede) månedsbeløb i pensionsfasen — bruges
    som en \"gør ikke de tidlige år værre end de var\"-reference."""
    pension_raekker = [r for r in resultat["jaevn_tabel"] if r["fase"] == "pension"]
    if not pension_raekker:
        return 0.0
    return min(r["normal_mdr"] for r in pension_raekker)


def score(resultat: dict, obj: Objektiv, mindste_raa_baseline: float) -> tuple[float, float]:
    """Returnerer (score, jaevn_netto_mdr). Score er det faste, bæredygtige
    månedsbeløb (samme tal som `jaevn_netto_mdr` i det almindelige diagram)
    — højere er bedre, det ER selve målet. Score = -inf hvis planen enten
    bryder en (eventuel) hård bundgrænse, gør de tidlige/laveste år værre
    end default-planens egne (se modulets docstring), eller indeholder et
    fuldstændigt indkomst-hul."""
    jaevn_tabel = [r for r in resultat["jaevn_tabel"] if r["fase"] == "pension"]
    jaevn_niveau = resultat["jaevn_netto_mdr"]
    graense = obj.haard_minimum_mdr if obj.haard_minimum_mdr is not None else 0.0

    for row in jaevn_tabel:
        udjaevnet = row["jaevn_mdr_real"] if row["jaevn_mdr_real"] is not None else row["jaevn_mdr"]
        if udjaevnet < graense:
            return float("-inf"), 0.0

    if _mindste_raa_beloeb(resultat) < mindste_raa_baseline * RAA_TOLERANCE:
        return float("-inf"), 0.0

    # Et fuldstændigt indkomst-hul (fx før pensionsalder, eller hvis ALLE
    # produkter er udskudt forbi et givet år) skal disqualificere uanset
    # hvor højt det udjævnede niveau ellers ser ud.
    pensionsalder = resultat["pensionsalder"]
    for row in resultat["tabel"]:
        if row["alder"] >= pensionsalder and row["total_netto_mdr"] <= 0:
            return float("-inf"), 0.0

    return jaevn_niveau, jaevn_niveau


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
        # Ingen grænse angivet af brugeren: beskyt mod at optimeringen finder
        # en plan der er drastisk værre i et enkelt år end den nuværende,
        # uden at kræve brugeren selv opfinder et tal.
        obj = Objektiv(haard_minimum_mdr=round(baseline["jaevn_netto_mdr"] * 0.7))
    mindste_raa_baseline = _mindste_raa_beloeb(baseline)

    alle_produkter = baseline["produkter"]

    if not alle_produkter:
        return OptimeringsResultat(evalueret=0, antal_gennemfoerlige=0, bedste=None)

    noegler, dimensioner = _byg_soegerum(alle_produkter, baseline["fp_alder"], baseline["pensionsalder"])

    evaluerede: list[tuple[tuple, float, float]] = []  # (vektor, score, jaevn_netto_mdr)
    for vektor in islice(product(*dimensioner), maks_evalueringer):
        p, _, _ = _parametre_for_vektor(parametre, vektor)
        resultat = generer_udbetalingstabel(profil, p)
        s, jaevn = score(resultat, obj, mindste_raa_baseline)
        evaluerede.append((vektor, s, jaevn))

    evaluerede.sort(key=lambda x: x[1], reverse=True)
    gennemfoerlige = [e for e in evaluerede if e[1] != float("-inf")]

    if not gennemfoerlige:
        return OptimeringsResultat(evalueret=len(evaluerede), antal_gennemfoerlige=0, bedste=None)

    # Kun de bedste 5 får et fuldt genberegnet resultat vedhæftet (til visning/
    # "Anvend denne plan") — at gemme den fulde generer_udbetalingstabel-output
    # for op til 3.000 kandidater undervejs ville bruge unødig hukommelse, når
    # kun vinderen reelt skal bruges bagefter.
    top_kandidater: list[Kandidat] = []
    for vektor, s, jaevn in gennemfoerlige[:5]:
        p, start_aldre, udb_aar = _parametre_for_vektor(parametre, vektor)
        opsaettelse = next((v for slag, _, v in vektor if slag == "opsaet"), 0)
        resultat = generer_udbetalingstabel(profil, p)
        top_kandidater.append(Kandidat(
            produkt_start_aldre=start_aldre,
            produkt_udb_aar=udb_aar,
            folkepension_opsaettelse_aar=opsaettelse,
            score=s, jaevn_netto_mdr=jaevn,
            resultat=resultat,
        ))

    # Følsomhed: spænd (max-min) i gennemsnitlig score pr. beslutningsdimension,
    # kun blandt gennemførlige kandidater — samme mønster som sekventering.ts.
    foelsomhed: dict[str, float] = {}
    for dim_idx, noegle in enumerate(noegler):
        grupper: dict[Any, list[float]] = {}
        for vektor, s, _ in gennemfoerlige:
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
