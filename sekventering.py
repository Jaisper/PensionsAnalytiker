"""
Sekventeringsoptimering — søgning over per-produkt start-aldre og
folkepensions-opsættelse.

Målet er IKKE at maksimere den samlede nutidsværdi af udbetalingerne (det
belønner lumpede, bagtunge forløb, fordi sen, stor kapital i teorien kan
"udjævnes" bagud over hele perioden) — målet er at maksimere det faste,
inflationskorrigerede månedsbeløb brugeren rent faktisk kan leve af hele
den ønskede periode, jf. den samme `jaevn_netto_mdr`/`jaevn_niveau_realt`
som allerede vises i det almindelige udbetalingsdiagram.

En kandidat-kombination er et selvstændigt kald til
engine.generer_udbetalingstabel (ren funktion, ingen delt tilstand).
Ratepensioners udbetalingsperiode og engangsbeløbs udbetalingsår behandles
begge som brugerens egne, allerede trufne valg — kun START-alderen pr.
løbende produkt og folkepensions-opsætningen er reelt frie
beslutningsvariable her.

VIGTIGT om realisme: `jaevn_netto_mdr` er en fremadskuende udjævning af HELE
den resterende løbende indkomst — for ethvert forløb med et sent
indkomst-løft (fx folkepension der først starter år senere) vil dette tal
helt naturligt ligge over de tidlige års rå indkomst, også for den helt
uoptimerede default-plan. Det er IKKE i sig selv et problem. Det AFGØRENDE
er om optimeringen aktivt GØR ET SPECIFIKT ÅR VÆRRE end default-planen
allerede har det år, blot for at presse et højere (men mindre reelt)
udjævnet tal frem. Der sammenlignes derfor år for år (alder for alder, se
`_raa_indkomst_pr_alder`): kandidatens rå, faktisk udbetalte beløb i hvert
overlappende år skal mindst matche default-planens eget for samme alder.

To tidligere, svagere forsøg viste sig utilstrækkelige og er droppet:
et enkelt-minimum-tjek fangede dybden af et hul men ikke hvor mange år det
varede ved; et summeret "bufferunderskud"-tjek kollapsede til nul tolerance
når default-planen selv havde 0 i underskud (almindeligt for profiler uden
et pre-pensions engangsbeløb) og var samtidig virkningsløst når
default-planen HAVDE et stort engangsbeløb (bufferen blev aldrig negativ
for nogen kandidat, uanset hvor langt et produkt blev udskudt). Den
år-for-år sammenligning der bruges nu er robust over for begge svagheder.

Søgerummet kan blive langt større end det er praktisk at gennemgå
udtømmende (fx 5 produkter × 15 aldre × 5 opsætningsår > 1 million
kombinationer). Er det tilfældet, bruges en DETERMINISTISK (fast seed)
tilfældig stikprøve i stedet for blot at tage de første N kombinationer i
den kartesiske rækkefølge — en ren "de første N" ville systematisk fastfryse
de forreste dimensioner nær deres laveste værdier og aldrig undersøge resten
af deres interval (og dermed rapportere en kunstigt lav følsomhed for netop
dem). Default-planens egen kombination indgår altid eksplicit, uanset
stikprøve, så den garanteret er en af de evaluerede kandidater.

En ren tilfældig stikprøve er ALENE ikke nok: ved store søgerum (typisk
allerede ved 3-4 løbende produkter) er dækningsgraden af 3000 tilfældige
kombinationer ud af et rum på hundredtusinder ofte under 1%, og en bruger
der manuelt flytter blot ÉN produkt-bjælke i tidslinjen kan sagtens finde en
konkret, bedre kombination som stikprøven aldrig ramte — hvorved
optimeringen fejlagtigt konkluderer "din nuværende plan er allerede den
bedste". Derfor suppleres stikprøven altid med en GRÅDIG
KOORDINAT-SØGNING (`_koordinat_udvid`) forankret i brugerens egen
nuværende plan: for hver beslutningsdimension (hvert produkts start-alder,
folkepensions-opsætningen) afprøves ALLE dens mulige værdier, mens de
øvrige holdes fast ved den hidtil bedste vektor, og der rykkes til den
bedste fundne værdi før næste dimension gennemgås. Det garanterer at enhver
forbedring der findes ved at flytte ét produkt ad gangen — præcis den måde
en bruger selv trækker tidslinjen på — bliver evalueret, uafhængigt af
søgerummets størrelse og uafhængigt af om den tilfældige stikprøve ramte
den. Et par gentagne runder fanger desuden de fleste sekventielle
to-produkt-justeringer (flyt produkt A til dets bedste punkt, derefter B ud
fra As nye værdi, osv.).

"Ingen løsning" er et gyldigt svar: kald altid har_loesning() før resultatet
præsenteres, og vis aldrig den næstbedste-men-utilstrækkelige plan som var
den brugbar. Fordi default-planens egen kombination altid indgår og altid
er gennemførlig mod sig selv, opstår "ingen løsning" i praksis kun hvis
brugeren selv har sat en urealistisk høj bundgrænse.

Er der uploadet en fuld partner-profil (Fase D), regnes partnerens
indkomst-per-kalenderår ÉN gang (partnerens egen plan optimeres ikke,
kun brugerens) og fødes ind i hver kandidats samspils-beregning — ellers
ville optimeringen score kandidater mod et forkert, husstands-uafhængigt
indtægtsgrundlag.

Folkepensions-opsætningens ventetillæg er en bevidst forenkling (samme skøn
som kildepakken selv bruger, jf. satser_2026.FOLKEPENSION_VENTEPROCENT_PR_AAR)
— IKKE en juridisk præcis beregning.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Optional

from engine import generer_udbetalingstabel, tidligste_private_pensionsalder, jaevn_niveau_realt, SkatParametre
import husstand

MAKS_EVALUERINGER_DEFAULT = 3000
STANDARD_OPSAETTELSE = [0, 1, 2, 3, 5]
STANDARD_EKSTRA_AAR  = 10  # søg start-aldre op til pensionsalder + dette
STIKPROEVE_SEED = 20260831  # fast seed — samme profil giver samme resultat hver gang
KOORDINAT_RUNDER = 3  # antal grådige gennemløb af alle dimensioner, se _koordinat_udvid

# Kandidatens rå (ikke-udjævnede) beløb for et givet år skal mindst udgøre
# denne andel af default-planens eget rå beløb for SAMME alder — lille
# tolerance for afrunding, ikke en invitation til at forringe et
# overlappende år mærkbart.
RAA_AAR_TOLERANCE = 0.97


@dataclass
class Objektiv:
    """Valgfri hård bundgrænse (kr/mdr, i dagens købekraft) — uden den falder
    optimeringen tilbage til en fornuftig default (se optimer())."""
    haard_minimum_mdr: Optional[float] = None


@dataclass
class Kandidat:
    produkt_start_aldre: dict[str, int]
    produkt_udb_aar: dict[str, int]
    folkepension_opsaettelse_aar: int
    score: float
    jaevn_netto_mdr: float  # i DAGENS købekraft — se engine.jaevn_niveau_realt
    resultat: Optional[dict] = None


@dataclass
class OptimeringsResultat:
    evalueret: int
    mulige_kombinationer: int  # størrelsen af det fulde søgerum, uanset stikprøve
    antal_gennemfoerlige: int
    bedste: Optional[Kandidat]
    top: list[Kandidat] = field(default_factory=list)
    foelsomhed: dict[str, float] = field(default_factory=dict)


def har_loesning(r: OptimeringsResultat) -> bool:
    """Findes der overhovedet en plan der opfylder den hårde grænse?"""
    return r.antal_gennemfoerlige > 0


def _byg_soegerum(baseline_produkter: list[dict], fp_alder: int, pensionsalder: int, nuvaerende_opsaettelse: int):
    """Bygger (noegler, dimensioner) — én dimension pr. beslutningsvariabel.
    Engangsbeløb (aldersopsparing/kapitalpension) får IKKE en
    start-alder-dimension — det er brugerens egen, bevidst valgte
    udbetalingsår, og optimeringen må ikke flytte rundt på et allerede
    truffet valg. Kun START-alderen pr. løbende produkt og
    folkepensions-opsætningen er reelt frie beslutningsvariable her (ATP er
    slet ikke en del af `produkter`-listen og indgår derfor aldrig)."""
    # Den generelle lovmæssige tommelfingerregel (fp_alder - 5) er kun en
    # DEFAULT-hjælp i interviewet — søgerummet skal ALDRIG ekskludere en alder
    # brugeren faktisk allerede har valgt (hverken pensionsalderen generelt
    # eller et enkelt produkts egen, evt. manuelt trukne start-alder).
    tidligst_generelt = min(tidligste_private_pensionsalder(fp_alder), pensionsalder)
    standard_interval = set(range(tidligst_generelt, pensionsalder + STANDARD_EKSTRA_AAR + 1))

    noegler: list[str] = []
    dimensioner: list[list[tuple]] = []
    for pr in baseline_produkter:
        if pr["udb_type"] == "engangsbeloeb":
            continue
        key = pr["key"]
        interval = standard_interval | {pr["start_alder"]}
        dimensioner.append([("start", key, a) for a in sorted(interval)])
        noegler.append(f"start:{key}")
    opsaettelse_interval = sorted(set(STANDARD_OPSAETTELSE) | {nuvaerende_opsaettelse})
    dimensioner.append([("opsaet", None, o) for o in opsaettelse_interval])
    noegler.append("opsaet:folkepension")
    return noegler, dimensioner


def _kandidat_vektorer(dimensioner: list[list[tuple]], maks_evalueringer: int, default_vektor: tuple):
    """Genererer kandidat-vektorer til evaluering. Udtømmende hvis det fulde
    søgerum er lille nok; ellers en DETERMINISTISK tilfældig stikprøve (fast
    seed) i stedet for blot de første N i kartesisk rækkefølge — en ren
    prefix-afskæring fastfryser systematisk de forreste dimensioner nær
    deres laveste værdier (se modulets docstring). default_vektor indgår
    altid eksplicit, uanset stikprøve."""
    mulige = 1
    for d in dimensioner:
        mulige *= len(d)

    if mulige <= maks_evalueringer:
        return list(product(*dimensioner)), mulige

    rng = random.Random(STIKPROEVE_SEED)
    valgt: set[tuple] = {default_vektor}
    forsoeg = 0
    maks_forsoeg = maks_evalueringer * 30
    while len(valgt) < maks_evalueringer and forsoeg < maks_forsoeg:
        vektor = tuple(rng.choice(d) for d in dimensioner)
        valgt.add(vektor)
        forsoeg += 1
    return list(valgt), mulige


def _koordinat_udvid(
    dimensioner: list[list[tuple]],
    start_vektor: tuple,
    evaluer_fn,
    score_for: dict[tuple, float],
    runder: int = KOORDINAT_RUNDER,
) -> None:
    """Grådig koordinat-optimering forankret i start_vektor (brugerens egen
    nuværende plan): for hver runde afprøves hver dimension over ALLE dens
    værdier, mens de øvrige holdes fast ved den hidtil bedste vektor, og der
    rykkes til den bedste fundne værdi før næste dimension. Kalder
    evaluer_fn(vektor) for hver afprøvet kombination (som selv dedupper og
    fylder score_for) — se modulets docstring for hvorfor dette supplement
    til den tilfældige stikprøve er nødvendigt."""
    evaluer_fn(start_vektor)
    bedste_vektor = start_vektor
    bedste_score = score_for[start_vektor]
    for _ in range(runder):
        forbedret = False
        for dim_idx, dim in enumerate(dimensioner):
            for vaerdi in dim:
                kandidat = bedste_vektor[:dim_idx] + (vaerdi,) + bedste_vektor[dim_idx + 1:]
                evaluer_fn(kandidat)
                s = score_for[kandidat]
                if s > bedste_score:
                    bedste_score, bedste_vektor = s, kandidat
                    forbedret = True
        if not forbedret:
            break


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


def _default_vektor(baseline_produkter: list[dict], nuvaerende_opsaettelse: int) -> tuple:
    """Bygger den vektor der svarer til brugerens NUVÆRENDE plan — bruges til
    at garantere den altid indgår som kandidat, se _kandidat_vektorer."""
    delvektor = tuple(
        ("start", pr["key"], pr["start_alder"])
        for pr in baseline_produkter if pr["udb_type"] != "engangsbeloeb"
    )
    return delvektor + (("opsaet", None, nuvaerende_opsaettelse),)


def _raa_indkomst_pr_alder(resultat: dict) -> dict[int, float]:
    """Rå (ikke-udjævnet) beløb pr. alder i pensionsfasen, i dagens
    købekraft når inflation er sat — bruges til at sammenligne to planers
    FAKTISKE udbetaling alder for alder (ikke kalenderår, da to kandidater
    typisk har forskudte tidslinjer)."""
    pension_raekker = [r for r in resultat["jaevn_tabel"] if r["fase"] == "pension"]
    return {
        r["alder"]: (r["normal_mdr_real"] if r["normal_mdr_real"] is not None else r["normal_mdr"])
        for r in pension_raekker
    }


def score(resultat: dict, obj: Objektiv, baseline_raa_pr_alder: dict[int, float]) -> tuple[float, float]:
    """Returnerer (score, jaevn_niveau_realt). Score er det faste,
    bæredygtige månedsbeløb i DAGENS købekraft — højere er bedre, det ER
    selve målet. Score = -inf hvis planen enten bryder en (eventuel) hård
    bundgrænse, gør et OVERLAPPENDE år ringere end default-planens eget
    samme år (se modulets docstring), eller indeholder et fuldstændigt
    indkomst-hul."""
    jaevn_tabel = [r for r in resultat["jaevn_tabel"] if r["fase"] == "pension"]
    jaevn_niveau = jaevn_niveau_realt(resultat)
    graense = obj.haard_minimum_mdr if obj.haard_minimum_mdr is not None else 0.0

    for row in jaevn_tabel:
        udjaevnet = row["jaevn_mdr_real"] if row["jaevn_mdr_real"] is not None else row["jaevn_mdr"]
        if udjaevnet < graense:
            return float("-inf"), 0.0

    candidate_raa = _raa_indkomst_pr_alder(resultat)
    for alder, baseline_vaerdi in baseline_raa_pr_alder.items():
        kandidat_vaerdi = candidate_raa.get(alder)
        if kandidat_vaerdi is not None and kandidat_vaerdi < baseline_vaerdi * RAA_AAR_TOLERANCE:
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
    profil_partner: Optional[dict] = None,
    parametre_partner: Optional[dict] = None,
) -> OptimeringsResultat:
    if obj is None:
        obj = Objektiv()

    # Fuld partner-profil (Fase D): partnerens indkomst-per-kalenderår regnes
    # ÉN gang her (partnerens egen plan optimeres ikke) og genbruges i alle
    # kandidat-kald nedenfor — ellers ville hver kandidat blive scoret mod et
    # husstands-uafhængigt (forkert, for lavt) indtægtsgrundlag.
    skat_params: Optional[SkatParametre] = None
    if profil_partner and parametre_partner:
        skat_params = husstand.skat_params_for_person(parametre, profil_partner, parametre_partner)

    baseline = generer_udbetalingstabel(profil, parametre, skat_params)
    if obj.haard_minimum_mdr is None:
        # Ingen grænse angivet af brugeren: beskyt mod at optimeringen finder
        # en plan der er drastisk værre i et enkelt år end den nuværende,
        # uden at kræve brugeren selv opfinder et tal. Baseres på det REELLE
        # (dagens-købekraft) niveau, samme grundlag som selve scoren.
        obj = Objektiv(haard_minimum_mdr=round(jaevn_niveau_realt(baseline) * 0.7))
    baseline_raa_pr_alder = _raa_indkomst_pr_alder(baseline)

    alle_produkter = baseline["produkter"]

    if not alle_produkter:
        return OptimeringsResultat(evalueret=0, mulige_kombinationer=0, antal_gennemfoerlige=0, bedste=None)

    nuvaerende_opsaettelse = int(parametre.get("folkepension_opsaettelse_aar", 0) or 0)
    noegler, dimensioner = _byg_soegerum(
        alle_produkter, baseline["fp_alder"], baseline["pensionsalder"], nuvaerende_opsaettelse
    )
    default_vektor = _default_vektor(alle_produkter, nuvaerende_opsaettelse)
    vektorer, mulige_kombinationer = _kandidat_vektorer(dimensioner, maks_evalueringer, default_vektor)

    evaluerede: list[tuple[tuple, float, float]] = []  # (vektor, score, jaevn_netto_mdr)
    sete: set[tuple] = set()
    score_for: dict[tuple, float] = {}

    def _evaluer(vektor: tuple) -> None:
        if vektor in sete:
            return
        sete.add(vektor)
        p, _, _ = _parametre_for_vektor(parametre, vektor)
        resultat = generer_udbetalingstabel(profil, p, skat_params)
        s, jaevn = score(resultat, obj, baseline_raa_pr_alder)
        evaluerede.append((vektor, s, jaevn))
        score_for[vektor] = s

    for vektor in vektorer:
        _evaluer(vektor)

    # Grådig koordinat-søgning forankret i brugerens nuværende plan — fanger
    # enkelt-produkt-forbedringer (og typiske sekventielle to-produkt-
    # justeringer) som en stikprøve over et stort søgerum kan misse, se
    # modulets docstring.
    _koordinat_udvid(dimensioner, default_vektor, _evaluer, score_for)

    evaluerede.sort(key=lambda x: x[1], reverse=True)
    gennemfoerlige = [e for e in evaluerede if e[1] != float("-inf")]

    if not gennemfoerlige:
        return OptimeringsResultat(
            evalueret=len(evaluerede), mulige_kombinationer=mulige_kombinationer,
            antal_gennemfoerlige=0, bedste=None,
        )

    # Kun de bedste 5 får et fuldt genberegnet resultat vedhæftet (til visning/
    # "Anvend denne plan") — resten af søgningen holder kun (vektor, score,
    # jaevn) i hukommelsen, ikke den fulde generer_udbetalingstabel-output.
    top_kandidater: list[Kandidat] = []
    for vektor, s, jaevn in gennemfoerlige[:5]:
        p, start_aldre, udb_aar = _parametre_for_vektor(parametre, vektor)
        opsaettelse = next((v for slag, _, v in vektor if slag == "opsaet"), 0)
        resultat = generer_udbetalingstabel(profil, p, skat_params)
        top_kandidater.append(Kandidat(
            produkt_start_aldre=start_aldre,
            produkt_udb_aar=udb_aar,
            folkepension_opsaettelse_aar=opsaettelse,
            score=s, jaevn_netto_mdr=jaevn,
            resultat=resultat,
        ))

    # Følsomhed: spænd (max-min) i gennemsnitlig score pr. beslutningsdimension,
    # kun blandt gennemførlige kandidater.
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
        mulige_kombinationer=mulige_kombinationer,
        antal_gennemfoerlige=len(gennemfoerlige),
        bedste=top_kandidater[0],
        top=top_kandidater,
        foelsomhed=foelsomhed,
    )
