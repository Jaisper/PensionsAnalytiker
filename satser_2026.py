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
MEDIECHECK = Sats("yd.mediecheck", 1_552, "Satser 2026; kræver tillægsprocent 100", verificeret=False)

# ── Folkepensions-opsættelse (sekventeringsoptimering) ───────────────────────
# Forenklet ventetillæg-tilnærmelse, porteret 1:1 fra pension-core (samme
# uverificerede skøn som kildepakken selv bruger) — den reelle regel bygger på
# Finanstilsynets levetidsforudsætninger og er væsentligt mere kompleks.
FOLKEPENSION_VENTEPROCENT_PR_AAR = Sats(
    "fp.opsaettelse.venteprocent", 0.06,
    "Forenklet estimat, jf. pension-core — bør erstattes af Finanstilsynets levetidsforudsatte ventetillæg",
    verificeret=False,
)
