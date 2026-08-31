"""
Husstands-orkestrering (Fase D) — kobler to selvstændige personers
pensionsberegning sammen der hvor dansk lovgivning reelt kobler dem: kun i
folkepensionstillægget og den personlige tillægsprocent (§ 29-modregningen
for gifte), som begge bruger et KOMBINERET indtægtsgrundlag. Selve
indkomstskatten er individuel og upåvirket — hver persons egen opsparings-
og udbetalingsberegning kører derfor uændret, kun med den anden persons
reelle, årsvarierende indkomst tilføjet i samspils-laget.

Ingen af de to personers udbetalingsplan optimeres eller udjævnes på tværs
af hinanden (buffer/jævn-udjævning kører uafhængigt for hver) — det ville
kræve en antagelse om delt likviditet der ikke er en del af dette arbejde.
"""
from __future__ import annotations
from datetime import date

from engine import generer_udbetalingstabel, SkatParametre, _foedselsaar_fra_profil


def _indtaegtsgrundlag_pr_kalenderaar(resultat: dict, alder_nu: int) -> dict[int, float]:
    """Udtrækker en persons eget (partner-uafhængige) indtægtsgrundlag pr.
    kalenderår fra en solo-kørsel — samme kalenderårs-formel som Fase B/C
    allerede bruger internt i engine.py's årsløkke."""
    basisaar = date.today().year
    return {
        basisaar + (row["alder"] - alder_nu): row["indtaegtsgrundlag_aar"]
        for row in resultat["tabel"]
    }


def skat_params_for_person(
    parametre_selv: dict, profil_partner: dict, parametre_partner: dict,
) -> SkatParametre:
    """Kører partnerens SOLO-kørsel (deres egen plan påvirkes ikke af hvad
    'selv' foretager sig) og bygger den SkatParametre 'selv' skal bruge for
    korrekt koblet samspil — samme byggesten som beregn_husstand, men
    genbrugelig for én side ad gangen (fx sekventeringsoptimeringen, der
    kun søger over den ene persons produkter og derfor kun behøver
    partnerens indkomst udregnet ÉN gang, ikke pr. kandidat)."""
    alder_nu_partner = int(profil_partner.get("person", {}).get("alder") or 0)
    solo_partner = generer_udbetalingstabel(profil_partner, parametre_partner)
    indkomst_partner_pr_aar = _indtaegtsgrundlag_pr_kalenderaar(solo_partner, alder_nu_partner)
    foedselsaar_partner = _foedselsaar_fra_profil(profil_partner)
    return _skat_params_med_partner(parametre_selv, foedselsaar_partner, indkomst_partner_pr_aar)


def beregn_husstand(
    profil_a: dict, parametre_a: dict,
    profil_b: dict, parametre_b: dict,
) -> dict:
    """Kører fire engine-kald (to solo + to endelige, ubetydeligt givet
    ~0,5 ms/kald jf. sekventeringsoptimeringens benchmark): solo-kørslerne
    udtrækker hver persons reelle indtægtsgrundlag pr. kalenderår, som
    derefter fødes ind i den ANDEN persons endelige, korrekt koblede
    kørsel. Returnerer {"a": resultat_a, "b": resultat_b}."""
    skat_a = skat_params_for_person(parametre_a, profil_b, parametre_b)
    skat_b = skat_params_for_person(parametre_b, profil_a, parametre_a)

    resultat_a = generer_udbetalingstabel(profil_a, parametre_a, skat_a)
    resultat_b = generer_udbetalingstabel(profil_b, parametre_b, skat_b)

    return {"a": resultat_a, "b": resultat_b}


def _skat_params_med_partner(
    parametre: dict, partner_foedselsaar: int | None, partner_indkomst_pr_kalenderaar: dict[int, float],
) -> SkatParametre:
    """Bygger samme SkatParametre-afledning som engine.generer_udbetalingstabel
    selv ville, men med partnerens REELLE fødselsår og indkomst-per-år
    tilføjet i stedet for Fase B's flade, manuelt indtastede overslag."""
    civilstand = parametre.get("civilstand") or "gift_samlevende"
    return SkatParametre.fra_pct(
        kommuneskat_pct=float(parametre.get("kommuneskat_pct", 25.0)),
        kirkeskat_pct=float(parametre.get("kirkeskat_pct", 0.7)),
        enlig=(civilstand != "gift_samlevende"),
        partner_foedselsaar=partner_foedselsaar,
        partner_indkomst_pr_kalenderaar=partner_indkomst_pr_kalenderaar,
    )
