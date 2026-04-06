"""
PensionsAnalytiker - FastAPI backend med streaming chat.
"""

import os
import re
import json
import uuid
import tempfile
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import logging
import warnings

load_dotenv(Path(__file__).parent / ".env")

# Supprimér støjende PDF-parser advarsler
logging.getLogger("pdfminer").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*FontBBox.*")
warnings.filterwarnings("ignore", message=".*gray.*color.*")

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import anthropic

from pdf_parser import parse_pensionsinfo_pdf, format_profil_til_tekst
from pension_rules import PENSION_REGLER
from engine import generer_udbetalingstabel, format_engine_til_llm, SkatParametre

app = FastAPI(title="PensionsAnalytiker")

# Servér statiske filer
static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)

# In-memory session store (brug Redis i produktion)
sessions: dict[str, dict] = {}

SYSTEM_PROMPT = """Du er en dansk pensionsanalytiker-assistent. Du analyserer brugerens pensionsoplysninger \
fra PensionsInfo og giver konkrete, personlige anbefalinger.

## GENERELLE REGLER
- Svar ALTID på dansk
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

**Brugeren kan svare på ét eller alle spørgsmål på én gang.**
Udled alle svar der er givet — stil KUN de spørgsmål der mangler svar.
Har brugeren svaret på alle relevante spørgsmål i én besked: gå DIREKTE til den komplette analyse.

**Spørgsmål 2:** Forventet **årligt afkast** efter PAL-skat? (standard 4% — skriv "ok" for at bekræfte)

**Spørgsmål 3:** Hvornår vil du **gå på pension**? (tidligst muligt: {tidligste_pensionsalder} år)

**Spørgsmål 4:** Over hvor mange **udbetalingsår**? (standard 30 år — skriv "ok" for at bekræfte)

**Spørgsmål 5** — stil **kun hvis der er en Kapitalpension i profilen:**
"Din kapitalpension — er afgiften allerede betalt (konverteret i 2013 til reduceret sats på 37,3%)?"
- Svar **ja**: kapitalpensionen er afgiftsfri (F) — ingen yderligere skat ved udbetaling. Brug F i tabellen, beregn fuldt beløb.
- Svar **nej**: kapitalpensionen er afgiftspligtig (A) — 40% afgift ved udbetaling. Nettoudbetaling = beløb × 0,60.
- Standardantagelse hvis ikke spurgt: A (40% afgift).

**Spørgsmål 6** — stil **kun hvis der er en firmapension med flere produkttyper (fx Velliv med Ratepension + Livsvarig + Aldersopsparing):**
Vis først de kendte nuværende saldi per produkt, og præsentér derefter den back-beregnede PMT-fordeling som standard:
"Din [selskab] firmapension har følgende nuværende saldi:
  – [Produkttype 1]: [saldo] kr.
  – [Produkttype 2]: [saldo] kr.
  – [Produkttype 3]: [saldo] kr.
  Samlet indbetaling er [total] kr/år. Ud fra rapporten estimeres fordelingen til:
  – [Produkttype 1]: ca. [X] kr/år
  – [Produkttype 2]: ca. [Y] kr/år
  – [Produkttype 3]: ca. [Z] kr/år
  Jeg bruger disse estimater — skriv 'ok' for at bekræfte, eller opgiv den præcise fordeling."
- Standardantagelse: brug de estimerede beløb direkte uden at vente på svar, medmindre de virker åbenlyst forkerte.

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

## BEREGNINGSREGLER — ABSOLUT FORBUD MOD EGNE BEREGNINGER

⚠️ KRITISK: Du må ALDRIG præsentere et beregnet tal der ikke er hentet direkte fra "BEREGNET PENSIONSANALYSE" nedenfor.
Dette gælder uanset om brugeren beder om det, og uanset om du tror du ved svaret.
Du har IKKE adgang til korrekte skatteberegninger — engine'en har. Brug KUN engine-tal.

**Hvis "BEREGNET PENSIONSANALYSE" er tilgængelig:**
- Tallene i engine-output ER beregnet med brugerens valgte parametre — de er korrekte. Præsentér dem direkte.
- MÅ IKKE kommentere på om parametrene "passer" eller "afviger" — de er hvad brugeren valgte.
- Kopiér Tabel 1 og Tabel 2 direkte fra engine-output — ret ingen tal
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
Brug tallene fra "BEREGNET PENSIONSANALYSE" nedenfor.

**Tabel 1**: FV og månedlig udbetaling per produkt — vis som angivet.

**Tabel 2** — fast kolonnestruktur (rekonstruér ALLE rækker):
| Alder | [produkt kr/år] | Folkepension kr/år | ATP kr/år | Brutto/år | Netto/mdr | Note |
- Alle produktkolonner viser brutto kr/år
- Brutto/år = sum af alle produktkolonner
- Netto/mdr = samlet netto månedligt efter AM-bidrag, indkomstskat og topskat
- Note: "Topskat" hvis samlet PI > 588.900 kr; "Modregning -X kr" hvis pensionstillæg reduceres; ellers "—"
- Folkepension og ATP vises som "—" inden folkepensionsalderen
- Engangsbeløb vises kun i Tabel 1

{engine_analyse}

## BRUGERENS BEREGNEDE PARAMETRE
{parametre}

## NØGLESATSER 2025
- Ratepension loft: 63.100 kr/år | Aldersopsparing: 9.100 kr/år (58.900 kr under 5 år til pension)
- AM-bidrag: 8% | Bundskat: 12,01% | Topskatgrænse: 588.900 kr PI | Topskat: 15%
- Folkepension: 7.955 kr/mdr | ATP: ca. 1.825 kr/mdr | PAL-skat: 15,3%
- Pensionstillæg max (enlig): 8.891 kr/mdr — modregnes 30,9% af S-indkomst over 98.400 kr/år

## BRUGERENS PENSIONSPROFIL
{profil}
"""


def _kør_engine(session_id: str) -> str:
    """Kør deterministisk beregning hvis pensionsalder er oplyst. Returnerer formateret tekst."""
    session = sessions.get(session_id, {})
    params  = session.get("beregningsparametre", {})
    profil  = session.get("profil")

    if not profil or not params.get("pensionsalder"):
        return "*(Ikke beregnet endnu — venter på pensionsalder fra spørgsmål 3)*"

    try:
        skat = SkatParametre.fra_pct(
            kommuneskat_pct=float(params.get("kommuneskat_pct", 25.0)),
            enlig=bool(params.get("enlig", True)),
        )
        result = generer_udbetalingstabel(profil, params, skat)
        session["engine_output"] = result
        return format_engine_til_llm(result)
    except Exception as e:
        import traceback
        return f"*(Engine-fejl: {type(e).__name__}: {e})*\n\n```\n{traceback.format_exc()}\n```"


def get_system_prompt(session_id: str) -> str:
    session = sessions.get(session_id, {})
    profil_tekst = session.get("profil_tekst", "Ingen rapport uploadet endnu.")
    params = session.get("beregningsparametre", {})
    profil = session.get("profil") or {}

    tidligste = profil.get("tidligste_pensionsalder", "ukendt")

    # Formater indsamlede parametre
    if params:
        parametre_tekst = "\n".join([
            f"- Netto årlig indbetaling: {params.get('netto_indbetaling', 'ikke oplyst')} kr/år",
            f"- Forventet afkast efter PAL-skat: {params.get('afkast_pct', 4.0)}%",
            f"- Ønsket pensionsalder: {params.get('pensionsalder', 'ikke oplyst')} år",
            f"- Udbetalingsperiode: {params.get('udbetaling_aar', 30)} år",
            f"- Kommuneskat: {params.get('kommuneskat_pct', 'ikke oplyst')}%",
            f"- Status: {'Enlig' if params.get('enlig', True) else 'Par/samlevende'}",
        ])
    else:
        parametre_tekst = "Ikke indsamlet endnu — start interview."

    engine_tekst = _kør_engine(session_id)

    return SYSTEM_PROMPT.format(
        profil=profil_tekst,
        parametre=parametre_tekst,
        engine_analyse=engine_tekst,
        tidligste_pensionsalder=tidligste,
    )


# ─── API ENDPOINTS ───────────────────────────────────────────────────────────

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

    # Find hvilke ordningstyper brugeren har
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
    # ATP og Folkepension inkluderes altid
    typer_set.add("atp")
    typer_set.add("folkepension")

    # Byg forklaringstekst i fast rækkefølge
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
        hint = (
            f"*(Rapporten viser {samlet:,.0f} kr/år i indbetalinger fordelt på alle aftaler "
            "inkl. forsikringspræmier og ATP. En del heraf går til forsikringsdækning — "
            "hvad forventer du at indbetale **netto til selve opsparingen** fremover?)*"
        ).replace(",", ".")
    else:
        hint = "*(Hvad forventer du at indbetale netto til selve pensionsopsparingen fremover?)*"
    return f"**Spørgsmål 1:** Hvad er din forventede **netto årlige indbetaling** til pensionsopsparing?\n\n{hint}"


def build_oversigt_tabel(profil: dict) -> str:
    """Bygger oversigtstabel: Selskab | Produkttype | Saldo | Bemærkning.
    Private ordninger øverst, ATP + Folkepension nederst.
    Aftaler med flere payout-produkter foldes ud som sub-rækker.
    """
    ord_  = profil.get("ordninger", [])
    prods = profil.get("pensionsprodukter", [])

    # Byg index: aftalenr → liste af payout-produkter
    prod_by_nr: dict[str, list] = {}
    for p in prods:
        nr = str(p.get("aftalenr") or "")
        if nr:
            prod_by_nr.setdefault(nr, []).append(p)

    def varighed(aldersperioder: dict) -> str:
        """Beregn omtrentlig udbetalingsperiode fra payout-perioder."""
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
        """kort=True bruges til sub-rækker: kortere tekst uden antal år."""
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
            continue                          # ATP håndteres separat nederst
        opsp = a.get("opsparing")
        if not opsp:
            continue
        ptype = a.get("produkttype") or ""
        nr    = a.get("aftalenr") or ""
        total_opsp += opsp
        saldo_s = f"{opsp:,.0f} kr.".replace(",", ".")

        sub = prod_by_nr.get(nr, [])
        # Fold ud hvis der er mere end ét payout-produkt under samme aftalenr
        if len(sub) > 1:
            private_rækker.append(
                f"| {selskab} | **Firmapension** (samlet) | **{saldo_s}** | Samlet saldo for {len(sub)} produkter |"
            )
            for sp in sub:
                spt = sp.get("produkttype") or ""
                per = sp.get("aldersperioder") or {}
                sub_opsp = sp.get("opsparing")
                sub_saldo_s = f"{sub_opsp:,.0f} kr.".replace(",", ".") if sub_opsp else "—"
                private_rækker.append(f"| {selskab} | \u00a0\u00a0↳ {spt} | {sub_saldo_s} | {beм(spt, per, kort=True)} |")
        else:
            # Find payout-perioder for enkelt produkt
            sub1 = prod_by_nr.get(nr, [])
            per1 = sub1[0].get("aldersperioder", {}) if sub1 else {}
            private_rækker.append(f"| {selskab} | {ptype} | {saldo_s} | {beм(ptype, per1)} |")

    # ── ATP — find beløb og startperiode fra payout_products ──
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


@app.post("/api/upload/{session_id}")
async def upload_rapport(session_id: str, file: UploadFile = File(...)):
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")

    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Kun PDF-filer accepteres")

    # Gem midlertidigt og parser
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        profil = parse_pensionsinfo_pdf(tmp_path)
        profil_tekst = format_profil_til_tekst(profil)

        sessions[session_id]["profil"] = profil
        sessions[session_id]["profil_tekst"] = profil_tekst

        # Fjern rå tekst fra response (for stor)
        profil_response = {k: v for k, v in profil.items() if k != "raa_tekst"}

        # Byg hele Trin 0 i Python (tabel + forklaringer)
        trin0 = build_trin0(profil)
        sporgsmaal1 = build_sporgsmaal1(profil)

        # Nulstil samtale og parametre ved ny rapport
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
    except Exception as e:
        import traceback
        raise HTTPException(500, detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
    finally:
        os.unlink(tmp_path)


class ChatMessage(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat")
async def chat(req: ChatMessage):
    if req.session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")

    session = sessions[req.session_id]
    session["messages"].append({"role": "user", "content": req.message})

    client = anthropic.Anthropic()

    async def stream_response():
        full_response = ""
        try:
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=8000,
                system=get_system_prompt(req.session_id),
                messages=session["messages"],
            ) as stream:
                for text in stream.text_stream:
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
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        except Exception as e:
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
    saldi_overrides: list | None = None  # [{aftalenr, produkttype, saldo}]


@app.post("/api/parametre")
async def gem_parametre(req: Parametre):
    if req.session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    p = sessions[req.session_id]["beregningsparametre"]
    if req.netto_indbetaling is not None: p["netto_indbetaling"] = req.netto_indbetaling
    if req.afkast_pct is not None:        p["afkast_pct"] = req.afkast_pct
    if req.pensionsalder is not None:     p["pensionsalder"] = req.pensionsalder
    if req.udbetaling_aar is not None:    p["udbetaling_aar"] = req.udbetaling_aar
    if req.kommuneskat_pct is not None:   p["kommuneskat_pct"] = req.kommuneskat_pct
    if req.enlig is not None:             p["enlig"] = req.enlig

    # Opdatér saldi i profilen så engine regner på aktuelle værdier (ikke PDF-saldi)
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
            # Regenerér profil_tekst med opdaterede saldi
            sessions[req.session_id]["profil_tekst"] = format_profil_til_tekst(profil)

    return {"success": True, "parametre": p}


@app.get("/api/session/{session_id}/history")
async def get_history(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    return {"messages": sessions[session_id]["messages"]}


@app.get("/api/session/{session_id}/raw")
async def get_raw_extraction(session_id: str):
    """Returnerer den rå LLM-ekstraktion til debugging."""
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    profil = sessions[session_id].get("profil") or {}
    return JSONResponse(profil.get("raw", {}))


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


# Servér frontend
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
