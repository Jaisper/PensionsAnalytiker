"""
Parser til PensionsInfo PDF-rapport.
Bruger pdfplumber til tekst-udtræk og Claude Sonnet til struktureret JSON-ekstraktion.
"""

import re
import os
import json
import asyncio
import logging
import warnings
import pdfplumber
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
warnings.filterwarnings("ignore")
# pdfminer (pdfplumber's underlying tekst-motor) logger — ikke Python
# warnings, så warnings.filterwarnings ovenfor rammer den ikke. Nogle
# PensionsInfo-rapporter har utraditionelle/ufuldstændige font-descriptorer
# eller farveværdier, som ellers drukner den rigtige log i hundredvis af
# harmløse "Could get FontBBox..."-linjer per upload.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

from prompts import EXTRACTION_PROMPT


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
        "flere oplysninger",
        "yderligere oplysninger",
        "Tidligst mulig pensionsalder",
        "Tidligst udbetaling",
        "tidligste pensionsalder",
        "Tidligste pensionsalder",
        "Du kan tidligst",
        "tidligst hæve",
        "Opsparingsalder",
    ]
    earliest_age = None
    AGE_PATTERNS = [
        r"Hvis du går på pension som (\d+)-årig",
        r"[Tt]idligst\s+mulig\s+pensionsalder[:\s]+(\d+)",
        r"[Tt]idligst\s+udbetaling[:\s]+(\d+)",
        r"[Tt]idligste\s+pensionsalder[:\s]+(\d+)",
        r"[Dd]u kan tidligst hæve fra (\d+)-årig",
        r"[Tt]idligst hæve.*?(\d+)\s*år",
    ]
    for page in pages:
        for pat in AGE_PATTERNS:
            for m in re.finditer(pat, page):
                age = int(m.group(1))
                if 55 <= age <= 75:
                    if earliest_age is None or age < earliest_age:
                        earliest_age = age

    kept = []
    for i, page in enumerate(pages):
        if any(kw in page for kw in RELEVANT_KEYWORDS):
            kept.append(f"=== SIDE {i+1} ===\n{page}")
        elif earliest_age and f"som {earliest_age}-årig" in page:
            kept.append(f"=== SIDE {i+1} ===\n{page}")
    return "\n\n".join(kept)


# Et enkelt ekstraktions-kald har i praksis taget alt fra ~10 til ~70+
# sekunder afhængigt af rapportens størrelse/antal aftaler — uden et loft
# kunne ét hængende/usædvanligt langsomt forsøg, ganget med 3 gentagne
# forsøg, nemt lægge uploadet langt ud over hvad platformens egen
# request-timeout tillader. Når DEN rammer først, afbrydes forbindelsen før
# vores eget try/except i app.py overhovedet ser fejlen, og browseren ender
# med at prøve at JSON-parse en HTML-fejlside ("<!DOCTYPE..."). Loftet her
# (og det reducerede antal forsøg) begrænser værste-falds-tiden markant,
# så vi selv når at svare med en brugbar fejlbesked først.
LLM_PER_FORSOEG_TIMEOUT_SEK = 40
LLM_MAKS_FORSOEG = 2


async def _extract_with_llm(text: str) -> dict:
    """Kalder Claude med op til LLM_MAKS_FORSOEG forsøg ved fejl, hvert med
    sin egen timeout (se LLM_PER_FORSOEG_TIMEOUT_SEK ovenfor)."""
    import anthropic as _anthropic
    client = _anthropic.AsyncAnthropic()
    last_exc: Exception = RuntimeError("Ingen forsøg gennemført")

    for attempt in range(LLM_MAKS_FORSOEG):
        try:
            msg = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8000,
                messages=[{
                    "role": "user",
                    "content": f"{EXTRACTION_PROMPT}\n\nREPORT TEXT:\n{text}",
                }],
                timeout=LLM_PER_FORSOEG_TIMEOUT_SEK,
            )
            raw = msg.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            if not raw:
                raise ValueError(
                    f"Tomt svar fra ekstraktions-modellen (stop_reason={msg.stop_reason!r})"
                )
            return json.loads(raw)
        except Exception as e:
            last_exc = e
            if attempt < LLM_MAKS_FORSOEG - 1:
                await asyncio.sleep(2 ** attempt)

    raise last_exc


_PDF_CACHE_DIR = Path(__file__).parent / ".pdf_cache"


def _pdf_cache_path(pdf_path: str) -> Path:
    import hashlib
    with open(pdf_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return _PDF_CACHE_DIR / f"{digest}.json"


async def parse_pensionsinfo_pdf(pdf_path: str) -> dict:
    """
    Udtrækker og strukturerer data fra en PensionsInfo PDF.
    Cacher resultatet på filens indhold (SHA-256), så gentagne uploads af samme
    PDF under udvikling/test ikke genudløser en ny LLM-ekstraktion.
    Slet .pdf_cache/ for at tvinge en frisk ekstraktion.
    """
    cache_path = _pdf_cache_path(pdf_path)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    pages = await asyncio.to_thread(_extract_pages, pdf_path)
    full_text = "\n\n".join(f"=== SIDE {i+1} ===\n{p}" for i, p in enumerate(pages) if p)
    if not full_text.strip():
        # pdfplumber fandt ingen tekst overhovedet — typisk en scannet/billed-PDF
        # uden tekstlag. At sende tom tekst til LLM'en giver et tomt/ubrugeligt
        # svar (json.loads fejler med "Expecting value" uden nogen god forklaring).
        raise ValueError(
            "Kunne ikke finde tekst i PDF'en. Rapporten er muligvis en scannet "
            "kopi (billede) uden tekstlag — prøv at eksportere en ny rapport "
            "direkte fra pensionsinfo.dk som PDF."
        )
    relevant = _filter_relevant_pages(pages)
    if not relevant.strip():
        # Ingen sider matchede de forventede overskrifter — sandsynligvis et
        # andet rapport-layout/version end forventet. Fald tilbage til hele
        # dokumentet i stedet for at sende tom tekst til LLM'en.
        relevant = full_text
    raw = await _extract_with_llm(relevant)
    profil = _to_legacy_format(raw, full_text)

    _PDF_CACHE_DIR.mkdir(exist_ok=True)
    cache_path.write_text(json.dumps(profil, ensure_ascii=False), encoding="utf-8")
    return profil


# r=0.04 herunder er IKKE brugerens afkastantagelse (den bruges kun i selve
# engine.py-fremskrivningen, jf. engine.satser_2026) — det er en uafhængig,
# konservativ diskonteringssats brugt til at "fortryde" en annuitet og gætte
# en manglende FV ud fra det payout-beløb rapporten selv viser, når PDF'en
# ikke oplyser saldoen direkte. Samme grund til at m=25 (nedenfor) for
# livsvarig/livrente er sit eget skøn, uafhængigt af engine.py's
# LIVSVARIG_ESTIMAT_AAR (18) — de løser to forskellige opgaver: dette er et
# forsigtigt gæt på en ukendt FV, ikke en fremadrettet udbetalingsperiode.
def _fv_fra_payout(ptype: str, aldersperioder: dict, r: float = 0.04) -> float | None:
    """Estimér FV ved at 'fortryde' annuiteten fra payout-beløbet (første periode)."""
    if not aldersperioder:
        return None
    first = next((v for v in aldersperioder.values() if v), None)
    if not first:
        return None
    pt = (ptype or "").lower()
    if "kapitalpension" in pt or "aldersopsparing" in pt:
        return float(first)
    elif "livsvarig" in pt or "livrente" in pt:
        m = 25  # forsigtigt skøn for restlevetid — se modulets kommentar ovenfor
    else:
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
    r: float = 0.04,  # se _fv_fra_payout's kommentar — uafhængig af brugerens egen afkastantagelse
) -> None:
    """
    For providers med flere produkter under samme aftalenr:
    back-beregn estimeret FV per produkt fra payout-beløbene,
    og udled deraf en estimeret PMT-fordeling (kr/år).
    Resultater gemmes direkte på hvert produkt-dict.
    """
    from collections import defaultdict
    by_key: dict[tuple, list] = defaultdict(list)
    for prod in pensionsprodukter:
        nr  = prod.get("aftalenr") or ""
        sel = (prod.get("selskab") or "").lower()
        if nr:
            by_key[(nr, sel)].append(prod)

    for (nr, sel), prods in by_key.items():
        if len(prods) < 2:
            continue

        samlet_pmt = next(
            (o["aarlig_indbetaling"] for o in ordninger
             if str(o.get("aftalenr") or "") == nr
             and (o.get("selskab") or "").lower() == sel
             and o.get("aarlig_indbetaling")),
            None,
        )
        if not samlet_pmt:
            continue

        fv_list = []
        for prod in prods:
            fv = _fv_fra_payout(prod["produkttype"], prod.get("aldersperioder") or {}, r)
            fv_list.append(fv)

        total_fv = sum(f for f in fv_list if f)
        if not total_fv:
            continue

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

        samlet_opsparing = next(
            (o["opsparing"] for o in ordninger
             if str(o.get("aftalenr") or "") == nr
             and (o.get("selskab") or "").lower() == sel
             and o.get("opsparing")),
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

    def safe_int_in_range(v, lo, hi):
        # LLM'en SKAL levere et rent tal per ekstraktions-prompten, men
        # garanterer det ikke — et uventet format (fx en streng med enhed,
        # "62 år", eller en decimal) skal degradere til None for netop dette
        # felt, ikke vælte hele parsingen af en ellers fin rapport.
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return None
        return iv if lo <= iv <= hi else None

    import re as _re
    from datetime import date as _date

    foedselsdato = ""
    alder = None
    cpr_raw = str(raw.get("cpr") or "")
    cpr_digits = _re.sub(r"\D", "", cpr_raw)
    if len(cpr_digits) >= 6:
        dd  = int(cpr_digits[0:2])
        mm  = int(cpr_digits[2:4])
        yy  = int(cpr_digits[4:6])
        # Bevidst forenkling: et CPR-nummers 7. ciffer afgør reelt century
        # (1900- vs. 2000-tal), som denne parsing ikke tjekker. Forsvarligt
        # her, fordi værktøjets egne aldersgrænser (typisk 55-85 år) betyder
        # at enhver reel bruger er født i 1900-tallet — en fejlfortolket
        # 2000-talsfødsel ville aldrig bestå den efterfølgende alder-sanitet.
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

    nr_til_ptypes: dict[tuple, list[str]] = {}
    for p in raw.get("payout_products", []):
        nr    = str(p.get("number") or "").strip()
        prv_p = str(p.get("provider") or "").strip()
        ptype = (p.get("type") or "").strip()
        if nr and ptype:
            key = (nr, prv_p)
            if key not in nr_til_ptypes:
                nr_til_ptypes[key] = []
            if ptype not in nr_til_ptypes[key]:
                nr_til_ptypes[key].append(ptype)

    def best_ptype(nr: str, selskab: str, fallback: str) -> str:
        ptypes = nr_til_ptypes.get((nr, selskab), [])
        if not ptypes:
            return fallback
        priority = ["kapitalpension", "livsvarig", "ratepension", "aldersopsparing"]
        for key in priority:
            for pt in ptypes:
                if key in pt.lower():
                    return pt
        return ptypes[0]

    ordninger = []
    for a in raw.get("agreements", []):
        nr  = str(a.get("number") or "").strip()
        prv = str(a.get("provider") or "")
        raw_type = a.get("type") or ""
        ptype = best_ptype(nr, prv, raw_type)
        epa_int = safe_int_in_range(a.get("earliest_payout_age"), 55, 75)
        apa_int = safe_int_in_range(a.get("agreed_payout_age"), 55, 85)
        ordninger.append({
            "aftalenr":                nr,
            "selskab":                 prv,
            "produkttype":             ptype,
            "aarlig_indbetaling":      to_int(a.get("annual_contribution")) or 0,
            "opsparing":               to_int(a.get("balance")),
            "kun_forsikring":          bool(a.get("insurance_only", False)),
            "investeringsform":        raw_type,
            "tidligste_udbetaling":    epa_int,
            "aftalt_udbetalingsalder": apa_int,
        })

    opsparing_total: dict[str, int] = {}
    for o in ordninger:
        if o["opsparing"] and not o["kun_forsikring"]:
            prv = o["selskab"]
            opsparing_total[prv] = opsparing_total.get(prv, 0) + o["opsparing"]

    forsikringer = {
        "liv_ved_doed":            to_int(raw.get("death_cover")) or 0,
        "tabt_arbejdsevne_aarlig": to_int(raw.get("disability_annual")) or 0,
        "kritisk_sygdom":          to_int(raw.get("critical_illness")) or 0,
        "sundhedsforsikring":      bool(raw.get("has_health_insurance", False)),
        "gruppeliv":               0,
    }

    aftalt_alder_by_aftale = {
        (o["aftalenr"], o["selskab"]): o["aftalt_udbetalingsalder"]
        for o in ordninger if o["aftalt_udbetalingsalder"]
    }

    pensionsprodukter = []
    for p in raw.get("payout_products", []):
        ams = p.get("amounts") or {}
        nr  = str(p.get("number") or "")
        prv = p.get("provider", "")
        pensionsprodukter.append({
            "selskab":                p.get("provider", ""),
            "aftalenr":               nr,
            "produkttype":            p.get("type", ""),
            "skat_type":              p.get("tax", ""),
            "aldersperioder":         {k: int(v) for k, v in ams.items() if v},
            "trend":                  None,
            "opsparing":              to_int(p.get("current_balance")),
            "aftalt_udbetalingsalder": aftalt_alder_by_aftale.get((nr, prv)),
        })

    per_aftale_ages = [o["tidligste_udbetaling"] for o in ordninger if o["tidligste_udbetaling"]]
    tidligste_top = raw.get("earliest_retirement_age")
    if per_aftale_ages:
        tidligste = min(per_aftale_ages)
        if tidligste_top and tidligste_top < tidligste:
            tidligste = tidligste_top
    else:
        tidligste = tidligste_top

    _estimer_pmt_fordeling(pensionsprodukter, ordninger, alder, tidligste)

    samlet_aarlig_indbetaling = sum(
        o["aarlig_indbetaling"] for o in ordninger
        if "ATP" not in (o.get("selskab") or "")
    )

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

        tid_udb = a.get("tidligste_udbetaling")
        linje = f"  {selskab} ({nr}){' – ' + ptype if ptype else ''}"
        if tid_udb:
            linje += f"  [tidligst udbetaling: {tid_udb} år]"
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

    linjer += ["", "=== FORSIKRINGER (summeret fra rapport — ikke per selskab) ==="]
    linjer.append(f"  Liv ved død: {f.get('liv_ved_doed', 0):,.0f} kr.".replace(",", "."))
    linjer.append(f"  Tabt arbejdsevne: {f.get('tabt_arbejdsevne_aarlig', 0):,.0f} kr/år".replace(",", "."))
    ks = f.get("kritisk_sygdom", 0)
    linjer.append(f"  Kritisk sygdom: {ks:,.0f} kr. (NÆVN IKKE SPECIFIKT SELSKAB — fremgår ikke af data)".replace(",", ".") if ks else "  Kritisk sygdom: Ingen")
    linjer.append(f"  Sundhedsforsikring: {'Ja' if f.get('sundhedsforsikring') else 'Nej'}")

    return "\n".join(linjer)
