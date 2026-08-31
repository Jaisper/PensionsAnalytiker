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


def beregn_husstand(
    profil_a: dict, parametre_a: dict,
    profil_b: dict, parametre_b: dict,
) -> dict:
    """Kører fire engine-kald (to solo + to endelige, ubetydeligt givet
    ~0,5 ms/kald jf. sekventeringsoptimeringens benchmark): solo-kørslerne
    udtrækker hver persons reelle indtægtsgrundlag pr. kalenderår, som
    derefter fødes ind i den ANDEN persons endelige, korrekt koblede
    kørsel. Returnerer {"a": resultat_a, "b": resultat_b}."""
    alder_nu_a = int(profil_a.get("person", {}).get("alder") or 0)
    alder_nu_b = int(profil_b.get("person", {}).get("alder") or 0)

    solo_a = generer_udbetalingstabel(profil_a, parametre_a)
    solo_b = generer_udbetalingstabel(profil_b, parametre_b)

    indkomst_a_pr_aar = _indtaegtsgrundlag_pr_kalenderaar(solo_a, alder_nu_a)
    indkomst_b_pr_aar = _indtaegtsgrundlag_pr_kalenderaar(solo_b, alder_nu_b)

    # Partnerens fødselsår kommer nu fra dennes EGEN uploadede rapport — langt
    # mere præcist end Fase B's manuelt indtastede alder, som stadig er
    # fallback-vejen når der ikke findes en fuld partner-profil (se app.py).
    foedselsaar_a = _foedselsaar_fra_profil(profil_a)
    foedselsaar_b = _foedselsaar_fra_profil(profil_b)

    skat_a = _skat_params_med_partner(parametre_a, foedselsaar_b, indkomst_b_pr_aar)
    skat_b = _skat_params_med_partner(parametre_b, foedselsaar_a, indkomst_a_pr_aar)

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
