"""
Regelpakke 2026 — satser og beløbsgrænser som DATA, ikke kode.

Porteret fra pension-core (TypeScript-prototype, egen produktspec) efter at
have kørt dens 22 håndverificerede golden-tests. Hver sats bærer sin egen
proveniens (`kilde`) og et `verificeret`-flag, så det altid kan spores hvilken
regel der ligger bag et tal — og så uverificerede satser kan blokeres i en
fremtidig "strict mode" uden at ombygge noget.

Kilde for alle verificerede tal: Satser 2026 / Skattereform 2026 / Lov om
social pension, som gengivet i pension-core's rules/2026.ts.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class Sats:
    id: str
    vaerdi: Union[float, bool]
    kilde: str
    verificeret: bool = True


def brug(sats: Sats, strict: bool, spor: list[str]) -> Union[float, bool]:
    """Returnerer satsens værdi og logger dens id i sporet. Kaster i strict
    mode hvis satsen ikke er verificeret."""
    if strict and not sats.verificeret:
        raise ValueError(f"Uverificeret regel anvendt i strict mode: {sats.id} ({sats.kilde})")
    spor.append(sats.id)
    return sats.vaerdi


@dataclass(frozen=True)
class Progressionstrin:
    id: str
    navn: str
    sats: float
    bundgraense: float
    skatteloft: float
    verificeret: bool = True
    kilde: str = "Skattereform 2026"


# ── Indkomstskat — progressive trin ──────────────────────────────────────────
# Hvert trin har sit EGET skatteloft (PSL § 19) — de tre lofter må ikke
# forveksles med hinanden; loftet gælder for den SAMLEDE marginalsats når
# trinnet rammes, ekskl. AM-bidrag og kirkeskat.
PROGRESSION: list[Progressionstrin] = [
    Progressionstrin("skat.mellemskat", "Mellemskat", 0.075, 641_200, 0.4457),
    Progressionstrin("skat.topskat", "Topskat", 0.075, 777_900, 0.5207),
    Progressionstrin("skat.toptopskat", "Top-topskat", 0.05, 2_592_700, 0.5707),
]

# ── Personfradrag ─────────────────────────────────────────────────────────────
# Skattefrit bundfradrag der trækkes fra INDEN bundskat/kommuneskat/kirkeskat
# beregnes — savnet helt i den porterede pension-core-kilde (som ikke selv
# modellerer det). Verificeret 2026-09-20 mod Skatteministeriets egen
# satsopgørelse (svmn.dk, PSL § 20-regulering): 54.100 kr. i 2026 (op fra
# 51.600 kr. i 2025) — den tidligere værdi her (49.700 kr.) var hverken det
# ene eller det andet års korrekte tal.
PERSONFRADRAG_AAR = Sats(
    "skat.personfradrag", 54_100,
    "Skatteministeriet (svmn.dk), PSL § 20-regulering, 2026",
)

# ── Folkepension grundbeløb ───────────────────────────────────────────────────
FOLKEPENSION_GRUNDBELOEB_AAR = Sats("fp.grundbeloeb", 90_528, "Satser 2026 (7.544 kr./md.)")

# ── Pensionstillæg — verificeret mod satstabellerne (rettet fra en tidligere
# aritmetisk udledning der ramte gifte 100 kr. for højt) ────────────────────
TILLAEG_ENLIG = {
    "id": "fp.tillaeg.enlig",
    "ydelse": Sats("fp.tillaeg.enlig.ydelse", 104_748, "Fradragsbeløb 2026, folkepension (enlige)"),
    "bundfradrag": Sats("fp.tillaeg.enlig.bund", 99_200, "Fradragsbeløb 2026, folkepension (enlige)"),
    "sats": Sats("fp.tillaeg.enlig.sats", 0.309, "Lov om social pension § 29"),
}
TILLAEG_GIFT_PARTNER_IKKE_PENSIONIST = {
    "id": "fp.tillaeg.gift.32",
    "ydelse": Sats("fp.tillaeg.gift.32.ydelse", 53_604, "Fradragsbeløb 2026, folkepension (gifte/samlevende)"),
    "bundfradrag": Sats("fp.tillaeg.gift.32.bund", 198_800, "Fradragsbeløb 2026, folkepension (gifte/samlevende)"),
    "sats": Sats("fp.tillaeg.gift.32.sats", 0.32, "Lov om social pension § 29"),
}
TILLAEG_GIFT_BEGGE_PENSIONISTER = {
    "id": "fp.tillaeg.gift.16",
    "ydelse": Sats("fp.tillaeg.gift.16.ydelse", 53_604, "Fradragsbeløb 2026, folkepension (gifte, begge pensionister)"),
    "bundfradrag": Sats("fp.tillaeg.gift.16.bund", 198_800, "Fradragsbeløb 2026, folkepension (gifte, begge pensionister)"),
    "sats": Sats("fp.tillaeg.gift.16.sats", 0.16, "Lov om social pension § 29"),
}

# ── Personlig tillægsprocent — SELVSTÆNDIG regel, ikke en afledning af
# pensionstillæggets aftrapning. Falder trinvis (Math.floor), langt stejlere,
# og rammer nul VÆSENTLIGT lavere i indkomst end pensionstillægget gør.
# Styrer ældrecheck og mediecheck — IKKE selve pensionstillægget.
TILLAEGSPROCENT_ENLIG = {
    "bundfradrag": Sats("fp.tillaegsprocent.enlig.bund", 35_700, "Satser 2026: 100% ved <= 35.700 kr."),
    "trinstoerrelse": Sats("fp.tillaegsprocent.enlig.trin", 635, "Satser 2026: -1 pct.point pr. 635 kr."),
    "nulpunkt": 99_200,
}
TILLAEGSPROCENT_GIFT = {
    "bundfradrag": Sats("fp.tillaegsprocent.gift.bund", 70_600, "Satser 2026: 100% ved <= 70.600 kr."),
    "trinstoerrelse": Sats("fp.tillaegsprocent.gift.trin", 1_282, "Satser 2026: -1 pct.point pr. 1.282 kr."),
    "nulpunkt": 198_800,
}

# ── Tillægsprocent-styrede ydelser ───────────────────────────────────────────
LIKVID_FORMUEGRAENSE = Sats("yd.formuegraense", 108_000, "Satser 2026 (samme for enlige og par) — HÅRD tærskel, ikke glidende")
AELDRECHECK = Sats("yd.aeldrecheck", 26_900, "Satser 2026, supplerende pensionsydelse")
AELDRECHECK_SKATTEPLIGTIG = Sats("yd.aeldrecheck.skat", True, "Beskattes som anden pensionsindkomst")
# Verificeret 2026-09-20 mod borger.dk (den officielle borgerportal): "Hvis
# du har ret til mediecheck, får du 1.552 kr. (2026) før skat."
MEDIECHECK = Sats("yd.mediecheck", 1_552, "borger.dk, mediecheck, 2026; kræver tillægsprocent 100")

# ── Pensionsafkastbeskatning (PAL) ───────────────────────────────────────────
# Rent informativ i denne kodebase — bruges ALDRIG i selve beregningen, fordi
# brugerens eget "Forventet afkast"-interviewsvar allerede ER efter PAL-skat
# (se interviewets egen formulering og app.py's dynamiske kontekstblok).
# Optaget her udelukkende så tallet har ét kildested når det nævnes i prompten.
PAL_SATS = Sats("pal.sats", 0.153, "Pensionsafkastbeskatningsloven (PAL) § 2")

# ── Folkepensions-opsættelse (sekventeringsoptimering) ───────────────────────
# Forenklet ventetillæg-tilnærmelse — den REELLE regel beregner venteprocenten
# som forholdet mellem antal måneder pensionen er udskudt og den gennemsnitlige
# restlevetid ved den alder pensionen faktisk opsættes fra, dvs. den er
# alders-/levetidsafhængig og IKKE lineær (hvert ekstra opsættelsesår giver et
# STØRRE tillæg end det foregående, fordi restlevetiden falder) — en enkelt
# flad sats kan derfor aldrig blive "verificeret" i egentlig forstand.
# Bruges som FALLBACK når det konkrete tabelopslag nedenfor ikke dækker
# (anden opsættelsesalder end 67, eller mere end 10 år) — sat til den
# nøjagtige tabels år-1-værdi som en forsigtig nedre-grænse-tilnærmelse.
FOLKEPENSION_VENTEPROCENT_PR_AAR = Sats(
    "fp.opsaettelse.venteprocent", 0.0571,
    "Ældre Sagen, eksempel for udskydelse fra 67 år i 2026 — den reelle regel er "
    "alders-/levetidsafhængig og ikke-lineær, ikke en fast sats; se "
    "VENTEPROCENT_LIVSVARIG_2026_FRA_67 for den nøjagtige, ikke-lineære tabel",
    verificeret=False,
)

# Nøjagtig, kildehenvist tabel for det specifikke (men almindelige) tilfælde:
# opsættelse af folkepensionen fra netop 67 år, 2026-niveau. Nøgle = antal
# opsatte hele år, værdi = venteprocent (multiplikator - 1) for resten af
# livet. IKKE lineær — bekræfter at FOLKEPENSION_VENTEPROCENT_PR_AAR ovenfor
# kun er en forsigtig tilnærmelse for andre opsættelsesaldre/-år.
# Betingelse for at optjene: mindst 750 løntimer/år i opsættelsesperioden,
# maks. 10 år. Kilde: Ældre Sagen, 2026-satser (SEKUNDÆR — ikke en primær
# myndighedskilde, men internt konsistent med den generelle beregningsregel).
VENTEPROCENT_LIVSVARIG_2026_FRA_67: dict[int, float] = {
    1: 0.0571, 2: 0.1194, 3: 0.1875, 4: 0.2623, 5: 0.3448,
    6: 0.4337, 7: 0.5350, 8: 0.6443, 9: 0.7660, 10: 0.9023,
}

# ── Engangsydelser (ikke modelleret i selve beregningen) ─────────────────────
# Skattefri engangscheck til folkepensionister i 2026, indført ved hastelov —
# betinget af likvid formue <= 350.000 kr. (årsopgørelsen 2025) og modtaget
# folkepension i november 2025. Udbetalt maj 2026, modregnes ikke i andre
# ydelser. Optaget som dokumenteret fakta, men IKKE bygget ind i
# udbetalingsfremskrivningen: den er et engangsbeløb i ét specifikt år, ikke
# en tilbagevendende sats, og ville kræve en formue-tærskel-kontrol
# engine.py i dag ikke har (kun det bredere `likvid_formue`-felt til
# ældrecheck). Se README.md.
CHECK_2026_ENGANGS = Sats(
    "yd.engangscheck_2026", 2_500,
    "fm.dk, faktaark om skattefri check til folkepensionister i 2026 (27.01.2026)",
)

# ── Finansielle grundkonstanter (tidligere bare navngivne tal i engine.py,
# uden kilde/verifikationsstatus — flyttet hertil så AL sats-drift går gennem
# samme provenance-mønster, ikke kun progressionstrin/tillæg) ────────────────
AM_BIDRAG = Sats("skat.am_bidrag", 0.08, "Lov om arbejdsmarkedsbidrag § 1")
BUNDSKAT = Sats("skat.bundskat", 0.1201, "Personskatteloven § 5/6, 2026-sats")
A_SKAT_NETTO_FAKTOR = Sats(
    "kapital.a_skat_netto_faktor", 0.60,
    "Netto-andel efter 40% afgift ved udbetaling af afgiftspligtig kapitalpension (PBL § 25)",
)
# Verificeret 2026-09-20 direkte mod skat.dk: "Du kan samlet set få fradrag
# for indbetalinger på op til 68.700 kr. i 2026 (65.500 kr. i 2025)." Den
# tidligere værdi her (63.100 kr.) var et ældre (2023/2024-niveau) tal.
# Uafhængigt krydsbekræftet af PenSam (samme tal, +4,8% fra 65.500 kr. i 2025
# — matcher PSL § 20's generelle 2026-reguleringssats).
RATEPENSION_LOFT = Sats(
    "ratepension.loft", 68_700,
    "skat.dk, fradrag for indbetalinger til pension, 2026 (2025: 65.500 kr.) — "
    "uafhængigt krydsbekræftet af PenSam",
)
# Verificeret 2026-09-20 mod ATP.dk's egen pressemeddelelse om 2026-stigningen:
# "Den udbetalte ATP-pension lyder på 17.340 kr. om året for et gennemsnitligt
# medlem fra 1. januar 2026" (svarer til 1.445 kr./mdr.) — den tidligere
# værdi her (1.825 kr./mdr.) havde ingen fundet kilde.
ATP_MDR_STANDARD = Sats(
    "atp.mdr_standard", 1_445,
    "ATP.dk, pressemeddelelse om pensionsstigning pr. 1. januar 2026 (17.340 kr./år for et "
    "gennemsnitligt medlem) — bruges kun som fallback når PDF-rapporten ikke selv oplyser et "
    "konkret ATP-beløb",
)
# Bevidst valg af "gennemsnitligt medlem" (inkl. delvis optjening) frem for
# et "fuldt optjent"-tal (~2.150 kr./mdr., Seniorhåndbogen, svagere kildet)
# — som fallback for en UKENDT brugers ATP er gennemsnittet det mere
# forsvarlige gæt, uafhængigt af hvilket tal der isoleret set er højest.
# IKKE verificeret ved dette gennemsyn: kræver Finanstilsynets faktiske
# K2023-levetidsbenchmark (aldersspecifik restlevetidstabel), ikke blot ét
# samlet tal — se finanstilsynet.dk/finansielle-temaer/forsikring-og-pension/
# levetidsmodel. 18 år er en grov, konservativ tommelfingerregel, ikke
# efterprøvet mod selve benchmark-tabellen.
LIVSVARIG_ESTIMAT_AAR = Sats(
    "livsvarig.estimat_aar", 18,
    "Konservativt fallback-skøn for livsvarig pensions udbetalingsperiode (overstyres normalt af "
    "et alder-justeret levetidsestimat) — bør efterprøves mod Finanstilsynets K2023-benchmark, "
    "ikke gjort ved dette gennemsyn",
    verificeret=False,
)

# ── Aldersopsparing — nær-pension-loft (PBL § 16, stk. 1, reguleret efter
# PSL § 20). Rettet 2026-09-20 efter fund af to indbyrdes modstridende,
# ukildede tal i koden (60.900 kr/7 år ét sted, 58.900 kr/5 år et andet,
# ingen af dem korrekte) — se README.md's "Kendte forenklinger"-afsnit.
# Kilde: skat.dk, Den juridiske vejledning C.A.10.2.6.3.3.1,
#        "Beløbsgrænserne på 9.900 kr. og 64.200 kr." (2026)
ALDERSOPSPARING_LOFT = Sats(
    "aldersopsparing.loft", 9_900, "PBL § 16 stk. 1 / skat.dk C.A.10.2.6.3.3.1 (2026)"
)
ALDERSOPSPARING_LOFT_FORHOEJET = Sats(
    "aldersopsparing.loft_forhoejet", 64_200, "PBL § 16 stk. 1 / skat.dk C.A.10.2.6.3.3.1 (2026)"
)
ALDERSOPSPARING_FORHOEJET_AAR_FOER_FOLKEPENSION = 7   # indkomstår
ALDERSOPSPARING_FORHOEJET_AAR_EFTER_UDBETALINGSALDER = 20
# De to nedenstående er dokumenteret som verificerede fakta, men bruges IKKE
# til beregning i denne omgang — PDF-parseren udtrækker i dag ikke hvornår en
# udbetaling er påbegyndt, som er forudsætningen for at kunne afgøre om
# diskvalifikations-/overførsels-strafafgiften rammer. Se README.md.
ALDERSOPSPARING_DISKVALIFIKATION_AAR = 10   # udbetaling påbegyndt efter 01-04-2018
ALDERSOPSPARING_STRAFAFGIFT = 0.20
ALDERSOPSPARING_STRAFAFGIFT_DISKVALIFICERET = 0.40
ALDERSOPSPARING_STRAFAFGIFT_VED_OVERFOERSEL = 0.04
