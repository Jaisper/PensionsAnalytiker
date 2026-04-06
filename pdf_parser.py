"""
Parser til PensionsInfo PDF-rapport.
Bruger pdfplumber til tekst-udtræk og Claude Sonnet til struktureret JSON-ekstraktion.
"""

import re
import os
import json
import warnings
import pdfplumber
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
warnings.filterwarnings("ignore")

# ── Simpelt, fokuseret ekstrakt-prompt ───────────────────────────────────────

EXTRACTION_PROMPT = """Extract data from this Danish PensionsInfo PDF report. Return ONLY valid JSON, no prose.

RULES:
- Amounts: integers in DKK, no separators (e.g. 771000 not "771.000 kr.")
- Missing value: null (never 0 unless explicitly stated as 0)
- Do not invent numbers

Return exactly this structure:

{
  "name": "full name of the insured person",
  "cpr": "CPR-number as shown on the front page, e.g. '010165-1234' or '0101651234' — digits 5+6 are birth year",
  "agreements": [
    {
      "provider": "company name",
      "number": "agreement number",
      "type": "product type in Danish (e.g. Ratepension, Kapitalpension, Livsvarig pension, Markedsrente)",
      "balance": null,
      "annual_contribution": null,
      "insurance_only": false
    }
  ],
  "death_cover": null,
  "disability_annual": null,
  "critical_illness": null,
  "has_health_insurance": false,
  "earliest_retirement_age": null,
  "payout_products": [
    {
      "provider": "company name",
      "number": "agreement number",
      "type": "product label",
      "tax": "A or S or F",
      "amounts": {"period label": integer_annual_amount}
    }
  ]
}

FIELD GUIDANCE:

agreements[].balance
  Look in "Nuværende pensionsopsparinger" section — this lists each agreement with its INDIVIDUAL balance.
  Also check under each agreement block in "Dine aftaler" for "Saldo" or "Opsparing pr.".
  If the report shows "Del af X kr" (part of total) under "Dine aftaler", that is the TOTAL, not the individual balance —
  instead use the value from "Nuværende pensionsopsparinger" for that specific agreement/provider.
  Set null if no individual balance found.

agreements[].annual_contribution
  Look under each agreement block in "Dine aftaler" for "Indbetaling i alt", "Bidrag i alt", or "Præmie i alt".
  This is the TOTAL annual amount paid to this specific agreement.
  If shown monthly, multiply by 12. Set null if not stated for this agreement.

agreements[].insurance_only
  Set true if the agreement says "Kun forsikring" or has no savings.

death_cover
  Total death/life cover lump sum from "Liv og erstatning" or front page summary.

disability_annual
  Total annual disability/loss-of-earning-capacity from "Tab af erhvervsevne" section.

critical_illness
  Total critical illness lump sum from "Kritisk sygdom" section.

has_health_insurance
  true if any health insurance policy is mentioned.

earliest_retirement_age
  The lowest retirement age shown in "Hvis du går på pension som X-årig" scenarios.

payout_products
  From the EARLIEST retirement age scenario ONLY.
  Each product line with amounts per time period (e.g. "60-67 år", "Fra 67 år", "Fra 68 år", etc.).
  amounts keys = the time band labels exactly as shown.
  amounts values = ANNUAL amounts (not monthly).
  Include ATP and Folkepension if shown.

  current_balance: the current savings balance for THIS specific product line, if shown separately
  in "Nuværende pensionsopsparinger" or "Dine aftaler". Set null if not individually stated.
  When a provider has multiple products (e.g. Aldersopsparing + Ratepension + Livsvarig pension),
  each may have its own balance listed — extract each separately.
"""


def parse_pensionsinfo_pdf(pdf_path: str) -> dict:
    """Udtrækker og strukturerer data fra en PensionsInfo PDF."""
    pages = _extract_pages(pdf_path)
    relevant = _filter_relevant_pages(pages)
    raw = _extract_with_llm(relevant)
    full_text = "\n\n".join(f"=== SIDE {i+1} ===\n{p}" for i, p in enumerate(pages) if p)
    return _to_legacy_format(raw, full_text)


def _extract_pages(pdf_path: str) -> list[str]:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


def _filter_relevant_pages(pages: list[str]) -> str:
    """Beholder kun relevante sider — reducerer token-forbrug markant."""
    RELEVANT_KEYWORDS = [
        "Pensionsoplysninger for",
        "Nuværende pensionsopsparinger",
        "Dine aftaler",
        "Liv og erstatning",
        "Tab af erhvervsevne",
        "Kritisk sygdom",
        "Sundhedsforsikring",
        "Øvrige forsikringer",
    ]
    # Find tidligste pensionsalder
    earliest_age = None
    for page in pages:
        m = re.search(r"Hvis du går på pension som (\d+)-årig", page)
        if m:
            age = int(m.group(1))
            if earliest_age is None or age < earliest_age:
                earliest_age = age

    kept = []
    for i, page in enumerate(pages):
        if any(kw in page for kw in RELEVANT_KEYWORDS):
            kept.append(f"=== SIDE {i+1} ===\n{page}")
        elif earliest_age and f"som {earliest_age}-årig" in page:
            kept.append(f"=== SIDE {i+1} ===\n{page}")
    return "\n\n".join(kept)


def _extract_with_llm(text: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{
            "role": "user",
            "content": f"{EXTRACTION_PROMPT}\n\nREPORT TEXT:\n{text}",
        }],
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def _fv_fra_payout(ptype: str, aldersperioder: dict, r: float = 0.04) -> float | None:
    """Estimér FV ved at 'fortryde' annuiteten fra payout-beløbet (første periode)."""
    if not aldersperioder:
        return None
    first = next((v for v in aldersperioder.values() if v), None)
    if not first:
        return None
    pt = (ptype or "").lower()
    if "kapitalpension" in pt or "aldersopsparing" in pt:
        return float(first)          # engangsbeløb ≈ FV direkte
    elif "livsvarig" in pt or "livrente" in pt:
        m = 25
    else:                            # ratepension — find antal år fra periodenøgler
        m = 0
        for k in aldersperioder:
            if re.match(r"^\d+ år$", k):
                m += 1
            elif mm := re.match(r"^(\d+)-(\d+) år$", k):
                m += int(mm.group(2)) - int(mm.group(1)) + 1
        if not m:
            m = 15
    if r == 0:
        return float(first) * m
    return float(first) * (1 - (1 + r) ** (-m)) / r


def _estimer_pmt_fordeling(
    pensionsprodukter: list,
    ordninger: list,
    alder: int | None,
    tidligste: int | None,
    r: float = 0.04,
) -> None:
    """
    For providers med flere produkter under samme aftalenr:
    back-beregn estimeret FV per produkt fra payout-beløbene,
    og udled deraf en estimeret PMT-fordeling (kr/år).
    Resultater gemmes direkte på hvert produkt-dict.
    """
    from collections import defaultdict
    by_nr: dict[str, list] = defaultdict(list)
    for prod in pensionsprodukter:
        nr = prod.get("aftalenr") or ""
        if nr:
            by_nr[nr].append(prod)

    for nr, prods in by_nr.items():
        if len(prods) < 2:
            continue   # kun relevant for multi-produkt aftaler

        # Find samlet PMT for denne aftale fra ordninger
        samlet_pmt = next(
            (o["aarlig_indbetaling"] for o in ordninger
             if str(o.get("aftalenr") or "") == nr and o.get("aarlig_indbetaling")),
            None,
        )
        if not samlet_pmt:
            continue

        # Estimér FV per produkt (fra payout-beløb)
        fv_list = []
        for prod in prods:
            fv = _fv_fra_payout(prod["produkttype"], prod.get("aldersperioder") or {}, r)
            fv_list.append(fv)

        total_fv = sum(f for f in fv_list if f)
        if not total_fv:
            continue

        # Fordel PMT proportionalt med FV
        n = (tidligste - alder) if (tidligste and alder and tidligste > alder) else None
        pmt_per_prod = []
        for prod, fv in zip(prods, fv_list):
            if fv is None:
                prod["estimated_pmt"] = None
                pmt_per_prod.append(0.0)
                continue
            share = fv / total_fv
            pmt_i = round(samlet_pmt * share)
            prod["estimated_pmt"] = pmt_i
            prod["estimated_fv_rapport"] = int(fv)
            pmt_per_prod.append(float(pmt_i))

        # Back-beregn PV per produkt: PV = (FV - PMT×((1+r)^n-1)/r) / (1+r)^n
        # Normaliser derefter så sum(PV_i) = faktisk total saldo
        samlet_opsparing = next(
            (o["opsparing"] for o in ordninger
             if str(o.get("aftalenr") or "") == nr and o.get("opsparing")),
            None,
        )
        if n and n > 0 and samlet_opsparing:
            growth = (1 + r) ** n
            pv_raw_list = []
            for fv, pmt_i in zip(fv_list, pmt_per_prod):
                if fv is None:
                    pv_raw_list.append(None)
                    continue
                pmt_fv = pmt_i * (growth - 1) / r if r > 0 else pmt_i * n
                pv_raw = (fv - pmt_fv) / growth
                pv_raw_list.append(max(0.0, pv_raw))

            total_pv_raw = sum(p for p in pv_raw_list if p is not None)
            if total_pv_raw > 0:
                scale = samlet_opsparing / total_pv_raw
                for prod, pv_raw in zip(prods, pv_raw_list):
                    if pv_raw is not None:
                        prod["estimated_saldo"] = int(pv_raw * scale)


def _to_legacy_format(raw: dict, full_text: str) -> dict:
    """Konverterer nyt JSON til det format resten af appen forventer."""

    def to_int(v):
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
        return None

    # ── Person — udled fødselsdato og alder fra CPR ──
    import re as _re
    from datetime import date as _date

    foedselsdato = ""
    alder = None
    cpr_raw = str(raw.get("cpr") or "")
    cpr_digits = _re.sub(r"\D", "", cpr_raw)  # fjern bindestreg etc.
    if len(cpr_digits) >= 6:
        dd  = int(cpr_digits[0:2])
        mm  = int(cpr_digits[2:4])
        yy  = int(cpr_digits[4:6])
        # Århundrede: pensionskunder er altid født i 1900-tallet
        yyyy = 1900 + yy
        try:
            foedselsdato = f"{dd:02d}.{mm:02d}.{yyyy}"
            today = _date.today()
            alder = today.year - yyyy - ((today.month, today.day) < (mm, dd))
        except ValueError:
            pass

    person = {
        "navn":         raw.get("name", ""),
        "foedselsdato": foedselsdato,
        "alder":        alder,
        "rapport_dato": "",
    }

    # Byg aftalenr → produkttype(r) fra payout_products (mere præcis end agreements[].type)
    nr_til_ptypes: dict[str, list[str]] = {}
    for p in raw.get("payout_products", []):
        nr    = str(p.get("number") or "").strip()
        ptype = (p.get("type") or "").strip()
        if nr and ptype:
            if nr not in nr_til_ptypes:
                nr_til_ptypes[nr] = []
            if ptype not in nr_til_ptypes[nr]:
                nr_til_ptypes[nr].append(ptype)

    def best_ptype(nr: str, fallback: str) -> str:
        """Vælg bedste produkttype: kapitalpension > aldersopsparing > ratepension > livsvarig > resten."""
        ptypes = nr_til_ptypes.get(nr, [])
        if not ptypes:
            return fallback
        priority = ["kapitalpension", "livsvarig", "ratepension", "aldersopsparing"]
        for key in priority:
            for pt in ptypes:
                if key in pt.lower():
                    return pt
        return ptypes[0]

    # ── Ordninger ──
    ordninger = []
    for a in raw.get("agreements", []):
        nr  = str(a.get("number") or "").strip()
        prv = str(a.get("provider") or "")
        raw_type = a.get("type") or ""
        ptype = best_ptype(nr, raw_type)
        ordninger.append({
            "aftalenr":           nr,
            "selskab":            prv,
            "produkttype":        ptype,
            "aarlig_indbetaling": to_int(a.get("annual_contribution")) or 0,
            "opsparing":          to_int(a.get("balance")),
            "kun_forsikring":     bool(a.get("insurance_only", False)),
            "investeringsform":   raw_type,
        })

    # ── Opsparing total per selskab (fra ordninger) ──
    opsparing_total: dict[str, int] = {}
    for o in ordninger:
        if o["opsparing"] and not o["kun_forsikring"]:
            prv = o["selskab"]
            opsparing_total[prv] = opsparing_total.get(prv, 0) + o["opsparing"]

    # ── Forsikringer ──
    forsikringer = {
        "liv_ved_doed":            to_int(raw.get("death_cover")) or 0,
        "tabt_arbejdsevne_aarlig": to_int(raw.get("disability_annual")) or 0,
        "kritisk_sygdom":          to_int(raw.get("critical_illness")) or 0,
        "sundhedsforsikring":      bool(raw.get("has_health_insurance", False)),
        "gruppeliv":               0,
    }

    # ── Pensionsprodukter ──
    pensionsprodukter = []
    for p in raw.get("payout_products", []):
        ams = p.get("amounts") or {}
        pensionsprodukter.append({
            "selskab":        p.get("provider", ""),
            "aftalenr":       str(p.get("number") or ""),
            "produkttype":    p.get("type", ""),
            "skat_type":      p.get("tax", ""),
            "aldersperioder": {k: int(v) for k, v in ams.items() if v},
            "trend":          None,
            "opsparing":      to_int(p.get("current_balance")),
        })

    tidligste = raw.get("earliest_retirement_age")

    # ── Estimér FV og PMT-fordeling for multi-produkt aftaler ──
    _estimer_pmt_fordeling(pensionsprodukter, ordninger, alder, tidligste)

    samlet_aarlig_indbetaling = sum(o["aarlig_indbetaling"] for o in ordninger)

    return {
        "person":                     person,
        "opsparing_total":            opsparing_total,
        "ordninger":                  ordninger,
        "pensionsprodukter":          pensionsprodukter,
        "tidligste_pensionsalder":    tidligste,
        "udbetalingsscenarier":       [],
        "forsikringer":               forsikringer,
        "samlet_aarlig_indbetaling":  samlet_aarlig_indbetaling,
        "raw":                        raw,
        "raa_tekst":                  full_text,
    }


# ── Formater profil til LLM-kontekst ─────────────────────────────────────────

def format_profil_til_tekst(profil: dict) -> str:
    p    = profil.get("person", {})
    f    = profil.get("forsikringer", {})
    ord_ = profil.get("ordninger", [])
    prod = profil.get("pensionsprodukter", [])
    tid_alder = profil.get("tidligste_pensionsalder")
    total_ops = sum(profil.get("opsparing_total", {}).values())

    alder     = p.get("alder")
    foedsels  = p.get("foedselsdato", "")

    linjer = [
        f"PENSIONSPROFIL FOR: {p.get('navn', 'Ukendt')}",
    ]
    if foedsels:
        linjer.append(f"Født: {foedsels}")
    if alder:
        linjer.append(f"Nuværende alder: {alder} år")
    if tid_alder:
        linjer.append(f"Tidligste mulige pensionsalder: {tid_alder} år")

    linjer += ["", "=== AFTALER OG INDBETALINGER ==="]
    for a in ord_:
        selskab  = a.get("selskab") or "Ukendt"
        nr       = a.get("aftalenr", "")
        indbetal = a.get("aarlig_indbetaling", 0)
        opsp     = a.get("opsparing")
        ptype    = a.get("produkttype", "")
        kun_fors = a.get("kun_forsikring", False)

        linje = f"  {selskab} ({nr}){' – ' + ptype if ptype else ''}"
        linje += f"\n    Indbetaling: {indbetal:,.0f} kr/år".replace(",", ".")
        if opsp:
            linje += f"  |  Opsparing: {opsp:,.0f} kr.".replace(",", ".")
        elif kun_fors:
            linje += "  |  Kun forsikring (ingen opsparing)"
        else:
            linje += "  |  Opsparing opgøres ikke"
        linjer.append(linje)

    samlet = profil.get("samlet_aarlig_indbetaling", 0)
    linjer.append(f"\n  SAMLET INDBETALING (inkl. forsikringspræmier): {samlet:,.0f} kr/år".replace(",", "."))
    if total_ops:
        linjer.append(f"  TOTAL OPSPARING: {total_ops:,.0f} kr.".replace(",", "."))

    if prod:
        # Vis skattetyper, varighed og estimerede PMT-fordelinger
        linjer += ["", "=== PRODUKTTYPER, SKAT, VARIGHED OG ESTIMERET INDBETALING ==="]
        linjer.append("  (Estimerede indbetalinger er back-beregnet fra payout-siden — brug som udgangspunkt, ikke facit)")
        seen = set()
        for pr in prod:
            selskab  = pr.get("selskab", "")
            ptype    = pr.get("produkttype", "")
            skat     = pr.get("skat_type", "")
            perioder = pr.get("aldersperioder") or {}
            est_pmt  = pr.get("estimated_pmt")
            key = (selskab, ptype)
            if key in seen:
                continue
            seen.add(key)

            # Udled varighed fra periodenavne (ikke beløb)
            varighed_str = ""
            pt_lower = ptype.lower()
            if "kapitalpension" in pt_lower or "aldersopsparing" in pt_lower:
                varighed_str = "engangsbeløb"
            elif "livsvarig" in pt_lower or "livrente" in pt_lower:
                varighed_str = "livsvarig"
            elif "rate" in pt_lower and perioder:
                years = 0
                for k in perioder:
                    if re.match(r"^\d+ år$", k):
                        years += 1
                    elif m2 := re.match(r"^(\d+)-(\d+) år$", k):
                        years += int(m2.group(2)) - int(m2.group(1)) + 1
                if years:
                    varighed_str = f"{years} år"

            extra = f", udbetaling: {varighed_str}" if varighed_str else ""
            pmt_s = f", estimeret indbetaling: {est_pmt:,.0f} kr/år".replace(",", ".") if est_pmt else ""
            linjer.append(f"  {selskab} – {ptype}: skat={skat}{extra}{pmt_s}")

    linjer += ["", "=== FORSIKRINGER ==="]
    linjer.append(f"  Liv ved død: {f.get('liv_ved_doed', 0):,.0f} kr.".replace(",", "."))
    linjer.append(f"  Tabt arbejdsevne: {f.get('tabt_arbejdsevne_aarlig', 0):,.0f} kr/år".replace(",", "."))
    linjer.append(f"  Kritisk sygdom: {f.get('kritisk_sygdom', 0):,.0f} kr.".replace(",", "."))
    linjer.append(f"  Sundhedsforsikring: {'Ja' if f.get('sundhedsforsikring') else 'Nej'}")

    return "\n".join(linjer)
