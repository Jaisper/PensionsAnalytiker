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
from datetime import date
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
                     analyser_forsikring, beregn_fri_formue_tabel, skat_params_fra_parametre,
                     civilstand_fra_parametre)
import sekventering
import husstand

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
- Tilpas ALTID sprogbrug, tempo og forklaringsdybde til brugerens valgte FORKLARINGSNIVEAU (Pensionslix) — se kontekstblokken for det gældende niveau og præcis hvordan det skal omsætte til din formulering, herunder Tabel 1-3 og især forklaring af en foreslået optimal udbetalingsrækkefølge

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
| Alder | [produkt kr/år] | Folkepension kr/år | ATP kr/år | Brutto/år | Netto/mdr | [Real/mdr] | [Engangsbeløb netto] | Note |
- Alle produktkolonner viser brutto kr/år
- Engangsbeløb netto-kolonnen vises KUN hvis engine-outputtet har den (der er mindst ét engangsbeløb-produkt) — ellers udelades den helt
- Engangsbeløb netto ligger sidst, lige før Note — IKKE mellem produktkolonnerne og Brutto/år.
  Det er bevidst: Brutto/år og Netto/mdr ligger dermed lige efter produktkolonnerne, så det er
  tydeligt at de kun summerer produkterne + Folkepension + ATP — IKKE engangsbeløbet.
- Brutto/år = sum af alle produktkolonner (Folkepension + ATP) — IKKE engangsbeløb
- Netto/mdr = samlet netto månedligt efter AM-bidrag, indkomstskat og topskat — IKKE engangsbeløb
- Engangsbeløb-kolonnen viser beløbet KUN i det år det udbetales (—/tomt i alle andre år) og må ALDRIG lægges til Brutto/år eller Netto/mdr
- Note: "Mellem-/topskat" hvis samlet PI > 641.200 kr; "Modregning -X kr" hvis pensionstillæg reduceres; "Tillægsprocent X%" hvis under 100; "+ældrecheck/mediecheck X kr/mdr" hvis ydelser udbetales; ellers "—"
- Folkepension og ATP vises som "—" inden folkepensionsalderen
- Engangsbeløb vises i BÅDE Tabel 1 (som samlet FV/netto) og Tabel 2 (år-for-år, hvornår det udbetales) — aldrig kun i Tabel 1

**Skatteeksempel år 1** — vis ALTID afsnittet "SKATTEBEREGNING — EKSEMPEL ÅR 1" direkte efter Tabel 2, ord for ord som det står i engine-outputtet. Ingen udeladelser. Følg den derefter ALTID op med en forklaring i almindeligt sprog af hvad tabellen viser — tilpasset FORKLARINGSNIVEAU ligesom Tabel 1-3 (se kontekstblokken): på niveau 1-2, gå gennem posterne i rækkefølge (brutto → AM-bidrag trukket fra → personlig indkomst → bund-/kommune-/kirkeskat trukket fra → evt. mellem-/topskat → netto) og sig med almindelige ord HVORFOR hver post trækkes fra, ikke kun hvad den hedder — dette er typisk det tætteste brugeren kommer på at se "hvor bliver pengene af", så det skal være letforståeligt, ikke blot en gentagelse af tabellens tal.

**Tabel 4** — Scenarieanalyse: hvis tilgængelig, vis den direkte. Der er 4 scenarier, der hver varierer ÉN antagelse (afkast eller inflation) ±1 procentpoint fra brugerens EGEN valgte antagelse (den anden holdes fast) — ikke faste absolutte niveauer og ikke et "Base"-scenarie (det er allerede Tabel 1–3). Forklar at hvert scenarie isolerer én usikkerhed ad gangen.

Vis ALTID denne linje direkte efter Tabel 1 og Tabel 3: *Beregnet af deterministisk engine — konsultér en certificeret pensionsrådgiver for konkrete beslutninger.*

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

## PENSIONSTILLÆG VS. PERSONLIG TILLÆGSPROCENT — TO UAFHÆNGIGE SKALAER
Dette er let at forveksle — gør det ALDRIG:
- **Pensionstillæg**: bortfalder først ved ca. 438.200 kr/år i anden indkomst (enlig). Vises som "tillaeg_mdr" / "Modregning -X kr/mdr" i Tabel 2.
- **Personlig tillægsprocent** (0-100): en HELT ANDEN, langt strengere skala, der rammer nul allerede ved 99.200 kr/år (enlig) — mens pensionstillægget der stadig er fuldt intakt. Den styrer IKKE pensionstillægget, men derimod ældrecheck og mediecheck.
- En bruger kan altså miste hele ældrechecken (skjult marginalskat op til ca. 82%) i indkomstintervallet 35.700-99.200 kr, LÆNGE før pensionstillægget overhovedet begynder at blive ramt væsentligt.
- Formuegrænsen (108.000 kr likvid formue) for ældrecheck er en HÅRD tærskel — ingen glidende aftrapning. Én krone over grænsen fjerner hele beløbet.
- Præsenter derfor altid disse to reguleringer SEPARAT for brugeren, aldrig som "aftrapningen" i ental.

## CIVILSTAND OG PARTNER — TILVALGT, FORENKLET
Hvis brugeren har angivet civilstand "gift eller samlevende":
- Pensionstillægget bruger automatisk den korrekte gift-sats (32 % hvis kun brugeren er folkepensionist, 16 % hvis begge er det) i stedet for enlig-satsen — dette sker år for år, og skifter automatisk det år partneren selv når folkepensionsalderen.
- Hvis en partner-indkomst er angivet, tælles den KUN med i indtægtsgrundlaget i de år partneren selv er folkepensionist — IKKE mens partneren stadig er erhvervsaktiv. Gør altid klart for brugeren at dette er et **overslag**, ikke en juridisk præcis fælles-beregning: den fulde modregningsregel for gifte par med to indkomster er mere kompleks end dette værktøj modellerer.
- Der er IKKE regnet en selvstændig udbetalingsplan for partneren — kun brugerens egen tidslinje/produkter vises. Sig det tydeligt hvis brugeren spørger til partnerens egen pension.

Hvis der derimod findes en "## PARTNERENS UDBETALINGSANALYSE"-sektion i konteksten (partneren har uploadet sin egen rapport):
- Partnerens tal er nu en FULD, egen beregning — egne produkter, egen tidslinje, egen skat, OG egne økonomiske antagelser (afkast, inflation, kommune-/kirkeskat, udbetalingsperiode — sat via "Beregn partnerens analyse", helt uafhængigt af brugerens egne) — ikke længere et overslag. Præsenter den som en klart adskilt sektion, ligesom den fremstår i konteksten.
- Den ENESTE tilnærmelse der stadig gælder er selve samspils-koblingen: det kombinerede indtægtsgrundlag for pensionstillæg/tillægsprocent er stadig en forenklet tilnærmelse til § 29-reglerne — nævn det kort, men lad være med at gentage hele forbeholdet for hver eneste tabel.
- Udbetalingsdiagrammet viser nu BEGGE i et split-screen — brugerens egen kolonne ("Dig") og partnerens ("Partner") side om side, hver med sin egen uafhængige, trækbare tidslinje og uafhængig scroll, plus en samlet husstands-sum nederst. Nævn dette hvis brugeren spørger hvordan de justerer partnerens produkter — de trækker direkte i partnerens egen tidslinje, ligesom deres egen.
- Sekventeringsoptimeringen ("Find bedste udbetalingsrækkefølge") ser stadig KUN på brugerens egne produkter — den optimerer ikke partnerens tidslinje.

## SEKVENTERINGSOPTIMERING — "FIND BEDSTE UDBETALINGSRÆKKEFØLGE"
Når brugeren har brugt knappen/funktionen der finder den bedste udbetalingsrækkefølge:
- Målet er at MAKSIMERE det faste, inflationskorrigerede månedsbeløb brugeren kan leve af hele den ønskede periode (`jaevn_netto_mdr`) — IKKE at maksimere den samlede udbetalte sum. Præsenter resultatet som "det højeste faste månedsbeløb vi kunne finde", ikke som en garanti.
- Optimeringen kan ALDRIG anbefale en plan hvor de tidlige/laveste år bliver ringere end de ville have været uden indblanding — det er en indbygget grænse, ikke noget der skal forklares som en begrænsning.
- Resultatet er fundet ved at afprøve en lang række kombinationer af per-produkt start-aldre, ratepensioners udbetalingsperiode (min. 10 år, lovkrav), evt. udskudt folkepension, og udbetalingsår for engangsbeløb der indgår i bufferen — IKKE ved at forudse fremtiden.
- Et engangsbeløb brugeren aktivt har FRAVALGT bufferen for (udbetales direkte, ikke som buffer) rører optimeringen IKKE — det er brugerens eget, allerede trufne valg.
- Hvis resultatet er "målet kan ikke nås": sig det direkte og eksplicit — foreslå ALDRIG den næstbedste plan som var den en løsning, det ville modsige selve formålet med den hårde grænse.
- Hvis brugeren har fået foreslået udskudt folkepension (`folkepension_opsaettelse_aar > 0`): gør klart at ventetillægget (6 %/år) er et **forenklet skøn**, ikke den juridisk præcise ventetillægsberegning — den rigtige regel er mere kompleks.
- Nævn at optimeringen kun ser på brugerens egne produkter — en eventuel partners egen pension indgår ikke i søgningen.
- Hvis svaret indeholder `husstand_advarsel`: gengiv den TYDELIGT og fremhævet, ikke som en fodnote — den betyder at den foreslåede plan forbedrer brugerens EGET beløb, men gør husstandens SAMLEDE beløb ringere, fordi den ændrer partnerens pensionstillæg/tillægsprocent. Foreslå ALDRIG at bruge "Anvend denne plan" uden at nævne dette, hvis feltet er sat.

## NØGLESATSER 2026
- Ratepension loft: 68.700 kr/år | Aldersopsparing: 9.100 kr/år (60.900 kr under 7 år til folkepensionsalder, PBL §16)
- AM-bidrag: 8% | Bundskat: 12,01% | Personfradrag: ca. 49.700 kr/år (2025-niveau genbrugt som skøn — IKKE bekræftet mod Skatteministeriets 2026-tal, nævn dette hvis det efterspørges)
- Progressiv topskat (personlig indkomst, hvert trin har sit eget skatteloft jf. PSL §19, EKSKL. AM-bidrag og kirkeskat):
  mellemskat 7,5% over 641.200 kr (loft 44,57%) | topskat 7,5% over 777.900 kr (loft 52,07%) | top-topskat 5% over 2.592.700 kr (loft 57,07%)
- Folkepension: 7.544 kr/mdr | ATP: ca. 1.825 kr/mdr (begge i dagens takst — satsreguleres nominelt med den valgte inflationsantagelse hvert år frem, så deres KØBEKRAFT holdes konstant, i stedet for at blive udhulet over et 20-30-årigt forløb) | PAL-skat: 15,3%
- Pensionstillæg max (enlig): 104.748 kr/år — modregnes 30,9% af indtægtsgrundlag over 99.200 kr/år (gift: 53.604 kr/år, 32%/16% over 198.800 kr). Pensionstillægget er ALMINDELIG skattepligtig personlig indkomst ligesom grundbeløbet — det tal du ser er allerede efter skat, ikke et bruttobeløb.
- Ældrecheck: op til 26.900 kr/år (skattepligtig) | Mediecheck: uverificeret sats, nævn dette hvis den vises
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
        # _kør_engine falder tilbage til dette (chat-udledte) svar når feltet
        # ovenfor ikke er sat — uden det her ville et frit formuleret chat-svar
        # på spørgsmål 5 (fx "ja, den blev konverteret i 2013") ikke udløse
        # nogen genberegning, fordi selve param-feltet aldrig ændrer sig.
        "kapital_skat_historik": _kapital_skat_type_fra_historik(session),
        "inflation_pct":      params.get("inflation_pct"),
        # Manglede tidligere — primærens netto_indbetaling/engangs_buffer_skat_pct
        # ændrer det beregnede resultat lige så meget som partnerens tilsvarende
        # felter (som ALLEREDE er dækket nedenfor via _PARTNER_OVERRIDE_KEYS),
        # men blev ikke selv talt med, så en rettet indbetaling kunne stille
        # blive vist med det gamle tal efter "Opdater analyse".
        "netto_indbetaling":  params.get("netto_indbetaling"),
        "engangs_buffer_skat_pct": params.get("engangs_buffer_skat_pct"),
        # Se _anvend_saldi_overrides — saldi-rettelser muterer profilen IN
        # PLACE og ændrer derfor ikke id(profil)/id(profil_partner) nedenfor
        # alene; denne tæller er den faktiske invalideringskilde for dem.
        "saldi_version":      session.get("_saldi_version", 0),
        "produkt_start_aldre": json.dumps(params.get("produkt_start_aldre", {}), sort_keys=True),
        "produkt_i_buffer":   json.dumps(params.get("produkt_i_buffer", {}), sort_keys=True),
        "loenvaekst_pct":     params.get("loenvaekst_pct"),
        "fri_formue":         params.get("fri_formue"),
        "fri_formue_skat":    params.get("fri_formue_kapital_skat_pct"),
        "civilstand":         params.get("civilstand"),
        "partner_alder":      params.get("partner_alder"),
        "partner_indkomst_aar": params.get("partner_indkomst_aar"),
        "produkt_udb_aar":    json.dumps(params.get("produkt_udb_aar", {}), sort_keys=True),
        "folkepension_opsaettelse_aar": params.get("folkepension_opsaettelse_aar"),
        "partner_pensionsalder": params.get("partner_pensionsalder"),
        "partner_kapital_skat_type": params.get("partner_kapital_skat_type"),
        # saldi_overrides/partner_saldi_overrides selv er IKKE med her — de
        # bliver aldrig gemt som et param-felt, kun anvendt med det samme (se
        # gem_parametre) — men deres EFFEKT er dækket af "saldi_version"
        # ovenfor, som netop findes fordi de muterer profilen IN PLACE og
        # derfor ikke selv ændrer id(profil)/id(profil_partner) nedenfor.
        # De partner_*-overrides — samme liste _byg_params_partner() bruger,
        # så et felt der tæller for beregningen aldrig kan glemmes her (en
        # glemt cache-nøgle er en STILLE fejl: uændret hash -> gammel,
        # forældet analyse serveres uden nogen fejlmeddelelse nogen steder).
        **{
            k: (json.dumps(v, sort_keys=True) if isinstance(v, dict) else v)
            for k, v in ((k, params.get(k)) for k in _PARTNER_OVERRIDE_KEYS)
        },
        "profil_partner_id":  id(session.get("profil_partner")),
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


def _civilstand_fra_params(params: dict) -> str:
    return civilstand_fra_parametre(params)


_PARTNER_OVERRIDE_KEYS = (
    "partner_produkt_start_aldre", "partner_produkt_i_buffer", "partner_produkt_udb_aar",
    "partner_folkepension_opsaettelse_aar", "partner_afkast_pct", "partner_inflation_pct",
    "partner_udbetaling_aar", "partner_kommuneskat_pct", "partner_kirkeskat_pct",
    "partner_netto_indbetaling", "partner_fri_formue", "partner_fri_formue_kapital_skat_pct",
)
# Rene personlige facts om den PRIMÆRE bruger. Skal ALDRIG bruges som
# fallback-default for partnerens tilsvarende felt (i modsætning til fx
# afkast_pct, hvor det ER fornuftigt at partneren arver primærens antagelse
# indtil de sætter deres egen) — partneren har sin egen opsparing og formue,
# aldrig primærens. De har hver deres partner_*-override i
# _PARTNER_OVERRIDE_KEYS ovenfor; kun uden den (intet sat) gælder engine.py's
# egne 0-fallbacks. loenvaekst_pct har intet UI-spørgsmål og derfor ingen
# partner-override overhovedet. kapital_skat_type hører også kun til
# primæren — den har sin egen partner_kapital_skat_type (anvendt direkte i
# _kør_engine/optimer, uden om denne generiske liste, ligesom kapital_skat_type
# selv er) — uden den her ville partnerens params-kopi ellers arve primærens
# svar på Q5 som en "fornuftig default", hvilket er forkert for et rent
# personligt faktum (om PARTNERENS kapitalpension blev konverteret i 2013).
_PRIMÆR_KUN_KEYS = ("netto_indbetaling", "fri_formue", "fri_formue_kapital_skat_pct", "loenvaekst_pct", "kapital_skat_type")


def _anvend_saldi_overrides(profil: dict, overrides: list) -> None:
    """Overskriver saldi for navngivne produkter (aftalenr+type) i en profil —
    delt mellem primærens og partnerens Q6-saldi-override (samme match-logik,
    kun profilen der muteres er forskellig)."""
    for ov in overrides:
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


def _byg_params_partner(params: dict) -> dict | None:
    """Bygger partnerens beregningsparametre (Fase D husstandskobling) ud fra
    den primære brugers params — eller None hvis der ikke er en fuld
    partner-profil/civilstand/partner-pensionsalder til at understøtte det.
    Partnerens EGNE antagelser (afkast/inflation/kommune-/kirkeskat,
    udbetalingsperiode, produkt-tidslinje — split-screen-diagrammet) er
    deres egne, uafhængige valg, sat eksplicit via "Beregn partnerens
    analyse", og skal ALDRIG blande sig med primærens felter. Kun hvis et
    partner_*-felt IKKE er sat falder det tilbage til primærens egen værdi
    (bagudkompatibelt med det gamle, lette overslags-flow uden disse felter).
    Rene personlige facts om primæren (`_PRIMÆR_KUN_KEYS`) har derimod INGEN
    fallback-semantik og udelades altid — partneren har sin egen opsparing
    og formue, aldrig primærens.
    Brugt tre steder (_kør_engine, /api/optimer, /api/session/.../engine-
    partner) — holdt ét sted så de tre aldrig kan drifte fra hinanden."""
    civilstand = _civilstand_fra_params(params)
    if civilstand != "gift_samlevende" or not params.get("partner_pensionsalder"):
        return None
    params_partner = {
        k: v for k, v in params.items()
        if k not in ("produkt_start_aldre", "produkt_i_buffer", "produkt_udb_aar",
                     "folkepension_opsaettelse_aar", "civilstand", "partner_alder",
                     "partner_indkomst_aar", "partner_pensionsalder",
                     *_PARTNER_OVERRIDE_KEYS, *_PRIMÆR_KUN_KEYS)
    }
    params_partner["pensionsalder"] = int(params["partner_pensionsalder"])
    params_partner["civilstand"] = "gift_samlevende"
    for kilde_noegle in _PARTNER_OVERRIDE_KEYS:
        vaerdi = params.get(kilde_noegle)
        if vaerdi is not None:
            params_partner[kilde_noegle.removeprefix("partner_")] = vaerdi
    return params_partner


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

        def _tilfoej_fri_formue_analyse(result_person: dict, params_person: dict, alder_nu_person: int) -> None:
            fri_formue_person = params_person.get("fri_formue")
            if fri_formue_person and fri_formue_person > 0:
                result_person["fri_formue_analyse"] = beregn_fri_formue_tabel(
                    fri_formue=float(fri_formue_person),
                    r_gross=float(params_person.get("afkast_pct", 4.0)) / 100,
                    udbetaling_aar=int(params_person.get("udbetaling_aar", 30)),
                    pensionsalder=int(params_person["pensionsalder"]),
                    alder_nu=alder_nu_person,
                    kapital_skat_pct=float(params_person.get("fri_formue_kapital_skat_pct") or 33.0),
                )
            else:
                result_person["fri_formue_analyse"] = None

        profil_partner = session.get("profil_partner")
        result_text_partner = None
        params_partner = _byg_params_partner(params) if profil_partner else None
        if params_partner is not None:
            profil_partner_kopi = copy.deepcopy(profil_partner)
            partner_kapital_skat = params.get("partner_kapital_skat_type")
            if partner_kapital_skat:
                for p in profil_partner_kopi.get("pensionsprodukter", []):
                    if "kapital" in (p.get("produkttype") or "").lower():
                        p["skat_type"] = partner_kapital_skat
            husstand_resultat = husstand.beregn_husstand(
                profil_kopi, params, profil_partner_kopi, params_partner,
            )
            result = husstand_resultat["a"]
            session["engine_output_partner"] = husstand_resultat["b"]
            alder_nu_partner = int((profil_partner.get("person") or {}).get("alder") or 0)
            _tilfoej_fri_formue_analyse(husstand_resultat["b"], params_partner, alder_nu_partner)
            result_text_partner = format_engine_til_llm(husstand_resultat["b"])
            skat = None  # allerede anvendt inde i beregn_husstand
        else:
            # Ryd en evt. tidligere partner-beregning — ellers bliver
            # /api/session/.../engine-partner ved med at svare 200 med en
            # forældet plan efter fx civilstand er slået fra igen, og
            # split-screen-kolonnen/husstands-summen forbliver forkert synlig.
            session["engine_output_partner"] = None
            skat = skat_params_fra_parametre(params)
            result = generer_udbetalingstabel(profil_kopi, params, skat)
            result["scenarier"] = generer_scenarier(copy.deepcopy(profil_kopi), params, skat)
        alder_nu = int((session.get("profil") or {}).get("person", {}).get("alder") or 0)
        _tilfoej_fri_formue_analyse(result, params, alder_nu)
        session["engine_output"] = result
        result_text = format_engine_til_llm(result)
        if result_text_partner:
            result_text += (
                "\n\n---\n\n"
                "## PARTNERENS UDBETALINGSANALYSE\n"
                "*(Fuld, selvstændig beregning for partneren — samspils-koblingen (kombineret "
                "indtægtsgrundlag for pensionstillæg/tillægsprocent) er stadig en forenklet "
                "tilnærmelse til § 29-reglerne, ikke en juridisk præcis fælles-beregning.)*\n\n"
                + result_text_partner
            )
        session["_engine_cache_key"]  = cache_key
        session["_engine_cache_text"] = result_text
        return result_text
    except Exception as e:
        import traceback
        logging.error("Engine fejl: %s", traceback.format_exc())
        return f"*(Engine-fejl: {type(e).__name__}: {e})*"


# ── Dynamisk kontekstblok (ændres pr. session/svar) ──────────────────────────

PENSION_LIX_DEFAULT = 2

# "Pensionslix" — hvor meget kan brugeren om pension i forvejen, ikke hvor
# meget de forstår af verdenen i øvrigt. Renset for tal-niveau (det styres
# af selve beregningen, ikke af dette) og for høflighed (altid respektfuld,
# uanset niveau) — kun sprogbrug, tempo og hvor meget der skal forklares vs.
# antages kendt. Sat af brugeren selv, ikke gættet ud fra deres formulering.
_PENSION_LIX_NIVEAUER: dict[int, str] = {
    1: (
        "NY I PENSION — brugeren er ikke inde i det endnu og skal ikke føle sig dum over det.\n"
        "- Ingen fagudtryk uden at oversætte dem STRAKS til hverdagssprog i samme sætning "
        "(fx \"ratepension — en pensionsopsparing der udbetales over en periode, du selv har valgt\").\n"
        "- Undgå ord som 'modregning', 'tillægsprocent', 'skatteloft', 'personlig indkomst' helt, "
        "medmindre du samtidig forklarer dem med en hverdagsanalogi (\"jo mere du selv tjener ved siden af, "
        "jo mindre topper staten op med\").\n"
        "- Korte sætninger. Én pointe ad gangen. Ingen lange tabel-opremsninger uden at sige hvad de betyder for DEM.\n"
        "- Ved Tabel 1-3: spring rå tal-tabeller over eller gør dem meget korte — sig i stedet det VIGTIGSTE tal "
        "(\"du får ca. X kr udbetalt om måneden\") og hvorfor, før detaljerne."
    ),
    2: (
        "KENDER GRUNDPRODUKTERNE — ved trods alt hvad en ratepension, aldersopsparing og folkepension er.\n"
        "- Brug gerne produktnavnene uden at genforklare dem hver gang.\n"
        "- Forklar stadig alt der ikke er et grundprodukt (modregning, tillægsprocent, skatteloft, PAL-skat, "
        "personlig indkomst vs. kapitalindkomst) med én kort sætning FØRSTE gang det nævnes i samtalen.\n"
        "- Almindelig, venlig forklarende tone — dette er standardniveauet, brug det hvis intet andet er sat."
    ),
    3: (
        "GODT KENDSKAB — har godt styr på det i forvejen og vil ikke spildes tiden med grundforklaringer.\n"
        "- Brug fagtermer frit og uden hånd-holdning: PAL-skat, PSL §19, AM-bidrag, personlig indkomst, "
        "modregningsgrundlag, tillægsprocent — ingen grundforklaringer.\n"
        "- Kortere, mere direkte svar. Kom til sagen. Nævn forbehold/nuancer i én bisætning, ikke et afsnit.\n"
        "- Det er fint at lyde som en kollega der sparrer, ikke en der underviser."
    ),
}


def _pension_lix_instruktion(niveau: int | None) -> str:
    niveau_int = int(niveau) if niveau in (1, 2, 3) else PENSION_LIX_DEFAULT
    instruktion = _PENSION_LIX_NIVEAUER[niveau_int]
    return (
        f"Niveau {niveau_int}/3. {instruktion}\n\n"
        "Dette gælder ALT du skriver — løbende chat, Tabel 1-3, og SÆRLIGT når du forklarer en foreslået "
        "optimal udbetalingsrækkefølge fra sekventeringsoptimeringen: den er ofte det sværeste at gennemskue "
        "(hvorfor lige DENNE rækkefølge af start-aldre/perioder giver et højere fast beløb), så brug ekstra "
        "omhu på niveau 1-2 til at forklare det ÅRSAGSMÆSSIGT (fx \"ved at vente med at hæve pengene her, "
        "undgår du at miste noget af folkepensionen samtidig\") i stedet for blot at gengive tallene."
    )


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
            # _civilstand_fra_params (ikke den rå, forældede 'enlig'-flag alene)
            # — ellers viste denne linje "Enlig" for enhver bruger der har sat
            # civilstand via partner-fanen uden nogensinde at have rørt det
            # gamle enlig-felt, selvom den faktiske beregning (format_engine_
            # til_llm, som læser skat_params.enlig) korrekt viser "Par".
            f"- Status: {'Enlig' if _civilstand_fra_params(params) != 'gift_samlevende' else 'Par/samlevende'}",
        ]
        if params.get("inflation_pct"):
            lines.append(f"- Inflation (realværdi): {params['inflation_pct']}%")
        if params.get("produkt_start_aldre"):
            lines.append(f"- Per-produkt start-aldre: {params['produkt_start_aldre']}")
        _buf_fravalg = [k for k, v in (params.get("produkt_i_buffer") or {}).items() if v is False]
        if _buf_fravalg:
            lines.append(f"- Engangsbeløb fravalgt som buffer: {', '.join(_buf_fravalg)}")
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

    # Partnerens egen produktliste (uploadet via den selvstændige Partner-fane)
    # — uden denne blok kendte modellen KUN partnerens beregnede Tabel 1-3
    # (fra engine_tekst ovenfor), aldrig deres faktiske ordninger/selskaber,
    # så et spørgsmål som "hvilke ordninger har min partner?" ikke kunne
    # besvares selvom profilen rent faktisk ligger i sessionen.
    partner_profil_blok = ""
    partner_profil_tekst = session.get("profil_partner_tekst")
    if partner_profil_tekst:
        partner_profil_blok = f"## PARTNERENS PENSIONSPROFIL\n{partner_profil_tekst}\n\n"

    lix_blok = f"## FORKLARINGSNIVEAU (Pensionslix)\n{_pension_lix_instruktion(params.get('pension_lix'))}\n\n"

    return (
        lix_blok
        + f"## TIDLIGSTE PENSIONSALDER\n{tidligste} år\n\n"
        + forsikring_blok
        + fri_formue_blok
        + f"## SPM6_FORDELING\n{spm6_fordeling}\n\n"
        + f"## BEREGNEDE PARAMETRE\n{parametre_tekst}\n\n"
        + f"## BEREGNET PENSIONSANALYSE\n{engine_tekst}\n\n"
        + f"## PENSIONSPROFIL\n{profil_tekst}\n\n"
        + partner_profil_blok
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
    if len(msgs) <= MAX_HISTORY_MESSAGES:
        return
    pruned = msgs[:2] + msgs[-(MAX_HISTORY_MESSAGES - 2):]
    # Fjern consecutive same-role beskeder der kan opstå ved join-punktet
    result: list[dict] = []
    for msg in pruned:
        if result and result[-1]["role"] == msg["role"]:
            result[-1] = msg  # erstat med nyeste (bevar kontekst)
        else:
            result.append(msg)
    # Anthropic kræver at første besked er 'user'
    while result and result[0]["role"] != "user":
        result.pop(0)
    session["messages"] = result


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

    vist_prod_keys: set[tuple] = set()  # (aftalenr, selskab) der allerede er vist

    for a in ord_:
        if a.get("kun_forsikring"):
            continue
        selskab = a.get("selskab") or "?"
        if "ATP" in selskab:
            continue
        ptype = a.get("produkttype") or ""
        nr    = a.get("aftalenr") or ""

        sub = get_prods(nr, selskab)

        opsp = a.get("opsparing")
        if not opsp:
            # Fallback: brug summen af sub-produkternes saldi hvis ordningen mangler total
            opsp = sum(sp.get("opsparing") or 0 for sp in sub) or None
        if not opsp:
            continue

        total_opsp += opsp
        saldo_s = f"{opsp:,.0f} kr.".replace(",", ".")

        vist_prod_keys.add((nr, selskab.lower()))
        for sp in sub:
            sp_nr = str(sp.get("aftalenr") or "")
            vist_prod_keys.add((sp_nr, (sp.get("selskab") or selskab).lower()))

        if len(sub) > 1:
            private_rækker.append(
                f"| {selskab} | **Firmapension** (samlet) | **{saldo_s}** | Samlet saldo for {len(sub)} produkter |"
            )
            for sp in sub:
                spt = sp.get("produkttype") or ""
                per = sp.get("aldersperioder") or {}
                sub_opsp = sp.get("opsparing") or sp.get("estimated_saldo")
                sub_saldo_s = f"{sub_opsp:,.0f} kr.".replace(",", ".") if sub_opsp else "—"
                private_rækker.append(f"| {selskab} |   ↳ {spt} | {sub_saldo_s} | {beм(spt, per, kort=True)} |")
        else:
            sub1 = get_prods(nr, selskab)
            per1 = sub1[0].get("aldersperioder", {}) if sub1 else {}
            inv = a.get("investeringsform") or ""
            display_ptype = inv if inv and inv.lower() not in (ptype or "").lower() and ptype.lower() not in inv.lower() else ptype
            private_rækker.append(f"| {selskab} | {display_ptype} | {saldo_s} | {beм(ptype, per1)} |")

    # Produkter i pensionsprodukter der ikke er dækket af nogen ordning-rad
    for p in prods:
        if "ATP" in (p.get("selskab") or ""):
            continue
        if p.get("udb_type") == "engangsbeloeb" or "aldersopsparing" in (p.get("produkttype") or "").lower() or "kapital" in (p.get("produkttype") or "").lower():
            pass  # alle produkttyper med saldo er relevante
        p_nr  = str(p.get("aftalenr") or "")
        p_sel = (p.get("selskab") or "").lower()
        if (p_nr, p_sel) in vist_prod_keys:
            continue
        p_opsp = p.get("opsparing") or 0
        if not p_opsp:
            continue
        p_ptype = p.get("produkttype") or ""
        p_saldo = f"{p_opsp:,.0f} kr.".replace(",", ".")
        total_opsp += p_opsp
        per = p.get("aldersperioder") or {}
        private_rækker.append(f"| {p.get('selskab') or '?'} | {p_ptype} | {p_saldo} | {beм(p_ptype, per)} |")

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
    tabel += "|---|---|---:|---|\n"
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

        # pension_lix er en kommunikations-præference, ikke et beregnings-
        # parameter — skal overleve et (gen-)upload ligesom resten af siden
        # gør det, i modsætning til pensionsalder/afkast/osv., som med rette
        # nulstilles ved en ny rapport.
        _pension_lix_foer_upload = sessions[session_id].get("beregningsparametre", {}).get("pension_lix")
        sessions[session_id]["messages"] = []
        sessions[session_id]["beregningsparametre"] = (
            {"pension_lix": _pension_lix_foer_upload} if _pension_lix_foer_upload is not None else {}
        )

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


@app.post("/api/upload-partner/{session_id}")
async def upload_partner_rapport(session_id: str, file: UploadFile = File(...)):
    """Fase D — valgfri partner-rapport, uploadet når civilstand-checkboxen
    vælges. Spejler /api/upload, men gemmer under profil_partner og rører
    IKKE messages/beregningsparametre — de tilhører hovedinterviewet, som
    kan allerede være i gang."""
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
        profil_partner = await parse_pensionsinfo_pdf(tmp_path)
        profil_partner_tekst = format_profil_til_tekst(profil_partner)

        sessions[session_id]["profil_partner"] = profil_partner
        sessions[session_id]["profil_partner_tekst"] = profil_partner_tekst

        # Samme oprensning som /api/upload/{session_id} — appen skal kunne
        # bygge PRÆCIS samme spørgeformular (buildInterviewForm) for partneren
        # som for den primære bruger, udfyldt med deres egne rapport-data.
        profil_response = {k: v for k, v in profil_partner.items() if k not in ("raa_tekst", "raw")}
        if "person" in profil_response:
            profil_response["person"] = {
                k: v for k, v in profil_response["person"].items()
                if k != "foedselsdato"
            }

        return {
            "success": True,
            "navn": profil_partner["person"].get("navn", ""),
            "profil_partner_tekst": profil_partner_tekst,
            "profil_partner": profil_response,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logging.error("Partner-upload fejl for session %s: %s", session_id, traceback.format_exc())
        raise HTTPException(500, detail=f"Fejl ved indlæsning af partners rapport: {type(e).__name__}: {e}")
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
                raw = m.group(1)
                # Afgør decimal-separator: hvis begge findes er den sidst forekommende decimal
                if "." in raw and "," in raw:
                    if raw.rfind(".") > raw.rfind(","):
                        raw = raw.replace(",", "")          # komma = tusindtals-sep
                    else:
                        raw = raw.replace(".", "").replace(",", ".")  # punktum = tusindtals-sep
                elif "," in raw:
                    raw = raw.replace(",", ".")             # kun komma → dansk decimal
                # kun punktum (eller ingen): brug direkte
                return cast(raw)
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

    m = re.search(r"5\.\s*Kapitalpension afgiftsfri:\s*([^\n]+)", message)
    if m:
        val = m.group(1).strip().lower()
        p["kapital_skat_type"] = "F" if "brug f" in val or val.startswith("ja") else "A"


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
    # Pensionslix — 1-4, styrer KUN hvordan LLM'en formulerer sig (se
    # _pension_lix_instruktion), rører aldrig selve beregningen og skal
    # derfor ikke indgå i _engine_cache_key.
    pension_lix: int | None = None
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
    produkt_i_buffer: dict | None = None
    loenvaekst_pct: float | None = None
    fri_formue: float | None = None
    fri_formue_kapital_skat_pct: float | None = None
    engangs_buffer_skat_pct: float | None = None
    civilstand: str | None = None
    partner_alder: int | None = None
    partner_indkomst_aar: float | None = None
    produkt_udb_aar: dict | None = None
    folkepension_opsaettelse_aar: int | None = None
    partner_pensionsalder: int | None = None
    # Partnerens EGEN udbetalingstidslinje (Fase D split-screen) — spejler
    # produkt_start_aldre/produkt_i_buffer/produkt_udb_aar/folkepension_
    # opsaettelse_aar ovenfor, men navngivet separat da partnerens
    # produkt-nøgler kommer fra deres EGEN rapport og lever i sessionens
    # egen "beregningsparametre_partner"-boble, ikke den primære brugers.
    partner_produkt_start_aldre: dict | None = None
    partner_produkt_i_buffer: dict | None = None
    partner_produkt_udb_aar: dict | None = None
    partner_folkepension_opsaettelse_aar: int | None = None
    # Partnerens EGNE økonomiske antagelser — helt uafhængige af den primære
    # brugers (Fase D+, split-screen). Sat eksplicit af brugeren via "Beregn
    # partnerens analyse" — falder ellers tilbage til primærens egne værdier
    # i _byg_params_partner(), så det gamle, lette flow (uden disse felter)
    # fortsat virker uændret.
    partner_afkast_pct: float | None = None
    partner_inflation_pct: float | None = None
    partner_udbetaling_aar: int | None = None
    partner_kommuneskat_pct: float | None = None
    partner_kirkeskat_pct: float | None = None
    # Partnerens EGNE personlige facts — samme spørgsmål (Q1/Q5/Q6/Q9/Q9b) som
    # den primære bruger besvarer i sit eget interview, nu genbrugt 1:1 for
    # partneren (samme buildInterviewForm(), udfyldt fra deres egen rapport)
    # i stedet for det tidligere lette alder+indkomst-overslag.
    partner_netto_indbetaling: float | None = None
    partner_fri_formue: float | None = None
    partner_fri_formue_kapital_skat_pct: float | None = None
    partner_kapital_skat_type: str | None = None
    partner_saldi_overrides: list | None = None


@app.post("/api/parametre")
async def gem_parametre(req: Parametre):
    if req.session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    p = sessions[req.session_id]["beregningsparametre"]
    if req.pension_lix is not None:          p["pension_lix"] = req.pension_lix
    if req.netto_indbetaling is not None:    p["netto_indbetaling"] = req.netto_indbetaling
    if req.afkast_pct is not None:           p["afkast_pct"] = req.afkast_pct
    if req.pensionsalder is not None:        p["pensionsalder"] = req.pensionsalder
    if req.udbetaling_aar is not None:       p["udbetaling_aar"] = req.udbetaling_aar
    if req.kommuneskat_pct is not None:      p["kommuneskat_pct"] = req.kommuneskat_pct
    if req.enlig is not None:                p["enlig"] = req.enlig
    if req.kapital_skat_type is not None:    p["kapital_skat_type"] = req.kapital_skat_type
    if req.partner_kapital_skat_type is not None: p["partner_kapital_skat_type"] = req.partner_kapital_skat_type
    if req.inflation_pct is not None:        p["inflation_pct"] = req.inflation_pct
    if req.kirkeskat_pct is not None:        p["kirkeskat_pct"] = req.kirkeskat_pct
    if req.produkt_start_aldre is not None:  p["produkt_start_aldre"] = req.produkt_start_aldre
    if req.produkt_i_buffer is not None:     p["produkt_i_buffer"] = req.produkt_i_buffer
    if req.loenvaekst_pct is not None:        p["loenvaekst_pct"] = req.loenvaekst_pct
    if req.fri_formue is not None:                    p["fri_formue"] = req.fri_formue
    if req.fri_formue_kapital_skat_pct is not None:   p["fri_formue_kapital_skat_pct"] = req.fri_formue_kapital_skat_pct
    if req.engangs_buffer_skat_pct is not None:       p["engangs_buffer_skat_pct"] = req.engangs_buffer_skat_pct
    if req.civilstand is not None:                    p["civilstand"] = req.civilstand
    if req.partner_alder is not None:                 p["partner_alder"] = req.partner_alder
    if req.partner_indkomst_aar is not None:          p["partner_indkomst_aar"] = req.partner_indkomst_aar
    if req.produkt_udb_aar is not None:               p["produkt_udb_aar"] = req.produkt_udb_aar
    if req.folkepension_opsaettelse_aar is not None:  p["folkepension_opsaettelse_aar"] = req.folkepension_opsaettelse_aar
    if req.partner_pensionsalder is not None:         p["partner_pensionsalder"] = req.partner_pensionsalder
    # De ni partner_*-overrides — samme _PARTNER_OVERRIDE_KEYS som
    # _byg_params_partner()/_engine_cache_key() bruger, så et nyt felt kun
    # skal tilføjes ét sted (tuplen) for at blive gemt, cachet OG anvendt.
    for _noegle in _PARTNER_OVERRIDE_KEYS:
        _vaerdi = getattr(req, _noegle)
        if _vaerdi is not None:
            p[_noegle] = _vaerdi

    if req.saldi_overrides:
        profil = sessions[req.session_id].get("profil")
        if profil:
            _anvend_saldi_overrides(profil, req.saldi_overrides)
            sessions[req.session_id]["profil_tekst"] = format_profil_til_tekst(profil)
            # Overriden muterer profilen IN PLACE, så id(profil) i cache-nøglen
            # ikke ændres af det alene — uden denne tæller ville en rettet
            # saldo blive stille ignoreret ved næste "Opdater analyse".
            sessions[req.session_id]["_saldi_version"] = sessions[req.session_id].get("_saldi_version", 0) + 1

    if req.partner_saldi_overrides:
        profil_partner = sessions[req.session_id].get("profil_partner")
        if profil_partner:
            _anvend_saldi_overrides(profil_partner, req.partner_saldi_overrides)
            sessions[req.session_id]["profil_partner_tekst"] = format_profil_til_tekst(profil_partner)
            sessions[req.session_id]["_saldi_version"] = sessions[req.session_id].get("_saldi_version", 0) + 1

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
    session = sessions[session_id]
    # Kør engine med aktuelle parametre (bruger intern cache — kører kun hvis params har ændret sig)
    if session.get("profil") and session.get("beregningsparametre"):
        _kør_engine(session_id)
    result = session.get("engine_output")
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
        "jaevn_tabel": result.get("jaevn_tabel", []),
        "produkt_start_aldre": params.get("produkt_start_aldre", {}),
        "produkt_i_buffer":   params.get("produkt_i_buffer", {}),
        "fri_formue_analyse": result.get("fri_formue_analyse"),
    })


@app.get("/api/session/{session_id}/engine-partner")
async def get_engine_data_partner(session_id: str):
    """Samme facon som /engine, men for partnerens fulde, uafhængige
    udbetalingsplan (Fase D split-screen-diagram) — kun tilgængeligt når en
    fuld partner-profil + civilstand + partner-pensionsalder er sat."""
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    session = sessions[session_id]
    if session.get("profil") and session.get("beregningsparametre"):
        _kør_engine(session_id)  # udfylder/opdaterer engine_output_partner via samme cache
    result = session.get("engine_output_partner")
    if not result:
        return JSONResponse({"error": "Ingen partner-beregning endnu"}, status_code=404)
    params = session.get("beregningsparametre", {})
    return JSONResponse({
        "tabel": result["tabel"],
        "produkter": result["produkter"],
        "engangsbeloeb": result.get("engangsbeloeb", []),
        "fp_alder": result["fp_alder"],
        "pensionsalder": result["pensionsalder"],
        "tabel_start": result["tabel_start"],
        "parametre": result["parametre"],
        "jaevn_netto_mdr": result.get("jaevn_netto_mdr", 0),
        "jaevn_tabel": result.get("jaevn_tabel", []),
        "produkt_start_aldre": params.get("partner_produkt_start_aldre", {}),
        "produkt_i_buffer":   params.get("partner_produkt_i_buffer", {}),
        "fri_formue_analyse": result.get("fri_formue_analyse"),
    })


@app.get("/api/session/{session_id}/analyse-tekst-partner")
async def get_analyse_tekst_partner(session_id: str):
    """Partnerens Tabel 1-3-ækvivalent som ren, allerede-formateret markdown —
    UDEN at gå via en LLM-chat-tur. Partnerens standalone-fane har ingen egen
    chat (den ville dele session['messages'] med primærens chat, med reelle
    rækkefølge-/rolleproblemer hvis begge faner er åbne samtidig), så samme
    format_engine_til_llm()-tekst der normalt injiceres i primærens chat-
    kontekst returneres her direkte til klientens renderMarkdown()."""
    if session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    session = sessions[session_id]
    if session.get("profil") and session.get("beregningsparametre"):
        _kør_engine(session_id)
    result = session.get("engine_output_partner")
    if not result:
        return JSONResponse({"error": "Ingen partner-beregning endnu"}, status_code=404)
    return JSONResponse({"tekst": format_engine_til_llm(result)})


def _foelsomhed_label(noegle: str) -> str:
    """Oversætter sekventering.py's interne følsomheds-nøgler (fx "start:Danica
    Pension_Ratepension") til læsbar dansk, til brug i optimerings-forklaringen."""
    if noegle == "opsaet:folkepension":
        return "hvornår du udskyder folkepension"
    if noegle.startswith("start:"):
        return f"starttidspunktet for {noegle.removeprefix('start:').replace('_', ' – ')}"
    if noegle.startswith("periode:"):
        return f"udbetalingsperioden for {noegle.removeprefix('periode:').replace('_', ' – ')}"
    return noegle


def _forklar_optimering(baseline: dict, vinder: dict, bedste, foelsomhed: dict) -> str:
    """Bygger en KONKRET, datadrevet forklaring af hvorfor den fundne plan er
    bedre end den nuværende — IKKE en generisk skabelontekst, men baseret på
    faktiske forskelle i beregnede skatteflag (over_topskat/tillaegsprocent)
    mellem de to planers år-for-år tabeller. Bruges direkte i resultatboksen
    (både primær og partner, ingen LLM involveret — deterministisk og
    hurtigt) og kan desuden fodres til LLM'en som et faktuelt anker, så dens
    egen (lix-tilpassede) omformulering ikke risikerer at opfinde en årsag.
    """
    baseline_by_alder = {r["alder"]: r for r in baseline.get("tabel", [])}
    vinder_by_alder   = {r["alder"]: r for r in vinder.get("tabel", [])}
    baseline_produkter = {p["key"]: p for p in baseline.get("produkter", [])}

    aendringer: list[str] = []
    for key, ny_start in (bedste.produkt_start_aldre or {}).items():
        gammel = baseline_produkter.get(key)
        if not gammel:
            continue
        gammel_start = gammel.get("start_alder")
        if gammel_start is None or gammel_start == ny_start:
            continue
        navn = f"{gammel.get('selskab', '')} – {gammel.get('produkttype', '')}"
        retning = "udskudt" if ny_start > gammel_start else "fremrykket"

        # Sammenlign skatteflagene VED DEN GAMLE START-ALDER i begge planer —
        # det er netop det år hvor planerne begynder at afvige.
        gl_row = baseline_by_alder.get(gammel_start)
        ny_row = vinder_by_alder.get(gammel_start)
        aarsager = []
        if gl_row and ny_row:
            if gl_row.get("over_topskat") and not ny_row.get("over_topskat"):
                aarsager.append(f"du undgår mellem-/topskat ved {gammel_start} år")
            gl_tp = gl_row.get("tillaegsprocent", 100)
            ny_tp = ny_row.get("tillaegsprocent", 100)
            if gl_tp < 100 and ny_tp > gl_tp:
                aarsager.append(f"du bevarer mere af din tillægsprocent (ældrecheck/mediecheck) ved {gammel_start} år")
        if not aarsager:
            aarsager.append("det udjævner din indkomst mere jævnt hen over årene, uden at forringe noget enkelt år")
        aendringer.append(f"- **{navn}**: {retning} fra {gammel_start} til {ny_start} år — {', og '.join(aarsager)}.")

    for key, ny_periode in (bedste.produkt_udb_aar or {}).items():
        gammel = baseline_produkter.get(key)
        if not gammel:
            continue
        gammel_periode = gammel.get("udb_aar")
        if gammel_periode is None or gammel_periode == ny_periode:
            continue
        navn = f"{gammel.get('selskab', '')} – {gammel.get('produkttype', '')}"
        retning = "forlænget" if ny_periode > gammel_periode else "afkortet"
        aendringer.append(
            f"- **{navn}**s udbetalingsperiode {retning} fra {gammel_periode} til {ny_periode} år — det ændrer "
            f"hvor stort det årlige beløb bliver, og dermed hvilke skattegrænser du rammer undervejs."
        )

    if getattr(bedste, "folkepension_opsaettelse_aar", 0):
        aendringer.append(
            f"- **Folkepension** udskydes {bedste.folkepension_opsaettelse_aar} år — det forhøjer både grundbeløb "
            f"og pensionstillæg med et ventetillæg (forenklet skøn: 6%/år, IKKE en juridisk præcis beregning)."
        )

    if not aendringer:
        return "Din nuværende plan er allerede den bedste fundet inden for det afsøgte rum — der er ingen ændringer at anvende."

    sens_linje = ""
    if foelsomhed:
        top_noegle = max(foelsomhed, key=foelsomhed.get)
        sens_linje = (
            f"\n\nDen enkeltændring der betyder mest for resultatet er **{_foelsomhed_label(top_noegle)}** "
            f"(alene værd ca. {round(foelsomhed[top_noegle]):,} kr/mdr)."
        ).replace(",", ".")

    return (
        "**Sådan er planen fundet:** Din nuværende plan blev sammenlignet år for år mod tusindvis af andre "
        "kombinationer af start-alder, udbetalingsperiode og evt. udskudt folkepension for dine produkter — "
        "og denne kombination gav det højeste FASTE månedsbeløb, uden at gøre noget enkelt år ringere end det "
        "allerede er i din nuværende plan (en indbygget, hård grænse — optimeringen kan aldrig \"redde\" ét år "
        "ved at forringe et andet).\n\n"
        "**Hvad ændres, og hvorfor:**\n" + "\n".join(aendringer) + sens_linje
    )


@app.post("/api/optimer")
async def optimer_udbetalingsplan(req: dict):
    session_id = req.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(404, "Session ikke fundet")
    session = sessions[session_id]
    params  = session.get("beregningsparametre", {})
    profil  = session.get("profil")

    import copy

    # for_partner: samme optimering, men kørt for PARTNERENS egne produkter i
    # stedet for primærens — sekventering.optimer() er allerede symmetrisk i
    # hvem der optimeres vs. hvem der blot bidrager indkomst til koblingen
    # (profil/parametre vs. profil_partner/parametre_partner), så det er kun
    # et spørgsmål om hvilket par der sendes som hvad.
    for_partner = bool(req.get("for_partner"))
    if for_partner:
        profil_partner_raa = session.get("profil_partner")
        params_egen = _byg_params_partner(params) if profil_partner_raa else None
        if not profil_partner_raa or not params_egen:
            return JSONResponse({"success": False, "besked": "Partnerens profil/pensionsalder er ikke sat endnu."}, status_code=400)
        profil_optimeres_kopi = copy.deepcopy(profil_partner_raa)
        kapital_skat = params.get("partner_kapital_skat_type")
        # Koblings-konteksten (primærens profil) er valgfri her, i modsætning
        # til else-grenen — partnerens EGEN optimering skal kunne køre selvom
        # primæren endnu ikke har gennemført sit eget interview. Uden dette
        # tjek ville generer_udbetalingstabel(profil, params) længere nede
        # ramme et KeyError på params["pensionsalder"] og give et uhåndteret
        # 500-svar i stedet for blot at optimere partneren uden husstandskobling.
        har_primaer = bool(profil and params.get("pensionsalder"))
        profil_kobling_kopi = copy.deepcopy(profil) if har_primaer else None
        params_kobling = params if har_primaer else None
    else:
        if not profil or not params.get("pensionsalder"):
            return JSONResponse({"success": False, "besked": "Ingen profil/pensionsalder endnu — gennemfør interviewet først."}, status_code=400)
        profil_optimeres_kopi = copy.deepcopy(profil)
        kapital_skat = params.get("kapital_skat_type") or _kapital_skat_type_fra_historik(session)
        params_egen = params
        # Samme husstands-detektion som _kør_engine — uden den ville optimeringen
        # score kandidater mod et husstands-uafhængigt (forkert) indtægtsgrundlag
        # for enhver bruger der har uploadet en fuld partner-profil. Partnerens
        # egen, manuelt justerede tidslinje (split-screen-diagrammet) indgår i
        # den koblede baseline via _byg_params_partner, ellers ville optimeringen
        # score mod partnerens u-justerede default-plan.
        profil_partner_raa = session.get("profil_partner")
        params_kobling = _byg_params_partner(params) if profil_partner_raa else None
        profil_kobling_kopi = copy.deepcopy(profil_partner_raa) if params_kobling is not None else None

    if kapital_skat:
        for p in profil_optimeres_kopi.get("pensionsprodukter", []):
            if "kapital" in (p.get("produkttype") or "").lower():
                p["skat_type"] = kapital_skat

    kwargs_partner = {
        "profil_partner": profil_kobling_kopi,
        "parametre_partner": params_kobling,
    } if params_kobling is not None else {}

    haard_minimum = req.get("haard_minimum_mdr")
    obj = sekventering.Objektiv(
        haard_minimum_mdr=float(haard_minimum) if haard_minimum is not None else None,
    )
    res = sekventering.optimer(profil_optimeres_kopi, params_egen, obj, **kwargs_partner)

    if not sekventering.har_loesning(res):
        return JSONResponse({
            "success": False,
            "evalueret": res.evalueret,
            "mulige_kombinationer": res.mulige_kombinationer,
            "besked": "Målet kan ikke nås inden for det afsøgte rum af start-aldre og folkepensions-opsætning — ingen kombination undgår mindst ét år under den anvendte minimumsgrænse.",
        })

    b = res.bedste

    # Optimeringens objektiv er UDELUKKENDE den optimerede persons eget
    # jaevn_niveau_realt (score() i sekventering.py) — partnerens bidrag til
    # koblingen holdes fast som en KONSTANT under hele søgningen (bevidst,
    # ellers ville hver kandidat kræve en fuld husstandskørsel — se
    # sekventering.py's docstring). Det betyder søgningen er blind for at en
    # højere plan for denne person kan hæve det KOMBINEREDE indtægtsgrundlag
    # nok til at skære i partnerens pensionstillæg/tillægsprocent mere end
    # denne person vinder — en reel husstands-forringelse der ville blive
    # scoret som en forbedring. For den VINDENDE kandidat (kun ÉN, ikke hver
    # af de tusindvis undervejs) tjekkes derfor eksplicit om husstandens
    # SAMLEDE beløb rent faktisk stiger, ikke kun denne persons eget.
    husstand_advarsel = None
    baseline_resultat = None
    vinder_resultat = None
    params_egen_ny_plan = {
        **params_egen,
        "produkt_start_aldre": b.produkt_start_aldre,
        "produkt_udb_aar": b.produkt_udb_aar,
        "folkepension_opsaettelse_aar": b.folkepension_opsaettelse_aar,
    }
    if params_kobling is not None and profil_kobling_kopi is not None:
        try:
            husstand_foer = husstand.beregn_husstand(
                profil_optimeres_kopi, params_egen, profil_kobling_kopi, params_kobling,
            )
            husstand_efter = husstand.beregn_husstand(
                profil_optimeres_kopi, params_egen_ny_plan, profil_kobling_kopi, params_kobling,
            )
            baseline_resultat = husstand_foer["a"]
            vinder_resultat   = husstand_efter["a"]
            total_foer  = husstand_foer["a"].get("jaevn_netto_mdr", 0) + husstand_foer["b"].get("jaevn_netto_mdr", 0)
            total_efter = husstand_efter["a"].get("jaevn_netto_mdr", 0) + husstand_efter["b"].get("jaevn_netto_mdr", 0)
            if total_efter < total_foer - 1:  # -1: undgå at ramle på afrundingsstøj
                husstand_advarsel = (
                    f"Denne plan forbedrer dit eget beløb, men jeres SAMLEDE husstandsindkomst "
                    f"falder med ca. {round(total_foer - total_efter):,} kr/mdr, fordi den ændrer "
                    f"jeres partners pensionstillæg/tillægsprocent. Optimeringen ser kun på dit "
                    f"eget beløb, ikke husstandens samlede."
                ).replace(",", ".")
        except Exception:
            # Denne ekstra kontrol må aldrig vælte selve optimerings-svaret —
            # udebliver den, mangler brugeren blot advarslen, ikke resultatet.
            import traceback
            logging.error("Husstands-tjek af vindende optimeringsplan fejlede: %s", traceback.format_exc())
    else:
        try:
            baseline_resultat = generer_udbetalingstabel(copy.deepcopy(profil_optimeres_kopi), params_egen)
            vinder_resultat   = generer_udbetalingstabel(copy.deepcopy(profil_optimeres_kopi), params_egen_ny_plan)
        except Exception:
            import traceback
            logging.error("Baseline/vinder-genberegning til optimerings-forklaring fejlede: %s", traceback.format_exc())

    # Konkret, datadrevet forklaring af HVORFOR — ikke kun tallet — genbrugt
    # direkte i resultatboksen for BÅDE primær og partner (ingen LLM-tur
    # nødvendig, partneren har jo ingen chat) og som fakta-anker til LLM'ens
    # egen (lix-tilpassede) narration i chatten for primæren.
    forklaring = (
        _forklar_optimering(baseline_resultat, vinder_resultat, b, res.foelsomhed)
        if baseline_resultat is not None and vinder_resultat is not None else None
    )

    return JSONResponse({
        "success": True,
        "evalueret": res.evalueret,
        "mulige_kombinationer": res.mulige_kombinationer,
        "antal_gennemfoerlige": res.antal_gennemfoerlige,
        "bedste": {
            "produkt_start_aldre": b.produkt_start_aldre,
            "produkt_udb_aar": b.produkt_udb_aar,
            "folkepension_opsaettelse_aar": b.folkepension_opsaettelse_aar,
            "jaevn_netto_mdr": round(b.jaevn_netto_mdr),
        },
        "foelsomhed": {k: round(v) for k, v in res.foelsomhed.items()},
        "husstand_advarsel": husstand_advarsel,
        "forklaring": forklaring,
        # satser_2026.FOLKEPENSION_VENTEPROCENT_PR_AAR er eksplicit uverificeret
        # (forenklet 6%/år-skøn, ikke en juridisk præcis ventetillægsberegning)
        # — når den bedste plan rent faktisk bygger på at udskyde folkepension,
        # skal ENHVER forbruger af dette svar (UI, LLM, en fremtidig API-klient)
        # kunne se det uden at skulle kende sekventering.py's interne satser.
        "usikker_venteprocent": bool(b.folkepension_opsaettelse_aar > 0),
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
