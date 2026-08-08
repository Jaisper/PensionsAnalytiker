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
    kommuneskat: float = KOMMUNESKAT_DEFAULT
    kirkeskat: float   = KIRKESKAT_DEFAULT
    enlig: bool        = True

    @classmethod
    def fra_pct(
        cls,
        kommuneskat_pct: float = 25.0,
        kirkeskat_pct: float = 0.7,
        enlig: bool = True,
    ) -> "SkatParametre":
        return cls(
            kommuneskat=kommuneskat_pct / 100,
            kirkeskat=kirkeskat_pct / 100,
            enlig=enlig,
        )


# ── Folkepensionsalder ───────────────────────────────────────────────────────

def folkepension_alder(foedselsaar: int) -> int:
    """
    Folkepensionsalder jf. Velfærdsaftalen 2006 og Lov om social pension.
      < 1963 : 67   (fylder 67 før 2030)
      1963–1966 : 68
      1967–1970 : 69
      1971+     : 70 (planlagt fra 2040 — ikke endeligt vedtaget pr. 2025)
    """
    if foedselsaar < 1963:   return 67
    if foedselsaar <= 1966:  return 68
    if foedselsaar <= 1970:  return 69
    return 70


def tidligste_private_pensionsalder(fp_alder: int) -> int:
    return fp_alder - 5


# ── Finansielle kernefunktioner ──────────────────────────────────────────────

def beregn_fv(pv: float, pmt: float, r: float, n: int) -> float:
    """FV = PV·(1+r)^n + PMT·((1+r)^n − 1)/r"""
    if n <= 0:  return max(0.0, pv)
    if r == 0:  return pv + pmt * n
    g = (1 + r) ** n
    return pv * g + pmt * (g - 1) / r


def beregn_maanedlig_annuitet(fv: float, r: float, m: int) -> float:
    """PMT = FV · (r/12) / (1 − (1+r/12)^(−m·12))"""
    if fv <= 0 or m <= 0:  return 0.0
    rm = r / 12
    if rm == 0:  return fv / (m * 12)
    return fv * rm / (1 - (1 + rm) ** (-m * 12))


# ── Skatteberegning ──────────────────────────────────────────────────────────

def _topskat_andel(dette_pi: float, total_pi: float) -> float:
    if total_pi <= TOPSKAT_GRAENSE_PI or total_pi == 0:
        return 0.0
    total_topskat = (total_pi - TOPSKAT_GRAENSE_PI) * TOPSKAT
    return total_topskat * (dette_pi / total_pi)


def _netto_s_med_am(brutto: float, skat: SkatParametre, total_pi: float, dette_pi: float) -> float:
    basis = BUNDSKAT + skat.kommuneskat + skat.kirkeskat
    netto = brutto * (1 - AM_BIDRAG) * (1 - basis)
    netto -= _topskat_andel(dette_pi, total_pi)
    return netto


def _netto_s_uden_am(brutto: float, skat: SkatParametre, total_pi: float, dette_pi: float) -> float:
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
    if brutto_aar <= 0:   return 0.0
    if skat_type == "F":  return brutto_aar
    if skat_type == "A":  return brutto_aar * 0.60
    dette_pi = brutto_aar * (1 - AM_BIDRAG) if har_am_bidrag else brutto_aar
    total_pi = total_s_pi_aar if total_s_pi_aar else dette_pi
    if har_am_bidrag:
        return _netto_s_med_am(brutto_aar, skat, total_pi, dette_pi)
    return _netto_s_uden_am(brutto_aar, skat, total_pi, dette_pi)


# ── Pensionstillæg (modregning) ──────────────────────────────────────────────

def folkepension_pensionstillaeg_aar(privat_s_indkomst_aar: float, skat: SkatParametre) -> float:
    """Pensionstillæg efter modregning. Kilde: Lov om social pension § 29 (enlige)."""
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

    Nye parametre (valgfri):
      inflation_pct (float, default 0.0)       — bruges til realværdi-kolonne
      produkt_start_aldre (dict, default {})   — key → start-alder for loebende produkter
    """
    if skat_params is None:
        skat_params = SkatParametre.fra_pct(
            kommuneskat_pct=float(parametre.get("kommuneskat_pct", 25.0)),
            kirkeskat_pct=float(parametre.get("kirkeskat_pct", 0.7)),
            enlig=bool(parametre.get("enlig", True)),
        )

    pensionsalder  = int(parametre["pensionsalder"])
    udbetaling_aar = int(parametre.get("udbetaling_aar", 30))
    r              = float(parametre.get("afkast_pct", 4.0)) / 100
    netto_indbetal = float(parametre.get("netto_indbetaling", 0))
    inflation_pct  = float(parametre.get("inflation_pct", 0.0)) / 100

    # Per-produkt start-aldre: {key: start_alder} — loebende produkter kan starte sent
    produkt_start_aldre_param: dict[str, int] = parametre.get("produkt_start_aldre", {})

    person   = profil.get("person", {})
    alder_nu = int(person.get("alder") or 0)
    n        = max(0, pensionsalder - alder_nu)

    foedselsaar = _foedselsaar_fra_profil(profil)
    fp_alder    = folkepension_alder(foedselsaar) if foedselsaar else 67

    ordninger         = profil.get("ordninger", [])
    pensionsprodukter = profil.get("pensionsprodukter", [])
    advarsler         = []

    total_opsparing = sum(
        o.get("opsparing") or 0
        for o in ordninger
        if not o.get("kun_forsikring") and "ATP" not in (o.get("selskab") or "")
    )

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

    # ── Pre-compute saldo-fordeling ───────────────────────────────────────────
    from collections import defaultdict

    def _find_ordning(sel: str, nr: str, ptype: str = ""):
        sel_l   = sel.lower()
        ptype_l = ptype.lower()
        if nr:
            for o in ordninger:
                if (str(o.get("aftalenr") or "") == nr
                        and (o.get("selskab") or "").lower() == sel_l):
                    return o
        if ptype_l:
            for o in ordninger:
                if (o.get("selskab") or "").lower() == sel_l:
                    opt = (o.get("produkttype") or "").lower()
                    if ptype_l[:4] and ptype_l[:4] in opt:
                        return o
        for o in ordninger:
            if (o.get("selskab") or "").lower() == sel_l:
                return o
        if nr:
            for o in ordninger:
                if str(o.get("aftalenr") or "") == nr:
                    return o
        return None

    _kendte_saldi:    dict[tuple, float] = defaultdict(float)
    _mangler_saldo:   dict[tuple, int]   = defaultdict(int)
    _kendte_saldi_s:  dict[str, float]   = defaultdict(float)
    _mangler_saldo_s: dict[str, int]     = defaultdict(int)
    for _p in pensionsprodukter:
        _sel = (_p.get("selskab") or "").lower()
        _nr  = str(_p.get("aftalenr") or "")
        _pt  = (_p.get("produkttype") or "").lower()
        if "atp" in _sel or "folkepension" in _pt:
            continue
        _pv = float(_p.get("opsparing") or _p.get("estimated_saldo") or 0)
        _key = (_nr, _sel) if _nr else None
        if _pv:
            if _key:
                _kendte_saldi[_key] += _pv
            _kendte_saldi_s[_sel] += _pv
        else:
            if _key:
                _mangler_saldo[_key] += 1
            _mangler_saldo_s[_sel] += 1

    # ── Byg produktliste ──────────────────────────────────────────────────────
    produkter = []
    _key_tæller: dict[str, int] = defaultdict(int)

    for prod in pensionsprodukter:
        selskab   = prod.get("selskab") or ""
        ptype     = prod.get("produkttype") or ""
        ptype_l   = ptype.lower()
        skat_type = prod.get("skat_type") or "S"
        perioder  = prod.get("aldersperioder") or {}

        if "atp" in selskab.lower() or "atp" in ptype_l or "folkepension" in ptype_l:
            continue

        pv = float(prod.get("opsparing") or prod.get("estimated_saldo") or 0)
        if not pv:
            nr      = str(prod.get("aftalenr") or "")
            ordning = _find_ordning(selskab.lower(), nr, ptype)
            if ordning:
                total   = float(ordning.get("opsparing") or 0)
                ord_nr  = str(ordning.get("aftalenr") or "")
                ord_sel = (ordning.get("selskab") or "").lower()
                nr_key  = (ord_nr, ord_sel) if ord_nr else None
                if nr_key and nr_key in _mangler_saldo:
                    kendte = _kendte_saldi.get(nr_key, 0.0)
                    count  = max(1, _mangler_saldo[nr_key])
                else:
                    kendte = _kendte_saldi_s.get(ord_sel, 0.0)
                    count  = max(1, _mangler_saldo_s.get(ord_sel, 1))
                rest = max(0.0, total - kendte)
                pv   = rest / count

        pmt = float(prod.get("estimated_pmt") or 0)
        if not pmt and "kapital" not in ptype_l and total_opsparing > 0 and pv > 0:
            pmt = netto_indbetal * (pv / total_opsparing)
        if "rate" in ptype_l:
            pmt = min(pmt, RATEPENSION_LOFT)

        # ── Unik key beregnes FØR FV så start-alder kan slås op ──
        base_key = f"{selskab}_{ptype}"
        _key_tæller[base_key] += 1
        key = base_key if _key_tæller[base_key] == 1 else f"{base_key}_{_key_tæller[base_key]}"

        # Per-produkt start-alder (loebende produkter kan udskydes)
        if "kapital" in ptype_l or "aldersopsparing" in ptype_l:
            # Engangsbeloeb starter altid ved pensionsalder
            produkt_start_alder = pensionsalder
        else:
            produkt_start_alder = max(
                pensionsalder,
                int(produkt_start_aldre_param.get(key, pensionsalder))
            )

        n_i = max(0, produkt_start_alder - alder_nu)
        fv  = beregn_fv(pv, pmt, r, n_i)

        # Udbetalingstype
        if "kapital" in ptype_l or "aldersopsparing" in ptype_l:
            udb_type, udb_aar  = "engangsbeloeb", 0
            mdr_brutto, stopper = 0.0, produkt_start_alder
        elif "livsvarig" in ptype_l or "livrente" in ptype_l:
            udb_aar    = LIVSVARIG_ESTIMAT_AAR
            udb_type   = "livsvarig"
            mdr_brutto = beregn_maanedlig_annuitet(fv, r, udb_aar)
            stopper    = None
        else:   # ratepension
            udb_aar    = _udled_udbetalingsaar(perioder) or 15
            udb_type   = "rate"
            mdr_brutto = beregn_maanedlig_annuitet(fv, r, udb_aar)
            stopper    = produkt_start_alder + udb_aar

        produkter.append({
            "selskab": selskab, "produkttype": ptype, "skat_type": skat_type,
            "pv": pv, "pmt": pmt, "fv": fv,
            "udb_type": udb_type, "udb_aar": udb_aar,
            "mdr_brutto": mdr_brutto, "stopper_ved_alder": stopper,
            "start_alder": produkt_start_alder,
            "key": key,
        })

    engangsbeloeb = [p for p in produkter if p["udb_type"] == "engangsbeloeb"]
    loebende      = [p for p in produkter if p["udb_type"] != "engangsbeloeb"]

    atp_prod = next(
        (p for p in pensionsprodukter if "atp" in (p.get("selskab") or "").lower()), None
    )
    if atp_prod:
        per     = atp_prod.get("aldersperioder") or {}
        first_v = next((v for v in per.values() if v), None)
        atp_mdr = round(first_v / 12) if first_v else ATP_MDR_STANDARD
    else:
        atp_mdr = ATP_MDR_STANDARD

    engangs_netto_total = sum(
        pr["fv"] * 0.60 if pr["skat_type"] == "A" else pr["fv"]
        for pr in engangsbeloeb
    )

    # ── År-for-år tabel ───────────────────────────────────────────────────────
    tabel = []
    for alder in range(pensionsalder, pensionsalder + udbetaling_aar + 1):
        har_fp = alder >= fp_alder

        # S-indkomst dette år — kun aktive produkter
        privat_s_brutto = sum(
            p["mdr_brutto"] * 12
            for p in loebende
            if p["skat_type"] == "S"
            and alder >= p["start_alder"]
            and (p["stopper_ved_alder"] is None or alder < p["stopper_ved_alder"])
        )

        pi_med_am  = privat_s_brutto * (1 - AM_BIDRAG)
        pi_uden_am = (FOLKEPENSION_MDR * 12 + atp_mdr * 12) if har_fp else 0.0
        total_pi   = pi_med_am + pi_uden_am

        produkt_data: dict[str, dict] = {}
        for p in loebende:
            aktiv = (alder >= p["start_alder"] and
                     (p["stopper_ved_alder"] is None or alder < p["stopper_ved_alder"]))
            if not aktiv:
                produkt_data[p["key"]] = {"mdr_brutto": 0.0, "mdr_netto": 0.0, "skat_type": p["skat_type"]}
                continue
            b_aar = p["mdr_brutto"] * 12
            if p["skat_type"] == "S":
                dette_pi  = b_aar * (1 - AM_BIDRAG)
                netto_aar = _netto_s_med_am(b_aar, skat_params, total_pi, dette_pi)
            elif p["skat_type"] == "F":
                netto_aar = b_aar
            else:
                netto_aar = b_aar * 0.60
            produkt_data[p["key"]] = {
                "mdr_brutto": p["mdr_brutto"],
                "mdr_netto":  netto_aar / 12,
                "skat_type":  p["skat_type"],
            }

        if har_fp:
            fp_netto_aar  = _netto_s_uden_am(FOLKEPENSION_MDR * 12, skat_params, total_pi, FOLKEPENSION_MDR * 12)
            atp_netto_aar = _netto_s_uden_am(float(atp_mdr * 12), skat_params, total_pi, float(atp_mdr * 12))
            fp_mdr_netto  = fp_netto_aar / 12
            atp_mdr_netto = atp_netto_aar / 12
            tillaeg_aar   = folkepension_pensionstillaeg_aar(privat_s_brutto, skat_params)
            tillaeg_mdr   = tillaeg_aar / 12
        else:
            fp_mdr_netto = atp_mdr_netto = tillaeg_mdr = 0.0

        total_netto_mdr = (
            sum(d["mdr_netto"] for d in produkt_data.values())
            + fp_mdr_netto + tillaeg_mdr + atp_mdr_netto
        )

        # Realværdi: deflatér med inflation siden i dag
        years_from_now = max(0, alder - alder_nu) if alder_nu else (alder - pensionsalder)
        real_deflator   = (1 + inflation_pct) ** years_from_now if inflation_pct > 0 else 1.0
        real_netto_mdr  = round(total_netto_mdr / real_deflator) if inflation_pct > 0 else None

        over_topskat = total_pi > TOPSKAT_GRAENSE_PI
        if over_topskat:
            msg = f"Alder {alder}: bruttoudbetalingen overstiger topskattegrænsen — effektiv marginalbeskatning over 52 %"
            if msg not in advarsler:
                advarsler.append(msg)

        # Engangsbeloeb udbetales i det år produktet starter
        engangs_dette_aar = sum(
            pr["fv"] * 0.60 if pr["skat_type"] == "A" else pr["fv"]
            for pr in engangsbeloeb
            if alder == pr["start_alder"]
        )

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
            "real_netto_mdr":  real_netto_mdr,
            "engangs_netto":   engangs_dette_aar,
            "over_topskat":    over_topskat,
        })

    # ── Jævn fordeling ───────────────────────────────────────────────────────
    BUFFER_SKAT = 0.40
    r_buffer = r * (1 - BUFFER_SKAT)

    n_aar = max(1, len(tabel))
    pv_normal      = sum(row["total_netto_aar"] / (1 + r_buffer) ** (i + 1)
                         for i, row in enumerate(tabel))
    annuitet_faktor = sum(1 / (1 + r_buffer) ** (i + 1) for i in range(n_aar))
    jaevn_netto_mdr = (engangs_netto_total + pv_normal) / annuitet_faktor / 12

    jaevn_tabel = []
    buffer = engangs_netto_total
    for row in tabel:
        buffer      *= (1 + r_buffer)
        normal_mdr   = row["total_netto_mdr"]
        fra_buffer   = jaevn_netto_mdr - normal_mdr
        buffer      -= fra_buffer * 12
        jaevn_tabel.append({
            "alder":       row["alder"],
            "normal_mdr":  round(normal_mdr),
            "jaevn_mdr":   round(jaevn_netto_mdr),
            "fra_buffer":  round(fra_buffer),
            "buffer_rest": round(max(0.0, buffer)),
        })

    return {
        "produkter":       produkter,
        "engangsbeloeb":   engangsbeloeb,
        "tabel":           tabel,
        "jaevn_tabel":     jaevn_tabel,
        "jaevn_netto_mdr": round(jaevn_netto_mdr),
        "fp_alder":        fp_alder,
        "pensionsalder":   pensionsalder,
        "advarsler":       advarsler,
        "parametre": {
            "r":               r,
            "n":               n,
            "pensionsalder":   pensionsalder,
            "udbetaling_aar":  udbetaling_aar,
            "fp_alder":        fp_alder,
            "kommuneskat_pct": skat_params.kommuneskat * 100,
            "kirkeskat_pct":   skat_params.kirkeskat * 100,
            "enlig":           skat_params.enlig,
            "inflation_pct":   inflation_pct * 100,
        },
    }


# ── Scenarieanalyse ───────────────────────────────────────────────────────────

def generer_scenarier(profil: dict, parametre: dict, skat_params: Optional[SkatParametre]) -> list[dict]:
    """
    Kør engine 3× med lav/midt/høj afkast.
    Returnerer liste af scenarie-dicts til Tabel 4.
    """
    import copy
    r_base = float(parametre.get("afkast_pct", 4.0))
    r_low  = max(1.0, r_base - 2.0)
    r_high = r_base + 2.0

    results = []
    for label, r_val in [("Pessimistisk", r_low), ("Base", r_base), ("Optimistisk", r_high)]:
        p_copy = {**parametre, "afkast_pct": r_val}
        try:
            res = generer_udbetalingstabel(copy.deepcopy(profil), p_copy, skat_params)
            fp_a   = res["fp_alder"]
            tbl    = res["tabel"]
            first  = tbl[0] if tbl else None
            fp_row = next((row for row in tbl if row["alder"] >= fp_a), None)
            results.append({
                "label":      label,
                "r":          r_val,
                "start_mdr":  round(first["total_netto_mdr"]) if first else 0,
                "fp_mdr":     round(fp_row["total_netto_mdr"]) if fp_row else 0,
                "jaevn_mdr":  res.get("jaevn_netto_mdr", 0),
            })
        except Exception:
            pass

    return results


# ── Indbetalingsfordeling ─────────────────────────────────────────────────────

_PRODUKTTYPE_SORT = [("rate", 0), ("livsvarig", 1), ("livrente", 1), ("aldersopsparing", 2)]

def _sort_produkttype(ptype: str) -> int:
    pt = (ptype or "").lower()
    for key, order in _PRODUKTTYPE_SORT:
        if key in pt:
            return order
    return 99


def fordel_pmt_default(profil: dict, netto_indbetaling: float) -> list[dict]:
    """
    Beregner default PMT-fordeling for multi-produkt firmapensioner.
    Fordelingsregel: Rate til loftet → Livsvarig → Aldersopsparing: 0
    """
    from collections import defaultdict

    ordninger         = profil.get("ordninger", [])
    pensionsprodukter = profil.get("pensionsprodukter", [])

    by_key: dict[tuple, list] = defaultdict(list)
    for prod in pensionsprodukter:
        nr  = str(prod.get("aftalenr") or "")
        sel = (prod.get("selskab") or "").lower()
        pt  = (prod.get("produkttype") or "").lower()
        if not nr or "atp" in sel or "folkepension" in pt:
            continue
        by_key[(nr, sel)].append(prod)

    multi_aftaler = {key: prods for key, prods in by_key.items() if len(prods) >= 2}
    if not multi_aftaler:
        return []

    bruger_beloeb = float(netto_indbetaling or 0)
    if bruger_beloeb > 0:
        if len(multi_aftaler) == 1:
            key = next(iter(multi_aftaler))
            pmt_by_key = {key: bruger_beloeb}
        else:
            saldo_by_key = {
                key: sum(float(p.get("opsparing") or p.get("estimated_saldo") or 0) for p in prods)
                for key, prods in multi_aftaler.items()
            }
            total_saldo = sum(saldo_by_key.values())
            pmt_by_key = {
                key: bruger_beloeb * (saldo_by_key[key] / total_saldo) if total_saldo > 0
                    else bruger_beloeb / len(multi_aftaler)
                for key in multi_aftaler
            }
    else:
        pmt_by_key = {
            (nr, sel): next(
                (float(o["aarlig_indbetaling"]) for o in ordninger
                 if str(o.get("aftalenr") or "") == nr
                 and (o.get("selskab") or "").lower() == sel
                 and o.get("aarlig_indbetaling")),
                0.0,
            )
            for (nr, sel) in multi_aftaler
        }

    resultater = []
    for key, prods in multi_aftaler.items():
        nr, sel = key
        samlet_pmt = pmt_by_key.get(key, 0.0)
        sorted_prods = sorted(prods, key=lambda p: _sort_produkttype(p.get("produkttype", "")))
        resterende = samlet_pmt
        for prod in sorted_prods:
            pt = (prod.get("produkttype") or "").lower()
            if "rate" in pt:
                allokeret = min(resterende, float(RATEPENSION_LOFT))
            elif "livsvarig" in pt or "livrente" in pt:
                allokeret = resterende
            else:
                allokeret = 0.0
            resterende = max(0.0, resterende - allokeret)
            resultater.append({
                "selskab":     prod.get("selskab", ""),
                "aftalenr":    nr,
                "produkttype": prod.get("produkttype", ""),
                "default_pmt": int(round(allokeret)),
                "opsparing":   prod.get("opsparing") or prod.get("estimated_saldo"),
            })

    return resultater


def format_fordeling_til_llm(fordeling: list[dict]) -> str:
    if not fordeling:
        return ""

    from collections import defaultdict
    by_nr: dict[str, list] = defaultdict(list)
    for item in fordeling:
        by_nr[item["aftalenr"]].append(item)

    linjer = []
    for nr, items in by_nr.items():
        selskab = items[0]["selskab"]
        samlet  = sum(i["default_pmt"] for i in items)
        linjer.append(f"**{selskab}** (aftale {nr}) — samlet indbetaling: {samlet:,.0f} kr/år".replace(",", "."))
        for item in sorted(items, key=lambda i: _sort_produkttype(i.get("produkttype", ""))):
            opsp_s = f"{item['opsparing']:,.0f} kr.".replace(",", ".") if item["opsparing"] else "ukendt"
            linjer.append(
                f"  – {item['produkttype']}: saldo {opsp_s} | "
                f"default indbetaling {item['default_pmt']:,.0f} kr/år".replace(",", ".")
            )
        linjer.append("")
    return "\n".join(linjer)


# ── Formatering til LLM-kontekst ─────────────────────────────────────────────

def _format_skatteeksempel(row: dict, loebende: list, parametre: dict, fp_alder: int) -> str:
    alder     = row["alder"]
    har_fp    = alder >= fp_alder
    kom_pct   = parametre.get("kommuneskat_pct", 25.0)
    basis_pct = (BUNDSKAT + kom_pct / 100 + KIRKESKAT_DEFAULT) * 100

    def kr(v: float) -> str:
        return f"{v:,.0f}".replace(",", ".")

    s_produkter = []
    f_produkter = []
    samlet_brutto = 0.0
    for pr in loebende:
        aktiv = (alder >= pr["start_alder"] and
                 (pr["stopper_ved_alder"] is None or alder < pr["stopper_ved_alder"]))
        if not aktiv:
            continue
        b_aar = pr["mdr_brutto"] * 12
        samlet_brutto += b_aar
        label = f"{pr['selskab']} – {pr['produkttype']}"
        if pr["skat_type"] == "S":
            s_produkter.append((label, b_aar))
        elif pr["skat_type"] == "F":
            f_produkter.append((label, b_aar))

    fp_b  = row.get("fp_mdr_brutto", 0.0) * 12 if har_fp else 0.0
    atp_b = row.get("atp_mdr_brutto", 0.0) * 12 if har_fp else 0.0
    if har_fp:
        samlet_brutto += fp_b + atp_b

    s_brutto_med_am = sum(b for _, b in s_produkter)
    am_bidrag       = s_brutto_med_am * AM_BIDRAG
    pi_med_am       = s_brutto_med_am * (1 - AM_BIDRAG)
    pi_uden_am      = fp_b + atp_b
    total_pi        = pi_med_am + pi_uden_am
    basis_skat      = total_pi * (BUNDSKAT + kom_pct / 100 + KIRKESKAT_DEFAULT)

    topskat = 0.0
    over_top = max(0.0, total_pi - TOPSKAT_GRAENSE_PI)
    if over_top:
        topskat = over_top * TOPSKAT

    netto_mdr = row["total_netto_mdr"]

    rows = [
        f"### SKATTEBEREGNING — EKSEMPEL ÅR 1 (ALDER {alder})",
        "",
        "| Post | Beregning | kr/år |",
        "|---|---|---:|",
    ]

    def add(post, beregning, beloeb, prefix=""):
        rows.append(f"| {prefix}{post} | {beregning} | {prefix}{kr(beloeb)} |")

    for label, b in s_produkter:
        add(label, "S-indkomst med AM-bidrag", b)
    for label, b in f_produkter:
        add(label, "Skattefri (F)", b)
    if fp_b:
        add("Folkepension", "S-indkomst, ingen AM-bidrag", fp_b)
    if atp_b:
        add("ATP", "S-indkomst, ingen AM-bidrag", atp_b)
    rows.append(f"| **Brutto i alt** | | **{kr(samlet_brutto)}** |")
    rows.append("|---|---|---:|")

    add("AM-bidrag 8%", f"{kr(s_brutto_med_am)} × 8%", am_bidrag, "− ")
    rows.append(f"| **Personlig indkomst (PI)** | | **{kr(total_pi)}** |")
    rows.append("|---|---|---:|")

    add(f"Bundskat + kommuneskat + kirkeskat", f"{kr(total_pi)} × {basis_pct:.2f}%", basis_skat, "− ")
    if topskat:
        add(f"Topskat 15% (PI over {kr(TOPSKAT_GRAENSE_PI)} kr)", f"{kr(over_top)} × 15%", topskat, "− ")
    else:
        rows.append(f"| Topskat | PI under grænsen ({kr(TOPSKAT_GRAENSE_PI)} kr) | — |")
    rows.append("|---|---|---:|")

    rows.append(f"| **Netto/år** | | **{kr(netto_mdr * 12)}** |")
    rows.append(f"| **Netto/mdr** | | **{kr(netto_mdr)}** |")
    rows.append("")
    rows.append("*(Lille afvigelse mulig: engine fordeler topskat forholdsmæssigt per produkt)*")

    return "\n".join(rows)


def format_engine_til_llm(result: dict) -> str:
    """
    Konverterer engine-output til tekst der injiceres som 'Hard Facts' i system-prompt.
    """
    p            = result["parametre"]
    loebende     = [pr for pr in result["produkter"] if pr["udb_type"] != "engangsbeloeb"]
    engang       = result["engangsbeloeb"]
    tabel        = result["tabel"]
    jaevn_tabel  = result.get("jaevn_tabel", [])
    fp_alder     = result["fp_alder"]
    advarsler    = result.get("advarsler", [])
    inflation_pct = p.get("inflation_pct", 0.0)
    scenarier    = result.get("scenarier", [])

    # Per-produkt start-aldre — noter hvis afviger fra pensionsalder
    start_noter = []
    for pr in loebende:
        if pr.get("start_alder", p["pensionsalder"]) != p["pensionsalder"]:
            start_noter.append(
                f"  {pr['selskab']} – {pr['produkttype']}: starter ved {pr['start_alder']} år"
            )

    def kr(v: float) -> str:
        return f"{v:,.0f}".replace(",", ".")

    L = [
        "## BEREGNET PENSIONSANALYSE — HARD FACTS",
        "*(Deterministisk engine — præsenter disse tal direkte, beregn aldrig selv)*",
        "",
        (
            f"Pensionsalder: {p['pensionsalder']} år  |  Folkepensionsalder: {fp_alder} år  |  "
            f"Afkast: {p['r']*100:.1f}% p.a.  |  Kommuneskat: {p['kommuneskat_pct']:.1f}%  |  "
            f"Kirkeskat: {p['kirkeskat_pct']:.1f}%  |  "
            f"Udbetalingsperiode: {p['udbetaling_aar']} år  |  "
            f"{'Enlig' if p['enlig'] else 'Par'}"
            + (f"  |  Inflation: {inflation_pct:.1f}%" if inflation_pct > 0 else "")
        ),
        "",
    ]

    if start_noter:
        L.append("### PER-PRODUKT STARTALDRE (afviger fra pensionsalder)")
        L += start_noter
        L.append("")

    if advarsler:
        L.append("### ADVARSLER")
        L += [f"- {a}" for a in advarsler]
        L.append("")

    # Tabel 1
    L += [
        "### TABEL 1 — FREMTIDSVÆRDI OG UDBETALING",
        "| Ordning | Skat | Start | Saldo nu | FV ved start | Brutto/mdr | Brutto/år | Netto/mdr | Varighed |",
        "|---|---|---|---|---|---|---|---|---|",
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
            f" | {pr['start_alder']} år"
            f" | {pr['pv']:,.0f} kr. | {pr['fv']:,.0f} kr."
            f" | {pr['mdr_brutto']:,.0f} kr/mdr | {pr['mdr_brutto']*12:,.0f} kr/år"
            f" | {n_mdr:,.0f} kr/mdr | {varighed} |".replace(",", ".")
        )
    for pr in engang:
        netto_eng = pr["fv"] * 0.60 if pr["skat_type"] == "A" else pr["fv"]
        L.append(
            f"| {pr['selskab']} – {pr['produkttype']} | {pr['skat_type']}"
            f" | {pr['start_alder']} år"
            f" | {pr['pv']:,.0f} kr. | {pr['fv']:,.0f} kr."
            f" | — | {netto_eng:,.0f} kr. (engangsbeløb) | — |".replace(",", ".")
        )
    L.append("")

    # Pensionstillæg modregning
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

    # Tabel 2
    prod_labels = []
    for pr in loebende:
        kort = pr["produkttype"].replace("pension", "").replace("Pension", "").strip()
        prod_labels.append(f"{pr['selskab']} {kort}".strip()[:18])

    alle_labels = prod_labels + ["Folkepension", "ATP"]
    n_prod_cols = len(alle_labels)
    has_real    = inflation_pct > 0

    header_cols = " | ".join(f"{l} kr/år" for l in alle_labels)
    real_col    = " | Real/mdr" if has_real else ""
    header = f"| Alder | {header_cols} | Brutto/år | Netto/mdr{real_col} | Note |"
    sep    = "|---|" + "|---|" * n_prod_cols + "---|---" + ("|---" if has_real else "") + "|---|"

    fp_idx  = next((i for i, r in enumerate(tabel) if r["alder"] >= fp_alder), None)
    vis_idx = set(range(min(5, len(tabel))))
    vis_idx |= set(range(max(0, len(tabel) - 3), len(tabel)))
    if fp_idx is not None:
        vis_idx |= {max(0, fp_idx - 1), fp_idx, min(fp_idx + 1, len(tabel) - 1)}
    start_alder = tabel[0]["alder"] if tabel else 0
    for pr in loebende:
        if pr.get("start_alder", p["pensionsalder"]) != p["pensionsalder"]:
            stop_i = pr["start_alder"] - start_alder
            vis_idx |= {max(0, stop_i - 1), min(stop_i, len(tabel) - 1)}
        if pr["stopper_ved_alder"] is not None:
            stop_i = pr["stopper_ved_alder"] - start_alder
            vis_idx |= {max(0, stop_i - 1), min(stop_i, len(tabel) - 1)}
    vis_idx = sorted(vis_idx)

    L += [
        f"### TABEL 2 — ÅR-FOR-ÅR BRUTTO/NETTO (uddrag — LLM rekonstruerer alle {len(tabel)} rækker)",
        f"Kolonner: Alder | {' | '.join(alle_labels)} | Brutto/år | Netto/mdr"
        + (" | Real/mdr" if has_real else "") + " | Note",
        header, sep,
    ]

    prev = -1
    for i in vis_idx:
        if i > prev + 1:
            L.append("| … | " + " | ".join(["…"] * n_prod_cols) + " | … | …" + (" | …" if has_real else "") + " | … |")
        row = tabel[i]
        har_fp = row["alder"] >= fp_alder

        prod_cols = []
        brutto_aar = 0.0
        for pr in loebende:
            aktiv = (row["alder"] >= pr["start_alder"] and
                     (pr["stopper_ved_alder"] is None or row["alder"] < pr["stopper_ved_alder"]))
            b_aar = pr["mdr_brutto"] * 12 if aktiv else 0.0
            prod_cols.append(f"{b_aar:,.0f}".replace(",", ".") if b_aar else "—")
            brutto_aar += b_aar

        fp_b_aar  = row.get("fp_mdr_brutto", 0.0) * 12
        atp_b_aar = row.get("atp_mdr_brutto", 0.0) * 12
        prod_cols.append(f"{fp_b_aar:,.0f}".replace(",", ".") if fp_b_aar else "—")
        prod_cols.append(f"{atp_b_aar:,.0f}".replace(",", ".") if atp_b_aar else "—")
        brutto_aar += fp_b_aar + atp_b_aar

        note_parts = []
        engangs_dette = row.get("engangs_netto", 0.0)
        if engangs_dette:
            note_parts.append(f"Engangsbeløb: +{engangs_dette:,.0f} kr.".replace(",", "."))
        if row["over_topskat"]:
            note_parts.append("Topskat")
        tillaeg_max = PENSIONSTILLAEG_MAX_ENLIG_AAR / 12
        if har_fp and row["tillaeg_mdr"] < tillaeg_max * 0.95:
            tabt = round(tillaeg_max - row["tillaeg_mdr"])
            note_parts.append(f"Modregning -{tabt:,.0f} kr/mdr".replace(",", "."))
        note = "; ".join(note_parts) if note_parts else "—"

        real_col_val = f" | {row['real_netto_mdr']:,.0f}".replace(",", ".") if has_real and row.get("real_netto_mdr") is not None else (" | —" if has_real else "")

        L.append(
            f"| {row['alder']} | " + " | ".join(prod_cols)
            + f" | {brutto_aar:,.0f} | {row['total_netto_mdr']:,.0f}".replace(",", ".")
            + real_col_val
            + f" | {note} |"
        )
        prev = i

    if has_real:
        L.append(f"*Real/mdr: 2025-købekraft ved {inflation_pct:.1f}% p.a. inflation*")

    # Skatteeksempel år 1
    if tabel:
        L += ["", _format_skatteeksempel(tabel[0], loebende, result["parametre"], fp_alder)]

    # Tabel 3 — Jævn fordeling
    jaevn_mdr = result.get("jaevn_netto_mdr", 0)
    engangs_total = sum(pr["fv"] * 0.60 if pr["skat_type"] == "A" else pr["fv"] for pr in engang)
    r_buf_pct = p["r"] * (1 - 0.40) * 100
    L += [
        "",
        f"### TABEL 3 — JÆVN FORDELING NETTO",
        f"Jævn netto/mdr over hele perioden: **{jaevn_mdr:,.0f} kr/mdr**".replace(",", "."),
        (f"Engangsbeløb netto ({engangs_total:,.0f} kr) bruges som buffer fra år 1. "
         f"Buffer forrentes med {r_buf_pct:.1f}% p.a. efter 40% kapitalindkomstskat.").replace(",", ".") if engangs_total else "",
        "| Alder | Normal netto/mdr | Jævn netto/mdr | Fra buffer/mdr | Buffer rest |",
        "|---|---|---|---|---|",
    ]
    for row in jaevn_tabel:
        fra_b = row["fra_buffer"]
        L.append(
            f"| {row['alder']} "
            f"| {row['normal_mdr']:,.0f} "
            f"| {row['jaevn_mdr']:,.0f} "
            f"| {fra_b:+,.0f} "
            f"| {row['buffer_rest']:,.0f} |".replace(",", ".")
        )

    L += [
        "",
        "INSTRUKTION: Kopiér Tabel 3 direkte — MÅ IKKE rekonstruere eller beregne buffer selv.",
    ]

    # Tabel 4 — Scenarieanalyse
    if scenarier:
        L += [
            "",
            "### TABEL 4 — SCENARIEANALYSE (pessimistisk / base / optimistisk afkast)",
            "| Scenarie | Afkast | Netto/mdr ved pensionsstart | Netto/mdr ved folkepension | Jævn netto/mdr |",
            "|---|---|---|---|---|",
        ]
        for s in scenarier:
            L.append(
                f"| {s['label']} | {s['r']:.1f}%"
                f" | {s['start_mdr']:,.0f} kr/mdr"
                f" | {s['fp_mdr']:,.0f} kr/mdr"
                f" | {s['jaevn_mdr']:,.0f} kr/mdr |".replace(",", ".")
            )
        L.append("*(Base-scenariet svarer til Tabel 1–3 ovenfor)*")

    return "\n".join(L)
