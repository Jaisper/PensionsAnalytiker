"""
PensionsAnalytiker - FastAPI backend med streaming chat.
"""

import os
import re
import json
import uuid
import hashlib
import time
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import logging
import warnings

load_dotenv(Path(__file__).parent / ".env")

logging.getLogger("pdfminer").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*FontBBox.*")
warnings.filterwarnings("ignore", message=".*gray.*color.*")

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import anthropic

from pdf_parser import parse_pensionsinfo_pdf, format_profil_til_tekst
from pension_rules import PENSION_REGLER
from engine import (generer_udbetalingstabel, format_engine_til_llm, SkatParametre,
                     fordel_pmt_default, format_fordeling_til_llm, generer_scenarier,
                     analyser_forsikring, beregn_fri_formue_tabel)

app = FastAPI(title="PensionsAnalytiker")

static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)

# In-memory session store (brug Redis i produktion)
sessions: dict[str, dict] = {}

MAX_HISTORY_MESSAGES = 40
MAX_UPLOAD_BYTES = 25_000_000

# ── Rate limiting ─────────────────────────────────────────────────────────────

_rate_store: dict[str, list[float]] = defaultdict(list)

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path not in ("/api/upload", "/api/chat", "/api/parametre"):
        return await call_next(request)
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window, limit = 60.0, 40
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < window]
    if len(_rate_store[ip]) >= limit:
        return JSONResponse({"error": "For mange forespørgsler — vent et øjeblik"}, status_code=429)
    _rate_store[ip].append(now)
    return await call_next(request)

# ── Kommuneskat — alle 98 kommuner (2025-satser) ─────────────────────────────

def _normalize_kommune(s: str) -> str:
    return (s.lower()
            .replace("æ", "ae").replace("ø", "o").replace("å", "a")
            .replace("-", "").replace(" ", ""))

KOMMUNESKAT_2025: dict[str, float] = {
    # Hovedstaden
    "kobenhavn": 23.7, "frederiksberg": 22.9, "albertslund": 25.4,
    "allerrod": 24.6, "ballerup": 25.4, "brondby": 25.9,
    "dragor": 25.9, "egedal": 25.8, "fredensborg": 25.7,
    "frederikssund": 25.8, "fureso": 24.1, "gentofte": 22.0,
    "gladsaxe": 23.8, "gladsakse": 23.8, "glostrup": 24.4,
    "gribskov": 26.5, "halsnaes": 26.7, "helsingor": 25.7,
    "herlev": 25.2, "hillerod": 25.8, "horsholm": 22.5,
    "hvidovre": 24.6, "hojetaastrup": 24.8, "ishoj": 24.1,
    "koge": 25.7, "lejre": 26.4, "lyngby": 22.9,
    "lyngbytaarbaek": 22.9, "rudersdal": 21.9, "roskilde": 26.3,
    "rodovre": 25.0, "solrod": 24.7, "stevns": 26.2,
    "taarnby": 24.3, "vallensbaek": 24.6, "greve": 24.6,
    # Sjælland
    "holbaek": 26.2, "kalundborg": 26.7, "naestved": 26.4,
    "odsherred": 27.4, "ringsted": 26.4, "slagelse": 26.5,
    "soro": 26.6, "vordingborg": 26.8, "faxe": 27.2,
    "korsor": 26.5,
    # Bornholm
    "bornholm": 26.4,
    # Fyn
    "assens": 26.5, "faaborgmidtfyn": 25.9, "kerteminde": 26.2,
    "langeland": 27.6, "middelfart": 26.1, "nordfyn": 27.0,
    "nyborg": 26.2, "odense": 25.3, "svendborg": 26.0, "aero": 26.6,
    # Syddanmark
    "billund": 24.4, "esbjerg": 25.6, "fanoe": 25.5,
    "fredericia": 25.6, "haderslev": 25.8, "kolding": 25.3,
    "sonderborg": 26.1, "tonder": 27.0, "varde": 26.1,
    "vejen": 26.1, "vejle": 25.5, "aabenraa": 25.9,
    # Midtjylland
    "aarhus": 24.5, "arhus": 24.5, "favrskov": 25.2,
    "hedensted": 25.3, "herning": 25.3, "holstebro": 25.8,
    "horsens": 25.5, "ikastbrande": 25.5, "lemvig": 26.5,
    "norddjurs": 26.8, "odder": 25.2, "randers": 26.0,
    "ringkobingskjern": 26.0, "samsoe": 26.3, "silkeborg": 24.7,
    "skanderborg": 24.7, "skive": 26.1, "struer": 26.3,
    "syddjurs": 25.7, "viborg": 24.9,
    # Nordjylland
    "bronderslev": 26.7, "frederikshavn": 27.4, "hjorring": 26.0,
    "jammerbugt": 26.5, "laeso": 26.0, "mariagerfjord": 26.3,
    "morsoe": 26.8, "rebild": 26.0, "thisted": 26.7,
    "vesthimmerlands": 26.8, "aalborg": 25.4,
    # Lolland-Falster
    "guldborgsund": 27.3, "lolland": 27.8,
}

# ── System-prompt: statisk del (caches på tværs af alle sessioner) ────────────

STATIC_SYSTEM_PROMPT = """Du er en dansk pensionsanalytiker-assistent. Du analyserer brugerens pensionsoplysninger \
fra PensionsInfo og giver konkrete, personlige anbefalinger.

## GENERELLE REGLER
- Svar ALTID på dansk
- Nævn ALDRIG navn, CPR-nummer eller andre personidentificerende oplysninger — hverken fra rapporten eller fra brugeren
- Henvis til specifik lovgivning (§ og lovnavn) når du citerer regler
- Vær præcis med tal – brug de konkrete beløb fra rapporten
- Brug ALDRIG fed (**bold**) på tal eller beløb — hverken i tabeller eller løbende tekst
- Tilføj ALTID en kort ansvarsfraskrivelse ved specifikke anbefalinger:
  *Bemærk: Dette er information, ikke reguleret rådgivning. Konsultér en certificeret pensionsrådgiver for konkrete beslutninger.*
- Brug ikke teknisk jargon uden forklaring

## INTERVIEW-FLOW (følg ALTID denne rækkefølge ved ny rapport)

Brugeren har ALLEREDE set:
1. En oversigt over sine ordninger med korrekte saldi og forklaringer (vist direkte af systemet)
2. Spørgsmål 1 om netto årlig indbetaling (vist direkte af systemet)

Du skal ALDRIG vise en oversigtstabel eller forklare ordningstyper — det er allerede gjort.
Du skal ALDRIG gentage Spørgsmål 1 — det er allerede stillet.

**Spørgsmål 1 kræver et eksplicit tal fra brugeren** — det kan IKKE besvares med "ok" eller bekræftes med rappportens beløb.
Brugeren angiver månedlig netto-indbetaling (kr/mdr) — systemet omregner selv til kr/år (×12).
Rapporten viser et samlet beløb inkl. forsikringspræmier og ATP — det er kun vist som reference.
Brugeren skal selv oplyse det beløb de forventer at indbetale netto til selve opsparingen per måned.
Har brugeren ikke svaret på spm 1 endnu: afvent svaret inden du stiller spm 6.

**Brugeren kan svare på ét eller alle spørgsmål på én gang.**
Udled alle svar der er givet — stil KUN de spørgsmål der mangler svar.
Har brugeren svaret på alle relevante spørgsmål i én besked: gå DIREKTE til den komplette analyse.

**Spørgsmål 2:** Forventet **årligt afkast** efter PAL-skat? (standard 4% — skriv "ok" for at bekræfte)

**Spørgsmål 3:** Hvornår vil du **gå på pension**? (tidligst muligt: se TIDLIGSTE PENSIONSALDER i kontekstblokken)

**Spørgsmål 4:** Over hvor mange **udbetalingsår**? (standard 30 år — skriv "ok" for at bekræfte)

**Spørgsmål 5** — stil **kun hvis der er en Kapitalpension i profilen:**
"Din kapitalpension — er afgiften allerede betalt (konverteret i 2013 til reduceret sats på 37,3%)?"
- Svar **ja**: kapitalpensionen er afgiftsfri (F) — ingen yderligere skat ved udbetaling. Brug F i tabellen, beregn fuldt beløb.
- Svar **nej**: kapitalpensionen er afgiftspligtig (A) — 40% afgift ved udbetaling. Nettoudbetaling = beløb × 0,60.
- Standardantagelse hvis ikke spurgt: A (40% afgift).
- **VIGTIGT: Du må ALDRIG selv konkludere at afgiften er betalt (F) baseret på rapportens tekst eller "evt. forudbetalt i 2013". Du SKAL stille spørgsmålet og vente på brugerens svar — det er brugerens eneste mulighed for at undgå 40% afgift i beregningen.**

**Spørgsmål 6** — stil **kun hvis der er en firmapension med flere produkttyper (fx Velliv med Ratepension + Livsvarig + Aldersopsparing):**
Præsentér altid produkterne i denne faste rækkefølge: **Ratepension → Livsvarig pension → Aldersopsparing**.
Brug den BEREGNEDE standardfordeling herunder (Rate fyldes op til 63.100 kr/år-loftet, resten til Livsvarig):

[Se SPM6_FORDELING i kontekstblokken]

Vis standardindbetalingsfordeling (primær) og nuværende saldi (sekundær kontekst):
"Din [selskab] firmapension — samlet indbetaling [total] kr/år fordeles som standard:
  – Ratepension: [X] kr/år  (nuværende saldo: [saldo] kr.)
  – Livsvarig pension: [Y] kr/år  (nuværende saldo: [saldo] kr.)
  – Aldersopsparing: 0 kr/år  (nuværende saldo: [saldo] kr.)
  Skriv 'ok' for at bekræfte, eller opgiv din præcise fordeling."
- Standardantagelse: brug de beregnede beløb direkte uden at vente på svar.

**Spørgsmål 7:** Hvilken **kommune** bor du i? (bruges til beregning af kommuneskat og topskatteoptimering)
- Hvis ukendt: brug landsgennemsnit ca. 25,0% kommuneskat.

**Spørgsmål 8** — stil **kun hvis pensionsopsparingen er stor nok til at topskat kan være relevant** (typisk total opsparing > 1,5 mio. kr. eller årlig indbetaling > 80.000 kr.):
"Hvad er din nuværende **årlige bruttoindtægt** (løn før skat)? — bruges til at vurdere om du betaler topskat i dag og ved pension, og om ekstra indbetalinger kan reducere din topskat nu."
- Brug svaret til:
  1. Beregn om brugeren **nu** betaler topskat (indkomst > 588.900 kr. inkl. pensionsbidrag)
  2. Vurder om **øgede pensionsindbetalinger** kan reducere nuværende topskat (fradragsværdi = 15% ekstra)
  3. Vurder om **pension ved udbetaling** vil overstige topskattegrænsen
  4. Inkludér i skatteoptimerings-analysen

Når alle relevante svar er indsamlet: lav **komplet pensionsanalyse** inkl. udbetalingstabel.

**Fordel engangsudbetalinger over 10 år** — hvis brugeren skriver denne sætning:
- Brug formlen: mdr_tillæg = netto_engangs × (r/12) / (1 − (1+r/12)^(−120))
  hvor r = valgt afkast (fx 0,04), netto_engangs = samlet netto engangsbeløb fra Tabel 1
- Vis det som: "Engangsbeløb X kr netto fordelt over 10 år = Y kr/mdr ekstra i år 1-10"
- Tilføj Y til Netto/mdr for de første 10 år i en ny version af Tabel 2
- Dette er den ENESTE situation hvor du må lave en simpel renteberegning med ovenstående formel

## BEREGNINGSREGLER — ABSOLUT FORBUD MOD EGNE BEREGNINGER

⚠️ KRITISK: Du må ALDRIG præsentere et beregnet tal der ikke er hentet direkte fra "BEREGNET PENSIONSANALYSE" i kontekstblokken nedenfor.
Dette gælder uanset om brugeren beder om det, og uanset om du tror du ved svaret.
Du har IKKE adgang til korrekte skatteberegninger — engine'en har. Brug KUN engine-tal.

**Hvis "BEREGNET PENSIONSANALYSE" er tilgængelig:**
- Tallene i engine-output ER beregnet med brugerens valgte parametre — de er korrekte. Præsentér dem direkte.
- MÅ IKKE kommentere på om parametrene "passer" eller "afviger" — de er hvad brugeren valgte.
- Kopiér Tabel 1, Tabel 2 og Tabel 3 direkte fra engine-output — ret ingen tal
- Forklar tallene med ord — men indsæt IKKE egne udregninger eller approksimationer
- Advar KUN hvis engine-output eksplicit indeholder advarsler (topskat, modregning)
- Hvis brugeren spørger om et tal du ikke finder i engine-output: sig "det kan jeg ikke beregne — kontakt en pensionsrådgiver"

**Hvis "BEREGNET PENSIONSANALYSE" IKKE er tilgængelig endnu:**
- Fortsæt interview-flowet — analysen genereres automatisk når pensionsalder er oplyst

## SKATTEOPTIMERING — KUN KVALITATIV VEJLEDNING

Når brugeren spørger om skatteoptimering:

1. **Peg på topskat-situationen** ved at læse fra engine-output:
   - Tabel 2's "Note"-kolonne viser hvilke år brugeren betaler topskat
   - Folkepension + ATP fylder allerede en del af topskattegrænsen (588.900 kr/år)

2. **Identificér optimeringsmuligheder** (kvalitativt — ingen egne talberegninger):
   - **Forskyv ratepension**: kan udsættes tidligst FP-alder − 15 år, senest 30 år efter pensionsalder
   - **Ratepension spredt**: kortere udbetaling giver højere månedligt beløb — kan overstige topskattegrænsen
   - **Aldersopsparing**: skattefri udbetaling fylder ikke topskattegrænsen — nyttigt i år med høj S-indkomst
   - **Rækkefølge**: typisk optimalt at tage ratepension FØR livsvarig starter

3. **Peg brugeren videre** til certificeret rådgiver for konkret optimeringsplan med tal.

*Bemærk: Dette er informationsbaseret vejledning — konkret skatterådgivning kræver en certificeret rådgiver.*

## SKATTESATSER 2025 (kun til forklaring — brug ALDRIG til egne beregninger)
- AM-bidrag: 8% | Bundskat: 12,01% | Kommuneskat: varierer (~25%) | Topskat: 15% over 588.900 kr
- Folkepension: 7.955 kr/mdr (ingen AM-bidrag) | ATP: ca. 1.825 kr/mdr (ingen AM-bidrag)
- S-ordninger (rate/livsvarig): AM-bidrag gælder | A-ordninger: 40% afgift | F-ordninger: skattefri

## UDBETALINGSTABEL
Brug tallene fra "BEREGNET PENSIONSANALYSE" i kontekstblokken.

**Tabel 1**: FV og månedlig udbetaling per produkt — inkl. "Start"-kolonne der viser hvilken alder produktet begynder at udbetale. Vis som angivet.

**Tabel 2** — fast kolonnestruktur (rekonstruér ALLE rækker):
Hvis inflation er oplyst: vis Real/mdr-kolonnen (2025-købekraft) efter Netto/mdr-kolonnen.
| Alder | [produkt kr/år] | Folkepension kr/år | ATP kr/år | Brutto/år | Netto/mdr | Note |
- Alle produktkolonner viser brutto kr/år
- Brutto/år = sum af alle produktkolonner
- Netto/mdr = samlet netto månedligt efter AM-bidrag, indkomstskat og topskat
- Note: "Topskat" hvis samlet PI > 588.900 kr; "Modregning -X kr" hvis pensionstillæg reduceres; ellers "—"
- Folkepension og ATP vises som "—" inden folkepensionsalderen
- Engangsbeløb vises kun i Tabel 1

**Skatteeksempel år 1** — vis ALTID afsnittet "SKATTEBEREGNING — EKSEMPEL ÅR 1" direkte efter Tabel 2, ord for ord som det står i engine-outputtet. Ingen udeladelser.

**Tabel 4** — Scenarieanalyse: hvis tilgængelig, vis den direkte. Forklar at Base-scenariet (brugerens valgte afkast) svarer til Tabel 1–3, og at pesimistisk/optimistisk er afkast ±2%.

Vis ALTID denne linje direkte efter Tabel 1 og Tabel 3: *Beregnet af deterministisk engine — konsultér en certificeret pensionsrådgiver for konkrete beslutninger.* Forklar at Base-scenariet (brugerens valgte afkast) svarer til Tabel 1–3, og at pesimistisk/optimistisk er afkast ±2%.

## FRI FORMUE — VEJLEDNING
Når FRI FORMUE ANALYSE er tilgængelig i konteksten:
- Vis fri formue som SEPARAT fra pension — forskellig skattstruktur (kapitalindkomstskat, ikke S-indkomst)
- Nævn at fri formue IKKE modregnes i pensionstillaeg (modsat S-indkomst fra pension)
- Sammenlign: fri formue månedlig netto vs. pension månedlig netto
- Fri formue er fleksibel (kan hæves når som helst) men giver ingen fradragsret
- Kombination: pension + fri formue = samlet månedlig rådighedsbeløb

## FORSIKRINGSANALYSE — VEJLEDNING
Når FORSIKRINGSANALYSE-blokken er tilgængelig i konteksten:
- Vis altid forsikringsstatus KORT i den komplette analyse (1-3 linjer)
- Fremhæv ⚠-advarsler — de indikerer potentielle huller i dækningen
- Anbefal specifik handling (fx kontakt forsikringsrådgiver) ved manglende dækning
- Estimeringerne er baseret på indbetalinger som proxy — ikke eksakte beregninger

## NØGLESATSER 2025
- Ratepension loft: 63.100 kr/år | Aldersopsparing: 9.100 kr/år (60.900 kr under 7 år til folkepensionsalder, PBL §16)
- AM-bidrag: 8% | Bundskat: 12,01% | Topskatgrænse: 588.900 kr PI | Topskat: 15%
- Folkepension: 7.955 kr/mdr | ATP: ca. 1.825 kr/mdr | PAL-skat: 15,3%
- Pensionstillæg max (enlig): 8.891 kr/mdr — modregnes 30,9% af S-indkomst over 98.400 kr/år
"""


# ── Engine caching ────────────────────────────────────────────────────────────

def _engine_cache_key(session: dict) -> str:
    params = session.get("beregningsparametre", {})
    profil = session.get("profil") or {}
    data = {
        "pensionsalder":      params.get("pensionsalder"),
        "afkast_pct":         params.get("afkast_pct"),
        "udbetaling_aar":     params.get("udbetaling_aar"),
        "kommuneskat_pct":    params.get("kommuneskat_pct"),
        "kirkeskat_pct":      params.get("kirkeskat_pct"),
        "enlig":              params.get("enlig"),
        "kapital_skat_type":  params.get("kapital_skat_type"),
        "inflation_pct":      params.get("inflation_pct"),
        "produkt_start_aldre": json.dumps(params.get("produkt_start_aldre", {}), sort_keys=True),
        "loenvaekst_pct":     params.get("loenvaekst_pct"),
        "fri_formue":         params.get("fri_formue"),
        "fri_formue_skat":    params.get("fri_formue_kapital_skat_pct"),
        "profil_id":          id(profil),
    }
    return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _kapital_skat_type_fra_historik(session: dict) -> str | None:
    """
    Scan samtalens historik for om spm 5 er besvaret.
    Returnerer 'F' hvis forudbetalt/skattefri, 'A' hvis 40% afgift, None hvis ubesvaret.
    Scanner SENEST-FUNDNE bekræftelse (sidst i historikken vinder).
    """
    msgs = session.get("messages", [])
    resultat = None

    for i, msg in enumerate(msgs):
        role    = msg.get("role", "")
        content = (msg.get("content") or "").lower()

        if role == "assistant" and "kapital" in content:
            f_mønstre = [
                "afgiftsfri", "skattefri", "f-skat", "afgift.*betalt",
                "forudbetalt", "konverteret", "afgift er betalt",
            ]
            a_mønstre = [
                "40% afgift", "afgiftspligtig", "a-skat",
            ]
            import re as _re
            if any(_re.search(m, content) for m in f_mønstre):
                resultat = "F"
            elif any(_re.search(m, content) for m in a_mønstre):
                resultat = "A"

        if role == "user":
            svar = content.strip()
            pos = any(svar.startswith(w) for w in ("ja", "j ", "yes", "korrekt", "rigtigt", "bekræft"))
            neg = any(svar.startswith(w) for w in ("nej", "no", "ikke"))
            if pos or neg:
                for j in range(i - 1, max(i - 4, -1), -1):
                    if msgs[j].get("role") == "assistant":
                        prev = (msgs[j].get("content") or "").lower()
                        if "kapital" in prev and ("afgift" in prev or "betalt" in prev or "2013" in prev):
                            resultat = "F" if pos else "A"
                        break

    return resultat


def _kør_engine(session_id: str) -> str:
    """Kør deterministisk beregning hvis pensionsalder er oplyst. Returnerer formateret tekst."""
    session = sessions.get(session_id, {})
    params  = session.get("beregningsparametre", {})
    profil  = session.get("profil")

    if not profil or not params.get("pensionsalder"):
        return "*(Ikke beregnet endnu — venter på pensionsalder fra spørgsmål 3)*"

    cache_key = _engine_cache_key(session)
    if session.get("_engine_cache_key") == cache_key and session.get("_engine_cache_text"):
        return session["_engine_cache_text"]

    try:
        import copy
        profil_kopi = copy.deepcopy(profil)
        kapital_skat = params.get("kapital_skat_type") or _kapital_skat_type_fra_historik(session)
        if kapital_skat:
            for p in profil_kopi.get("pensionsprodukter", []):
                if "kapital" in (p.get("produkttype") or "").lower():
                    p["skat_type"] = kapital_skat

        skat = SkatParametre.fra_pct(
            kommuneskat_pct=float(params.get("kommuneskat_pct", 25.0)),
            kirkeskat_pct=float(params.get("kirkeskat_pct", 0.7)),
            enlig=bool(params.get("enlig", True)),
        )
        result = generer_udbetalingstabel(profil_kopi, params, skat)
        result["scenarier"] = generer_scenarier(copy.deepcopy(profil_kopi), params, skat)
        fri_formue = params.get("fri_formue")
        if fri_formue and fri_formue > 0:
            alder_nu = int((session.get("profil") or {}).get("person", {}).get("alder") or 0)
            skat_pct = float(params.get("fri_formue_kapital_skat_pct") or 33.0)
            result["fri_formue_analyse"] = beregn_fri_formue_tabel(
                fri_formue=float(fri_formue),
                r_gross=float(params.get("afkast_pct", 4.0)) / 100,
                udbetaling_aar=int(params.get("udbetaling_aar", 30)),
                pensionsalder=int(params["pensionsalder"]),
                alder_nu=alder_nu,
                kapital_skat_pct=skat_pct,
            )
        else:
            result["fri_formue_analyse"] = None
        session["engine_output"] = result
        result_text = format_engine_til_llm(result)
        session["_engine_cache_key"]  = cache_key
        session["_engine_cache_text"] = result_text
        return result_text
    except Exception as e:
        import traceback
        logging.error("Engine fejl: %s", traceback.format_exc())
        return f"*(Engine-fejl: {type(e).__name__}: {e})*"


# ── Dynamisk kontekstblok (ændres pr. session/svar) ──────────────────────────

def _get_dynamic_context(session_id: str) -> str:
    session = sessions.get(session_id, {})
    profil_tekst = session.get("profil_tekst", "Ingen rapport uploadet endnu.")
    params = session.get("beregningsparametre", {})
    profil = session.get("profil") or {}

    tidligste = profil.get("tidligste_pensionsalder", "ukendt")

    if params:
        lines = [
            f"- Netto årlig indbetaling: {params.get('netto_indbetaling', 'ikke oplyst')} kr/år",
            f"- Forventet afkast efter PAL-skat: {params.get('afkast_pct', 4.0)}%",
            f"- Ønsket pensionsalder: {params.get('pensionsalder', 'ikke oplyst')} år",
            f"- Udbetalingsperiode: {params.get('udbetaling_aar', 30)} år",
            f"- Kommuneskat: {params.get('kommuneskat_pct', 'ikke oplyst')}%",
            f"- Kirkeskat: {params.get('kirkeskat_pct', 0.7)}%",
            f"- Status: {'Enlig' if params.get('enlig', True) else 'Par/samlevende'}",
        ]
        if params.get("inflation_pct"):
            lines.append(f"- Inflation (realværdi): {params['inflation_pct']}%")
        if params.get("produkt_start_aldre"):
            lines.append(f"- Per-produkt start-aldre: {params['produkt_start_aldre']}")
        parametre_tekst = "\n".join(lines)
    else:
        parametre_tekst = "Ikke indsamlet endnu — start interview."

    engine_tekst = _kør_engine(session_id)

    # Forsikringsanalyse
    forsikring_tekst = ""
    if profil:
        fa = analyser_forsikring(profil, params)
        lines = []
        for a in fa["advarsler"]:
            lines.append(f"⚠ {a}")
        for a in fa["analyser"]:
            lines.append(f"- {a}")
        if fa["estimer_bruttolonn"]:
            lines.append(f"- Estimeret bruttoløn (proxy): {fa['estimer_bruttolonn']:,.0f} kr/år".replace(",", "."))
        forsikring_tekst = "\n".join(lines)

    netto_indbetaling = params.get("netto_indbetaling")
    if not profil:
        spm6_fordeling = "*(Ingen rapport uploadet — Spørgsmål 6 ikke relevant)*"
    elif netto_indbetaling is None:
        spm6_fordeling = "*(Afventer spm 1: stil Spørgsmål 6 FØRST når brugerens samlede netto-indbetaling er oplyst)*"
    else:
        fordeling = fordel_pmt_default(profil, float(netto_indbetaling))
        spm6_fordeling = format_fordeling_til_llm(fordeling) if fordeling else \
            "*(Ingen multi-produkt firmapension fundet — Spørgsmål 6 ikke relevant)*"

    forsikring_blok = (f"## FORSIKRINGSANALYSE\n{forsikring_tekst}\n\n" if forsikring_tekst else "")

    fri_formue_blok = ""
    ff = (session.get("engine_output") or {}).get("fri_formue_analyse")
    if ff:
        fri_formue_blok = (
            f"## FRI FORMUE ANALYSE\n"
            f"Fri formue nu: {ff['fri_formue_nu']:,.0f} kr. | "
            f"Vækst til pension: {ff['fv_ved_pension']:,.0f} kr.\n"
            f"Brutto afkast: {ff['r_gross_pct']:.1f}% | Kapitalbeskatning: {ff['kapital_skat_pct']:.0f}% | "
            f"Netto afkast: {ff['r_net_pct']:.1f}%\n"
            f"Månedlig netto-udbetaling (annuitet over {ff['udbetaling_aar']} år): "
            f"{ff['mdr_netto']:,.0f} kr/mdr\n"
            f"(Fri formue beskattes som kapitalindkomst — ikke S-indkomst. "
            f"Tæller IKKE med i topskat-grundlag for pension.)\n\n"
        ).replace(",", ".")

    return (
        f"## TIDLIGSTE PENSIONSALDER\n{tidligste} år\n\n"
        + forsikring_blok
        + fri_formue_blok
        + f"## SPM6_FORDELING\n{spm6_fordeling}\n\n"
        + f"## BEREGNEDE PARAMETRE\n{parametre_tekst}\n\n"
        + f"## BEREGNET PENSIONSANALYSE\n{engine_tekst}\n\n"
        + f"## PENSIONSPROFIL\n{profil_tekst}\n"
    )


def get_system_prompt_blocks(session_id: str) -> list:
    """Returnerer system-prompt som blok-liste med prompt caching på den statiske del."""
    return [
        {
            "type": "text",
            "text": STATIC_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": _get_dynamic_context(session_id),
        },
    ]


# ── Historik pruning ──────────────────────────────────────────────────────────

def _prune_history(session: dict) -> None:
    msgs = session["messages"]
    if len(msgs) > MAX_HISTORY_MESSAGES:
        session["messages"] = msgs[:2] + msgs[-(MAX_HISTORY_MESSAGES - 2):]


# ── Ordningsforklaringer ──────────────────────────────────────────────────────

ORDNING_FORKLARINGER = {
    "kapitalpension": (
        "**Kapitalpension** er en gammel ordning lukket for nye indbetalinger siden 2013. "
        "Den udbetales som ét engangsbeløb. Normalt trækkes 40% i afgift ved udbetaling — "
        "men mange valgte i 2013 at forudbetale afgiften til en reduceret sats (37,3%) og "
        "konvertere ordningen. Hvis det er tilfældet hos dig, er den nu **afgiftsfri** ved udbetaling."
    ),
    "ratepension": (
        "**Ratepension** udbetales som løbende månedlige beløb over en fast periode (fx 10 eller 15 år). "
        "Beløbene beskattes som personlig indkomst. Der er et årligt loft på 63.100 kr. (2025) for nye indbetalinger."
    ),
    "aldersopsparing": (
        "**Aldersopsparing** er en skattefavoriseret opsparing: du får ikke fradrag for indbetalingerne, "
        "men til gengæld er udbetalingerne **skattefri** (kun PAL-skat på afkastet). "
        "Loft 9.100 kr./år normalt — op til 58.900 kr./år de sidste 5 år før pension."
    ),
    "livsvarig pension": (
        "**Livsvarig pension** udbetales som løbende månedlige beløb **så længe du lever** — "
        "du kan altså ikke løbe tør for penge. Beløbene beskattes som personlig indkomst. "
        "Særligt værdifuld hvis du lever længe."
    ),
    "livrente": (
        "**Livrente** udbetales livsvarigt som løbende månedlige beløb. Beskattes som personlig indkomst."
    ),
    "atp": (
        "**ATP Livslang Pension** er en lovpligtig statslig pension der starter ved folkepensionsalderen (tidligst 67 år). "
        "Beløbet er beskedent — typisk 1.500–2.500 kr./mdr. — og afhænger af hvor mange år du har arbejdet."
    ),
    "folkepension": (
        "**Folkepension** er den statslige pension alle danskere er berettiget til fra folkepensionsalderen (67 år og stigende). "
        "Grundbeløbet er ca. 7.955 kr./mdr. (2025) — hertil kan komme tillæg afhængigt af indkomst."
    ),
}


def build_trin0(profil: dict) -> str:
    """Bygger hele Trin 0 i Python: tabel + ordningsforklaringer. Ingen LLM."""
    tabel = build_oversigt_tabel(profil)

    ordninger = profil.get("ordninger", [])
    prod = profil.get("pensionsprodukter", [])
    typer_set = set()
    for o in ordninger:
        pt = (o.get("produkttype") or "").lower()
        for key in ORDNING_FORKLARINGER:
            if key in pt:
                typer_set.add(key)
    for p in prod:
        pt = (p.get("produkttype") or "").lower()
        for key in ORDNING_FORKLARINGER:
            if key in pt:
                typer_set.add(key)
    typer_set.add("atp")
    typer_set.add("folkepension")

    rækkefølge = ["kapitalpension", "ratepension", "aldersopsparing", "livsvarig pension", "livrente", "atp", "folkepension"]
    forklaringer = []
    for key in rækkefølge:
        if key in typer_set:
            forklaringer.append(ORDNING_FORKLARINGER[key])

    tekst = tabel + "\n\n---\n\n**Hvad betyder dine ordninger?**\n\n"
    tekst += "\n\n".join(forklaringer)
    return tekst


def build_sporgsmaal1(profil: dict) -> str:
    """Bygger Spørgsmål 1 i Python — ingen LLM."""
    samlet = profil.get("samlet_aarlig_indbetaling", 0)
    if samlet:
        mdr = round(samlet / 12)
        hint = (
            f"*(Rapporten viser ca. {mdr:,.0f} kr/mdr i indbetalinger inkl. forsikringspræmier og ATP. "
            "En del heraf går til forsikringsdækning — "
            "hvad forventer du at indbetale **netto til selve opsparingen** per måned?)*"
        ).replace(",", ".")
    else:
        hint = "*(Hvad forventer du at indbetale netto til selve pensionsopsparingen per måned?)*"
    return f"**Spørgsmål 1:** Hvad er din forventede **månedlige netto-indbetaling** til pensionsopsparing?\n\n{hint}"


def build_oversigt_tabel(profil: dict) -> str:
    """Bygger oversigtstabel: Selskab | Produkttype | Saldo | Bemærkning."""
    ord_  = profil.get("ordninger", [])
    prods = profil.get("pensionsprodukter", [])

    prod_by_nr: dict[tuple, list] = {}
    prod_by_nr_alone: dict[str, list] = {}
    for p in prods:
        nr  = str(p.get("aftalenr") or "")
        prv = str(p.get("selskab") or "")
        if nr:
            prod_by_nr.setdefault((nr, prv), []).append(p)
            prod_by_nr_alone.setdefault(nr, []).append(p)

    def get_prods(nr: str, selskab: str) -> list:
        exact = prod_by_nr.get((nr, selskab), [])
        if exact:
            return exact
        all_for_nr = prod_by_nr_alone.get(nr, [])
        providers = {str(p.get("selskab") or "") for p in all_for_nr}
        if len(providers) <= 1:
            return all_for_nr
        return [p for p in all_for_nr if not p.get("selskab")]

    def varighed(aldersperioder: dict) -> str:
        if not aldersperioder:
            return ""
        paying = {k: v for k, v in aldersperioder.items() if v}
        if not paying:
            return ""
        if any("Fra " in k for k in paying):
            return "livsvarig"
        years = 0
        for k in paying:
            if re.match(r"^\d+ år$", k):
                years += 1
            elif m := re.match(r"^(\d+)-(\d+) år$", k):
                years += int(m.group(2)) - int(m.group(1)) + 1
        return f"ca. {years} år" if years else ""

    def beм(ptype: str, aldersperioder: dict | None = None, kort: bool = False) -> str:
        pt = (ptype or "").lower()
        if "kapitalpension" in pt:
            return "Engangsbeløb — 40% afgift (evt. forudbetalt i 2013)"
        if "ratepension" in pt:
            if kort:
                return "Ratepension — udbetaling over en årrække, beskattes som indkomst"
            v = varighed(aldersperioder or {})
            dur = f" ({v})" if v else ""
            return f"Tidsbegrænset månedlig udbetaling{dur} — beskattes som indkomst"
        if "livsvarig" in pt or "livrente" in pt:
            if kort:
                return "Livsvarig pension — udbetaling så længe du lever, beskattes som indkomst"
            return "Månedlig udbetaling så længe du lever — beskattes som indkomst"
        if "aldersopsparing" in pt:
            if kort:
                return "Aldersopsparing — skattefri udbetaling"
            v = varighed(aldersperioder or {})
            dur = f" ({v})" if v and v != "livsvarig" else ""
            return f"Skattefri udbetaling{dur}"
        return ""

    private_rækker = []
    total_opsp = 0

    for a in ord_:
        if a.get("kun_forsikring"):
            continue
        selskab = a.get("selskab") or "?"
        if "ATP" in selskab:
            continue
        opsp = a.get("opsparing")
        if not opsp:
            continue
        ptype = a.get("produkttype") or ""
        nr    = a.get("aftalenr") or ""
        total_opsp += opsp
        saldo_s = f"{opsp:,.0f} kr.".replace(",", ".")

        sub = get_prods(nr, selskab)
        if len(sub) > 1:
            private_rækker.append(
                f"| {selskab} | **Firmapension** (samlet) | **{saldo_s}** | Samlet saldo for {len(sub)} produkter |"
            )
            for sp in sub:
                spt = sp.get("produkttype") or ""
                per = sp.get("aldersperioder") or {}
                sub_opsp = sp.get("opsparing")
                sub_saldo_s = f"{sub_opsp:,.0f} kr.".replace(",", ".") if sub_opsp else "—"
                private_rækker.append(f"| {selskab} |   ↳ {spt} | {sub_saldo_s} | {beм(spt, per, kort=True)} |")
        else:
            sub1 = get_prods(nr, selskab)
            per1 = sub1[0].get("aldersperioder", {}) if sub1 else {}
            inv = a.get("investeringsform") or ""
            display_ptype = inv if inv and inv.lower() not in (ptype or "").lower() and ptype.lower() not in inv.lower() else ptype
            private_rækker.append(f"| {selskab} | {display_ptype} | {saldo_s} | {beм(ptype, per1)} |")

    atp_prod = next((p for p in prods if "ATP" in (p.get("selskab") or "")), None)
    if atp_prod:
        per = atp_prod.get("aldersperioder") or {}
        start_periode = next((k for k, v in per.items() if v), None)
        start_beloeb  = per.get(start_periode, 0) if start_periode else 0
        mdr = round(start_beloeb / 12) if start_beloeb else 1825
        atp_bem = f"Livsvarig ydelse fra {start_periode} — ca. {mdr:,.0f} kr/mdr".replace(",", ".")
    else:
        atp_bem = "Livsvarig ydelse fra folkepensionsalderen — ca. 1.825 kr/mdr"

    total_s = f"{total_opsp:,.0f} kr.".replace(",", ".")

    rækker  = private_rækker
    rækker += [
        f"| ATP | ATP Livslang Pension | — | {atp_bem} |",
        "| Staten | Folkepension | — | Statslig grundydelse fra folkepensionsalderen — ca. 7.955 kr/mdr (2025) |",
        f"| I alt | | {total_s} | |",
    ]

    tabel  = "**Dine pensionsordninger**\n\n"
    tabel += "| Selskab | Produkttype | Saldo | Bemærkning |\n"
    tabel += "|---|---|---|---|\n"
    tabel += "\n".join(rækker)
    return tabel


# ─── API ENDPOINTS ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/version")
async def get_version():
    import subprocess
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent
        ).decode().strip()
    except Exception:
        commit = "ukendt"
    return {"commit": commit}


@app.post("/api/session")
async def create_session():
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "messages": [],
        "profil": None,
        "profil_tekst": "",
        "beregningsparametre": {},
    }
    return {"session_id": session_id}


@app.post("/api/upload/{session_id}")
async def upload_rapport(session_id: str, file: UploadFile = File(...)):
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")

    if not (file.filename or "").endswith(".pdf"):
        raise HTTPException(400, "Kun PDF-filer accepteres")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"Filen er for stor (max {MAX_UPLOAD_BYTES // 1_000_000} MB)")
        tmp.write(content)
        tmp_path = tmp.name

    try:
        profil = await parse_pensionsinfo_pdf(tmp_path)
        profil_tekst = format_profil_til_tekst(profil)

        sessions[session_id]["profil"] = profil
        sessions[session_id]["profil_tekst"] = profil_tekst

        # Ekskludér rå tekst og CPR-holdigt raw-objekt fra client-response
        profil_response = {k: v for k, v in profil.items() if k not in ("raa_tekst", "raw")}
        if "person" in profil_response:
            profil_response["person"] = {
                k: v for k, v in profil_response["person"].items()
                if k != "foedselsdato"
            }

        trin0 = build_trin0(profil)
        sporgsmaal1 = build_sporgsmaal1(profil)

        sessions[session_id]["messages"] = []
        sessions[session_id]["beregningsparametre"] = {}

        return {
            "success": True,
            "navn": profil["person"].get("navn", ""),
            "profil_tekst": profil_tekst,
            "profil": profil_response,
            "trin0": trin0,
            "sporgsmaal1": sporgsmaal1,
            "start_interview": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logging.error("Upload fejl for session %s: %s", session_id, traceback.format_exc())
        raise HTTPException(500, detail=f"Fejl ved indlæsning af rapport: {type(e).__name__}: {e}")
    finally:
        os.unlink(tmp_path)


class ChatMessage(BaseModel):
    session_id: str
    message: str


def _parse_mine_svar(message: str, session: dict) -> None:
    """Udtræk parametre direkte fra 'Mine svar:'-besked og gem i session."""
    if not message.startswith("Mine svar:"):
        return
    p = session["beregningsparametre"]

    def _num(pattern, text, cast=float):
        m = re.search(pattern, text)
        if m:
            try:
                return cast(m.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                pass
        return None

    v = _num(r"1\.\s*Netto indbetaling:\s*([\d.,]+)", message)
    if v is not None: p["netto_indbetaling"] = v

    v = _num(r"2\.\s*Forventet afkast:\s*([\d.,]+)", message)
    if v is not None: p["afkast_pct"] = v

    v = _num(r"3\.\s*Pensionsalder:\s*(\d+)", message, int)
    if v is not None: p["pensionsalder"] = v

    v = _num(r"4\.\s*Udbetalingsperiode:\s*(\d+)", message, int)
    if v is not None: p["udbetaling_aar"] = v

    m = re.search(r"7\.\s*Kommune:\s*([^\n]+)", message)
    if m and "kommuneskat_pct" not in p:
        kommune = m.group(1).strip()
        p["kommuneskat_pct"] = KOMMUNESKAT_2025.get(_normalize_kommune(kommune), 25.0)

    v = _num(r"2b\.\s*Inflation:\s*([\d.,]+)", message)
    if v is not None: p["inflation_pct"] = v

    v = _num(r"7b\.\s*Kirkeskat:\s*([\d.,]+)", message)
    if v is not None: p["kirkeskat_pct"] = v


@app.post("/api/chat")
async def chat(req: ChatMessage):
    if req.session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")

    session = sessions[req.session_id]
    session["messages"].append({"role": "user", "content": req.message})
    _prune_history(session)
    _parse_mine_svar(req.message, session)

    client = anthropic.AsyncAnthropic()

    async def stream_response():
        full_response = ""
        try:
            async with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=8000,
                system=get_system_prompt_blocks(req.session_id),
                messages=session["messages"],
            ) as stream:
                async for text in stream.text_stream:
                    full_response += text
                    yield f"data: {json.dumps({'text': text})}\n\n"

            session["messages"].append({"role": "assistant", "content": full_response})
            yield f"data: {json.dumps({'done': True})}\n\n"

        except anthropic.AuthenticationError:
            yield f"data: {json.dumps({'error': 'API-nøgle mangler eller er ugyldig. Sæt ANTHROPIC_API_KEY som miljøvariabel.'})}\n\n"
        except anthropic.APIStatusError as e:
            if e.status_code == 529 or "overloaded" in str(e).lower():
                yield f"data: {json.dumps({'error': 'overloaded'})}\n\n"
            else:
                logging.error("API fejl i chat: %s", e)
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        except Exception as e:
            logging.error("Chat fejl: %s", e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class Parametre(BaseModel):
    session_id: str
    netto_indbetaling: float | None = None
    afkast_pct: float | None = None
    pensionsalder: int | None = None
    udbetaling_aar: int | None = None
    kommuneskat_pct: float | None = None
    enlig: bool | None = None
    saldi_overrides: list | None = None
    kapital_skat_type: str | None = None
    inflation_pct: float | None = None
    kirkeskat_pct: float | None = None
    produkt_start_aldre: dict | None = None
    loenvaekst_pct: float | None = None
    fri_formue: float | None = None
    fri_formue_kapital_skat_pct: float | None = None


@app.post("/api/parametre")
async def gem_parametre(req: Parametre):
    if req.session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    p = sessions[req.session_id]["beregningsparametre"]
    if req.netto_indbetaling is not None:    p["netto_indbetaling"] = req.netto_indbetaling
    if req.afkast_pct is not None:           p["afkast_pct"] = req.afkast_pct
    if req.pensionsalder is not None:        p["pensionsalder"] = req.pensionsalder
    if req.udbetaling_aar is not None:       p["udbetaling_aar"] = req.udbetaling_aar
    if req.kommuneskat_pct is not None:      p["kommuneskat_pct"] = req.kommuneskat_pct
    if req.enlig is not None:                p["enlig"] = req.enlig
    if req.kapital_skat_type is not None:    p["kapital_skat_type"] = req.kapital_skat_type
    if req.inflation_pct is not None:        p["inflation_pct"] = req.inflation_pct
    if req.kirkeskat_pct is not None:        p["kirkeskat_pct"] = req.kirkeskat_pct
    if req.produkt_start_aldre is not None:  p["produkt_start_aldre"] = req.produkt_start_aldre
    if req.loenvaekst_pct is not None:        p["loenvaekst_pct"] = req.loenvaekst_pct
    if req.fri_formue is not None:                    p["fri_formue"] = req.fri_formue
    if req.fri_formue_kapital_skat_pct is not None:   p["fri_formue_kapital_skat_pct"] = req.fri_formue_kapital_skat_pct

    if req.saldi_overrides:
        profil = sessions[req.session_id].get("profil")
        if profil:
            for ov in req.saldi_overrides:
                nr    = str(ov.get("aftalenr") or "")
                ptype = (ov.get("produkttype") or "").lower()
                saldo = float(ov.get("saldo") or 0)
                for pp in profil.get("pensionsprodukter", []):
                    if (str(pp.get("aftalenr") or "") == nr and
                            (pp.get("produkttype") or "").lower() == ptype):
                        pp["opsparing"] = saldo
                for o in profil.get("ordninger", []):
                    if (str(o.get("aftalenr") or "") == nr and
                            (o.get("produkttype") or "").lower() == ptype):
                        o["opsparing"] = saldo
            sessions[req.session_id]["profil_tekst"] = format_profil_til_tekst(profil)

    return {"success": True, "parametre": p}


@app.get("/api/session/{session_id}/history")
async def get_history(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    return {"messages": sessions[session_id]["messages"]}

@app.get("/api/session/{session_id}/engine")
async def get_engine_data(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    result = sessions[session_id].get("engine_output")
    if not result:
        return JSONResponse({"error": "Ingen beregning endnu"}, status_code=404)
    params = sessions[session_id].get("beregningsparametre", {})
    # Return only what's needed for charting (avoid huge nested objects)
    return JSONResponse({
        "tabel": result["tabel"],
        "produkter": result["produkter"],
        "engangsbeloeb": result.get("engangsbeloeb", []),
        "fp_alder": result["fp_alder"],
        "pensionsalder": result["pensionsalder"],
        "tabel_start": result["tabel_start"],
        "parametre": result["parametre"],
        "jaevn_netto_mdr": result.get("jaevn_netto_mdr", 0),
        "produkt_start_aldre": params.get("produkt_start_aldre", {}),
        "fri_formue_analyse": result.get("fri_formue_analyse"),
    })



@app.get("/api/session/{session_id}/raw")
async def get_raw_extraction(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    profil = sessions[session_id].get("profil") or {}
    return JSONResponse(profil.get("raw", {}))


@app.get("/api/session/{session_id}/text")
async def get_pdf_text(session_id: str, q: str = ""):
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    profil = sessions[session_id].get("profil") or {}
    tekst = profil.get("raa_tekst", "")
    if q:
        lines = tekst.split("\n")
        match_idx = [i for i, l in enumerate(lines) if q.lower() in l.lower()]
        result = []
        for i in match_idx:
            result.append(f"--- linje {i} ---")
            result.append("\n".join(lines[max(0, i-3):i+4]))
        tekst = "\n".join(result) if result else f"(ingen match for '{q}')"
    return PlainTextResponse(tekst)


class InjectMessage(BaseModel):
    role: str
    content: str


@app.post("/api/session/{session_id}/inject")
async def inject_message(session_id: str, req: InjectMessage):
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    sessions[session_id]["messages"].append({"role": req.role, "content": req.content})
    return {"success": True}


@app.delete("/api/session/{session_id}/history")
async def clear_history(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    sessions[session_id]["messages"] = []
    return {"success": True}


@app.get("/")
async def root():
    html_path = static_path / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Frontend mangler</h1>")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    uvicorn.run("app:app", host=host, port=port, reload=not os.environ.get("PORT"))
