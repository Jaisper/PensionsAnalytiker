"""
PensionEngine — deterministisk beregningskerne for dansk pensionsrådgivning.
Kilde: PBL, Lov om social pension, SKATs satser 2025.

Bruges af app.py til at forudberegne FV, skat og udbetalingstabel
INDEN LLM-kaldet, så assistenten præsenterer tal — aldrig beregner dem.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

# ── 2025-satser ──────────────────────────────────────────────────────────────

AM_BIDRAG                         = 0.08
BUNDSKAT                          = 0.1201
TOPSKAT                           = 0.15
TOPSKAT_GRAENSE_PI                = 588_900    # kr/år personlig indkomst (efter AM-bidrag)
KOMMUNESKAT_DEFAULT               = 0.250
KIRKESKAT_DEFAULT                 = 0.007

FOLKEPENSION_MDR                  = 7_955      # kr/mdr grundbeløb 2025
ATP_MDR_STANDARD                  = 1_825      # kr/mdr estimat

PENSIONSTILLAEG_MAX_ENLIG_AAR     = 8_891 * 12    # 106.692 kr/år
PENSIONSTILLAEG_BUNDFRADRAG_ENLIG = 98_400         # kr/år
PENSIONSTILLAEG_MODREGNING        = 0.309          # 30,9 % jf. § 29

RATEPENSION_LOFT                  = 63_100
LIVSVARIG_ESTIMAT_AAR             = 25             # konservativt estimat


@dataclass
class SkatParametre:
    kommuneskat: float = KOMMUNESKAT_DEFAULT    # decimal, fx 0.250
    kirkeskat: float   = KIRKESKAT_DEFAULT
    enlig: bool        = True

    @classmethod
    def fra_pct(
        cls,
        kommuneskat_pct: float = 25.0,
        kirkeskat_pct: float = 0.7,
        enlig: bool = True,
    ) -> "SkatParametre":
        return cls(kommuneskat=kommuneskat_pct / 100, kirkeskat=kirkeskat_pct / 100, enlig=enlig)


# ── Folkepensionsalder ───────────────────────────────────────────────────────

def folkepension_alder(foedselsaar: int) -> int:
    """
    Folkepensionsalder jf. Velfærdsaftalen 2006 og Lov om social pension.

    Logik: stiger til X i år Y  ⇒  berørt hvis du fylder X EFTER år Y.
      < 1963 : 67   (fylder 67 før 2030)
      1963–1966 : 68 (fylder 67 i 2030–2033; venter til 68)
      1967–1970 : 69 (fylder 68 i 2035–2038; venter til 69)
      1971+     : 70 (planlagt fra 2040 — ikke endeligt vedtaget pr. 2025)
    """
    if foedselsaar < 1963:   return 67
    if foedselsaar <= 1966:  return 68
    if foedselsaar <= 1970:  return 69
    return 70


def tidligste_private_pensionsalder(fp_alder: int) -> int:
    """PALFP: tidligste private pensionsalder = folkepensionsalder − 5 år."""
    return fp_alder - 5


# ── Finansielle kernefunktioner ──────────────────────────────────────────────

def beregn_fv(pv: float, pmt: float, r: float, n: int) -> float:
    """
    Fremtidsværdi med løbende indbetaling.
    FV = PV·(1+r)^n + PMT·((1+r)^n − 1)/r
    """
    if n <= 0:  return max(0.0, pv)
    if r == 0:  return pv + pmt * n
    g = (1 + r) ** n
    return pv * g + pmt * (g - 1) / r


def beregn_maanedlig_annuitet(fv: float, r: float, m: int) -> float:
    """
    Månedlig udbetaling fra kapital FV over m år.
    PMT = FV · (r/12) / (1 − (1+r/12)^(−m·12))
    """
    if fv <= 0 or m <= 0:  return 0.0
    rm = r / 12
    if rm == 0:  return fv / (m * 12)
    return fv * rm / (1 - (1 + rm) ** (-m * 12))


# ── Skatteberegning ──────────────────────────────────────────────────────────

def _topskat_andel(dette_pi: float, total_pi: float) -> float:
    """Forholdsmæssig andel af topskatten for dette produkts PI-bidrag."""
    if total_pi <= TOPSKAT_GRAENSE_PI or total_pi == 0:
        return 0.0
    total_topskat = (total_pi - TOPSKAT_GRAENSE_PI) * TOPSKAT
    return total_topskat * (dette_pi / total_pi)


def _netto_s_med_am(
    brutto: float,
    skat: SkatParametre,
    total_pi: float,
    dette_pi: float,
) -> float:
    """Netto for S-indkomst MED AM-bidrag (ratepension, livsvarig pension)."""
    basis = BUNDSKAT + skat.kommuneskat + skat.kirkeskat
    netto = brutto * (1 - AM_BIDRAG) * (1 - basis)
    netto -= _topskat_andel(dette_pi, total_pi)
    return netto


def _netto_s_uden_am(
    brutto: float,
    skat: SkatParametre,
    total_pi: float,
    dette_pi: float,
) -> float:
    """Netto for S-indkomst UDEN AM-bidrag (folkepension, ATP)."""
    basis = BUNDSKAT + skat.kommuneskat + skat.kirkeskat
    netto = brutto * (1 - basis)
    netto -= _topskat_andel(dette_pi, total_pi)
    return netto


def beregn_netto_skat(
    brutto_aar: float,
    skat_type: str,
    skat: SkatParametre,
    total_s_pi_aar: float = 0.0,
    har_am_bidrag: bool = True,
) -> float:
    """
    Netto årslig udbetaling efter skat/afgift.

    skat_type       : 'S' = skattepligtig, 'A' = 40 % afgift, 'F' = skattefri
    total_s_pi_aar  : samlet personlig indkomst alle S-kilder (til korrekt topskat)
    har_am_bidrag   : False for folkepension og ATP
    """
    if brutto_aar <= 0:   return 0.0
    if skat_type == "F":  return brutto_aar
    if skat_type == "A":  return brutto_aar * 0.60

    dette_pi = brutto_aar * (1 - AM_BIDRAG) if har_am_bidrag else brutto_aar
    total_pi = total_s_pi_aar if total_s_pi_aar else dette_pi

    if har_am_bidrag:
        return _netto_s_med_am(brutto_aar, skat, total_pi, dette_pi)
    return _netto_s_uden_am(brutto_aar, skat, total_pi, dette_pi)


# ── Pensionstillæg (modregning) ──────────────────────────────────────────────

def folkepension_pensionstillaeg_aar(
    privat_s_indkomst_aar: float,
    skat: SkatParametre,
) -> float:
    """
    Pensionstillæg efter modregning.
    Kilde: Lov om social pension § 29 (enlige).

    KUN privat S-indkomst (rate, livsvarig) modregnes.
    Aldersopsparing (F-skat) tæller IKKE med.
    """
    max_t      = PENSIONSTILLAEG_MAX_ENLIG_AAR if skat.enlig else PENSIONSTILLAEG_MAX_ENLIG_AAR * 0.55
    bundfradrag = PENSIONSTILLAEG_BUNDFRADRAG_ENLIG if skat.enlig else PENSIONSTILLAEG_BUNDFRADRAG_ENLIG * 2.2
    overskud   = max(0.0, privat_s_indkomst_aar - bundfradrag)
    return max(0.0, max_t - overskud * PENSIONSTILLAEG_MODREGNING)


# ── Hjælpefunktioner ─────────────────────────────────────────────────────────

def _foedselsaar_fra_profil(profil: dict) -> Optional[int]:
    person   = profil.get("person", {})
    foedsels = person.get("foedselsdato", "")
    if foedsels:
        try:
            return int(foedsels.split(".")[-1])
        except (ValueError, IndexError):
            pass
    alder = person.get("alder")
    if alder:
        from datetime import date
        return date.today().year - int(alder)
    return None


def _udled_udbetalingsaar(aldersperioder: dict) -> int:
    """Udled udbetalingsperiode i år fra periodenavne (fx '60–74 år' → 15)."""
    total = 0
    for k in (aldersperioder or {}):
        if re.match(r"^\d+ år$", k):
            total += 1
        elif m := re.match(r"^(\d+)-(\d+) år$", k):
            total += int(m.group(2)) - int(m.group(1)) + 1
    return total


# ── Hoved-beregning ──────────────────────────────────────────────────────────

def generer_udbetalingstabel(
    profil: dict,
    parametre: dict,
    skat_params: Optional[SkatParametre] = None,
) -> dict:
    """
    Beregner komplet pensionsudbetalingstabel.

    Kræver i parametre:
      pensionsalder (int)
    Valgfrit med defaults:
      afkast_pct (float, default 4.0)
      udbetaling_aar (int, default 30)
      kommuneskat_pct (float, default 25.0)
      netto_indbetaling (float, default 0)
      enlig (bool, default True)

    Returnerer dict med:
      produkter, engangsbeloeb, tabel, fp_alder, pensionsalder, advarsler, parametre
    """
    if skat_params is None:
        skat_params = SkatParametre.fra_pct(
            kommuneskat_pct=float(parametre.get("kommuneskat_pct", 25.0)),
            enlig=bool(parametre.get("enlig", True)),
        )

    pensionsalder  = int(parametre["pensionsalder"])
    udbetaling_aar = int(parametre.get("udbetaling_aar", 30))
    r              = float(parametre.get("afkast_pct", 4.0)) / 100
    netto_indbetal = float(parametre.get("netto_indbetaling", 0))

    person   = profil.get("person", {})
    alder_nu = int(person.get("alder") or 0)
    n        = max(0, pensionsalder - alder_nu)

    foedselsaar = _foedselsaar_fra_profil(profil)
    fp_alder    = folkepension_alder(foedselsaar) if foedselsaar else 67

    ordninger         = profil.get("ordninger", [])
    pensionsprodukter = profil.get("pensionsprodukter", [])
    advarsler         = []

    # Total saldo til PMT-fordeling (ekskl. ATP)
    total_opsparing = sum(
        o.get("opsparing") or 0
        for o in ordninger
        if not o.get("kun_forsikring") and "ATP" not in (o.get("selskab") or "")
    )

    # Ratepension loft-check
    rate_pmt = sum(
        o.get("aarlig_indbetaling") or 0
        for o in ordninger
        if "rate" in (o.get("produkttype") or "").lower()
    )
    if rate_pmt > RATEPENSION_LOFT:
        overskud = rate_pmt - RATEPENSION_LOFT
        advarsler.append(
            f"Ratepension-loft ({RATEPENSION_LOFT:,.0f} kr/år): "
            f"{overskud:,.0f} kr/år flyttes automatisk til livsvarig pension.".replace(",", ".")
        )

    # ── Byg produktliste ──────────────────────────────────────────────────────
    produkter = []
    for prod in pensionsprodukter:
        selskab   = prod.get("selskab") or ""
        ptype     = prod.get("produkttype") or ""
        ptype_l   = ptype.lower()
        skat_type = prod.get("skat_type") or "S"
        perioder  = prod.get("aldersperioder") or {}

        # Spring ATP og Folkepension over — håndteres separat
        if "atp" in selskab.lower() or "atp" in ptype_l or "folkepension" in ptype_l:
            continue

        # Saldo
        pv = float(prod.get("opsparing") or prod.get("estimated_saldo") or 0)
        if not pv:
            nr = str(prod.get("aftalenr") or "")
            for o in ordninger:
                if str(o.get("aftalenr") or "") == nr:
                    pv = float(o.get("opsparing") or 0)
                    break

        # PMT
        pmt = float(prod.get("estimated_pmt") or 0)
        if not pmt and total_opsparing > 0 and pv > 0:
            pmt = netto_indbetal * (pv / total_opsparing)
        if "rate" in ptype_l:
            pmt = min(pmt, RATEPENSION_LOFT)

        fv = beregn_fv(pv, pmt, r, n)

        # Udbetalingstype
        if "kapital" in ptype_l or "aldersopsparing" in ptype_l:
            udb_type, udb_aar  = "engangsbeloeb", 0
            mdr_brutto, stopper = 0.0, pensionsalder
        elif "livsvarig" in ptype_l or "livrente" in ptype_l:
            udb_aar    = LIVSVARIG_ESTIMAT_AAR
            udb_type   = "livsvarig"
            mdr_brutto = beregn_maanedlig_annuitet(fv, r, udb_aar)
            stopper    = None
        else:   # ratepension
            udb_aar    = _udled_udbetalingsaar(perioder) or 15
            udb_type   = "rate"
            mdr_brutto = beregn_maanedlig_annuitet(fv, r, udb_aar)
            stopper    = pensionsalder + udb_aar

        produkter.append({
            "selskab": selskab, "produkttype": ptype, "skat_type": skat_type,
            "pv": pv, "pmt": pmt, "fv": fv,
            "udb_type": udb_type, "udb_aar": udb_aar,
            "mdr_brutto": mdr_brutto, "stopper_ved_alder": stopper,
            "key": f"{selskab}_{ptype}",
        })

    engangsbeloeb = [p for p in produkter if p["udb_type"] == "engangsbeloeb"]
    loebende      = [p for p in produkter if p["udb_type"] != "engangsbeloeb"]

    # ATP-beløb
    atp_prod = next(
        (p for p in pensionsprodukter if "atp" in (p.get("selskab") or "").lower()), None
    )
    if atp_prod:
        per     = atp_prod.get("aldersperioder") or {}
        first_v = next((v for v in per.values() if v), None)
        atp_mdr = round(first_v / 12) if first_v else ATP_MDR_STANDARD
    else:
        atp_mdr = ATP_MDR_STANDARD

    # ── År-for-år tabel ───────────────────────────────────────────────────────
    tabel = []
    for alder in range(pensionsalder, pensionsalder + udbetaling_aar + 1):
        har_fp = alder >= fp_alder

        # S-indkomst dette år (rate + livsvarig) til topskat-beregning
        privat_s_brutto = sum(
            p["mdr_brutto"] * 12
            for p in loebende
            if p["skat_type"] == "S"
            and (p["stopper_ved_alder"] is None or alder < p["stopper_ved_alder"])
        )

        # Total personlig indkomst (PI):
        # private S (med AM) + folkepension + ATP (begge uden AM)
        pi_med_am  = privat_s_brutto * (1 - AM_BIDRAG)
        pi_uden_am = (FOLKEPENSION_MDR * 12 + atp_mdr * 12) if har_fp else 0.0
        total_pi   = pi_med_am + pi_uden_am

        # Netto per produkt
        produkt_data: dict[str, dict] = {}
        for p in loebende:
            aktiv = p["stopper_ved_alder"] is None or alder < p["stopper_ved_alder"]
            if not aktiv:
                produkt_data[p["key"]] = {"mdr_brutto": 0.0, "mdr_netto": 0.0, "skat_type": p["skat_type"]}
                continue
            b_aar = p["mdr_brutto"] * 12
            if p["skat_type"] == "S":
                dette_pi  = b_aar * (1 - AM_BIDRAG)
                netto_aar = _netto_s_med_am(b_aar, skat_params, total_pi, dette_pi)
            elif p["skat_type"] == "F":
                netto_aar = b_aar
            else:   # A
                netto_aar = b_aar * 0.60
            produkt_data[p["key"]] = {
                "mdr_brutto": p["mdr_brutto"],
                "mdr_netto":  netto_aar / 12,
                "skat_type":  p["skat_type"],
            }

        # Folkepension og ATP (ingen AM-bidrag)
        if har_fp:
            fp_netto_aar  = _netto_s_uden_am(
                FOLKEPENSION_MDR * 12, skat_params, total_pi, FOLKEPENSION_MDR * 12
            )
            atp_netto_aar = _netto_s_uden_am(
                float(atp_mdr * 12), skat_params, total_pi, float(atp_mdr * 12)
            )
            fp_mdr_netto  = fp_netto_aar / 12
            atp_mdr_netto = atp_netto_aar / 12

            # Pensionstillæg — kun S-indkomst modregnes (jf. § 29)
            tillaeg_aar = folkepension_pensionstillaeg_aar(privat_s_brutto, skat_params)
            tillaeg_mdr = tillaeg_aar / 12
        else:
            fp_mdr_netto = atp_mdr_netto = tillaeg_mdr = 0.0

        total_netto_mdr = (
            sum(d["mdr_netto"] for d in produkt_data.values())
            + fp_mdr_netto + tillaeg_mdr + atp_mdr_netto
        )

        over_topskat = total_pi > TOPSKAT_GRAENSE_PI
        if over_topskat:
            msg = f"Alder {alder}: bruttoudbetalingen overstiger topskattegrænsen — effektiv marginalbeskatning over 52 %"
            if msg not in advarsler:
                advarsler.append(msg)

        tabel.append({
            "alder":           alder,
            "aar_nr":          alder - pensionsalder + 1,
            "produkter":       produkt_data,
            "fp_mdr_brutto":   float(FOLKEPENSION_MDR) if har_fp else 0.0,
            "fp_mdr_netto":    fp_mdr_netto,
            "atp_mdr_brutto":  float(atp_mdr) if har_fp else 0.0,
            "atp_mdr_netto":   atp_mdr_netto,
            "tillaeg_mdr":     tillaeg_mdr,
            "total_netto_mdr": total_netto_mdr,
            "total_netto_aar": total_netto_mdr * 12,
            "over_topskat":    over_topskat,
        })

    return {
        "produkter":     produkter,
        "engangsbeloeb": engangsbeloeb,
        "tabel":         tabel,
        "fp_alder":      fp_alder,
        "pensionsalder": pensionsalder,
        "advarsler":     advarsler,
        "parametre": {
            "r":               r,
            "n":               n,
            "pensionsalder":   pensionsalder,
            "udbetaling_aar":  udbetaling_aar,
            "fp_alder":        fp_alder,
            "kommuneskat_pct": skat_params.kommuneskat * 100,
            "enlig":           skat_params.enlig,
        },
    }


# ── Formatering til LLM-kontekst ─────────────────────────────────────────────

def format_engine_til_llm(result: dict) -> str:
    """
    Konverterer engine-output til tekst der injiceres som 'Hard Facts' i system-prompt.
    LLM'en præsenterer disse tal direkte — beregner aldrig selv.
    """
    p        = result["parametre"]
    loebende = [pr for pr in result["produkter"] if pr["udb_type"] != "engangsbeloeb"]
    engang   = result["engangsbeloeb"]
    tabel    = result["tabel"]
    fp_alder = result["fp_alder"]
    advarsler = result.get("advarsler", [])

    L = [
        "## BEREGNET PENSIONSANALYSE — HARD FACTS",
        "*(Deterministisk engine — præsenter disse tal direkte, beregn aldrig selv)*",
        "",
        (
            f"Pensionsalder: {p['pensionsalder']} år  |  Folkepensionsalder: {fp_alder} år  |  "
            f"Afkast: {p['r']*100:.1f}% p.a.  |  Kommuneskat: {p['kommuneskat_pct']:.1f}%  |  "
            f"Udbetalingsperiode: {p['udbetaling_aar']} år  |  "
            f"{'Enlig' if p['enlig'] else 'Par'}"
        ),
        "",
    ]

    if advarsler:
        L.append("### ADVARSLER")
        L += [f"- {a}" for a in advarsler]
        L.append("")

    # Tabel 1: FV og månedlig udbetaling
    L += [
        "### TABEL 1 — FREMTIDSVÆRDI OG UDBETALING",
        "| Ordning | Skat | Saldo nu | FV ved pension | Brutto/mdr | Netto/mdr | Varighed |",
        "|---|---|---|---|---|---|---|",
    ]
    for pr in loebende:
        first = next(
            (r for r in tabel if pr["key"] in r["produkter"] and r["produkter"][pr["key"]]["mdr_brutto"] > 0),
            None,
        )
        n_mdr    = first["produkter"][pr["key"]]["mdr_netto"] if first else 0.0
        varighed = "livsvarig (25 år est.)" if pr["udb_type"] == "livsvarig" else f"{pr['udb_aar']} år"
        L.append(
            f"| {pr['selskab']} – {pr['produkttype']} | {pr['skat_type']}"
            f" | {pr['pv']:,.0f} kr. | {pr['fv']:,.0f} kr."
            f" | {pr['mdr_brutto']:,.0f} kr/mdr | {n_mdr:,.0f} kr/mdr | {varighed} |".replace(",", ".")
        )
    for pr in engang:
        netto_eng = pr["fv"] * 0.60 if pr["skat_type"] == "A" else pr["fv"]
        L.append(
            f"| {pr['selskab']} – {pr['produkttype']} | {pr['skat_type']}"
            f" | {pr['pv']:,.0f} kr. | {pr['fv']:,.0f} kr."
            f" | — | {netto_eng:,.0f} kr. (engangsbeløb) | — |".replace(",", ".")
        )
    L.append("")

    # Modregnings-info (vis kun hvis tillæg faktisk reduceres)
    first_fp_row = next((r for r in tabel if r["alder"] >= fp_alder), None)
    if first_fp_row:
        max_mdr  = PENSIONSTILLAEG_MAX_ENLIG_AAR / 12
        faktisk  = first_fp_row["tillaeg_mdr"]
        if faktisk < max_mdr * 0.95:
            tabt = max_mdr - faktisk
            L += [
                "### PENSIONSTILLÆG EFTER MODREGNING",
                f"Maks. tillæg (enlig): {max_mdr:,.0f} kr/mdr → Faktisk: {faktisk:,.0f} kr/mdr (tabt: {tabt:,.0f} kr/mdr)".replace(",", "."),
                f"Modregning: {PENSIONSTILLAEG_MODREGNING*100:.1f} % af S-indkomst over "
                f"{PENSIONSTILLAEG_BUNDFRADRAG_ENLIG:,.0f} kr/år (jf. § 29).".replace(",", "."),
                "Aldersopsparing (F-skat) tæller IKKE med i modregningsgrundlaget.",
                "",
            ]

    # ── Tabel 2: fast kolonnestruktur ────────────────────────────────────────
    # Kolonner: Alder | [produkt brutto/mdr] x N | FP brutto/mdr | ATP brutto/mdr | Brutto/år | Netto/mdr | Note
    prod_labels = []
    for pr in loebende:
        kort = pr["produkttype"].replace("pension", "").replace("Pension", "").strip()
        prod_labels.append(f"{pr['selskab']} {kort}".strip()[:18])

    alle_labels = prod_labels + ["Folkepension", "ATP"]
    n_prod_cols = len(alle_labels)

    header = "| Alder | " + " | ".join(f"{l} kr/år" for l in alle_labels) + " | Brutto/år | Netto/mdr | Note |"
    sep    = "|---|" + "|---|" * n_prod_cols + "---|---|---|"

    # Uddrag: første 5 + folkepensions-overgang + sidste 3
    fp_idx  = next((i for i, r in enumerate(tabel) if r["alder"] >= fp_alder), None)
    vis_idx = set(range(min(5, len(tabel))))
    vis_idx |= set(range(max(0, len(tabel) - 3), len(tabel)))
    if fp_idx is not None:
        vis_idx |= {max(0, fp_idx - 1), fp_idx, min(fp_idx + 1, len(tabel) - 1)}
    vis_idx = sorted(vis_idx)

    L += [
        f"### TABEL 2 — ÅR-FOR-ÅR BRUTTO/NETTO (uddrag — LLM rekonstruerer alle {len(tabel)} rækker)",
        f"Kolonner: Alder | {' | '.join(alle_labels)} | Brutto/år | Netto/mdr | Note",
        header, sep,
    ]

    prev = -1
    for i in vis_idx:
        if i > prev + 1:
            L.append("| … | " + " | ".join(["…"] * n_prod_cols) + " | … | … | … |")
        row = tabel[i]
        har_fp = row["alder"] >= fp_alder

        # Brutto/år per produkt
        prod_cols = []
        brutto_aar = 0.0
        for pr in loebende:
            aktiv = pr["stopper_ved_alder"] is None or row["alder"] < pr["stopper_ved_alder"]
            b_aar = pr["mdr_brutto"] * 12 if aktiv else 0.0
            prod_cols.append(f"{b_aar:,.0f}".replace(",", ".") if b_aar else "—")
            brutto_aar += b_aar

        fp_b_aar  = row.get("fp_mdr_brutto", 0.0) * 12
        atp_b_aar = row.get("atp_mdr_brutto", 0.0) * 12

        prod_cols.append(f"{fp_b_aar:,.0f}".replace(",", ".") if fp_b_aar else "—")
        prod_cols.append(f"{atp_b_aar:,.0f}".replace(",", ".") if atp_b_aar else "—")

        brutto_aar += fp_b_aar + atp_b_aar

        # Note
        note_parts = []
        if row["over_topskat"]:
            note_parts.append("Topskat")
        tillaeg_max = PENSIONSTILLAEG_MAX_ENLIG_AAR / 12
        if har_fp and row["tillaeg_mdr"] < tillaeg_max * 0.95:
            tabt = round(tillaeg_max - row["tillaeg_mdr"])
            note_parts.append(f"Modregning -{tabt:,.0f}".replace(",", "."))
        note = " | ".join(note_parts) if note_parts else "—"

        L.append(
            f"| {row['alder']} | " + " | ".join(prod_cols)
            + f" | {brutto_aar:,.0f} | {row['total_netto_mdr']:,.0f} | {note} |".replace(",", ".")
        )
        prev = i

    L += [
        "",
        "INSTRUKTION: Rekonstruér ALLE rækker med samme kolonnestruktur. "
        "Produktkolonner = brutto kr/år. Brutto/år = sum af produktkolonner. "
        "Netto/mdr = samlet månedlig netto efter skat. "
        "Note: 'Topskat' hvis PI > 588.900 kr; 'Modregning -X kr' hvis pensionstillæg reduceres.",
    ]
    return "\n".join(L)
