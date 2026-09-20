# PensionsAnalytiker

Et beslutningsstøtteværktøj til dansk pensionsplanlægning: brugeren uploader sin PensionsInfo-rapport (PDF), værktøjet fremskriver forventet udbetaling år for år under dansk skatte- og pensionslovgivning, og en AI-assistent præsenterer og forklarer resultatet i en chat.

Dette dokument er skrevet til at kunne fungere som grundlag for en gennemgang af værktøjets pålidelighed — hvad det beregner, hvilke antagelser det bygger på, hvor de kommer fra, og hvordan man selv verificerer dem. Det erstatter ikke en revisors egen vurdering, men samler alt det en sådan gennemgang har brug for at kunne finde, ét sted.

## Formål og ansvarsfraskrivelse

PensionsAnalytiker er **information, ikke reguleret rådgivning**. Det gælder både værktøjet og dette dokument. Enhver konkret beslutning om egen pension bør ske i samråd med en certificeret pensionsrådgiver — den formulering vises eksplicit til brugeren ved enhver konkret anbefaling (se `prompts.py`'s `STATIC_SYSTEM_PROMPT`, "GENERELLE REGLER").

Værktøjet er bygget af én udvikler som et personligt/uformelt projekt, ikke af en godkendt finansiel rådgivningsvirksomhed. Det har ingen juridisk status som revideret finansielt system. Formålet med dette dokument er gennemsigtighed om hvordan det er bygget — ikke en garanti for lovmæssig korrekthed i enhver situation.

## Arkitektur-oversigt

| Fil | Ansvar |
|---|---|
| `engine.py` | Den deterministiske beregningskerne. Al skat, annuitets- og fremskrivningsmatematik. Ingen AI, ingen gætværk. |
| `satser_2026.py` | **Eneste kildested** for alle satser og beløbsgrænser — se næste afsnit. |
| `sekventering.py` | Optimerer rækkefølgen af udbetalinger (start-aldre, perioder, evt. udskudt folkepension) ved at afprøve mange kombinationer gennem `engine.py` og score dem. |
| `husstand.py` | Orkestrerer to koblede `engine.py`-kørsler for et gift/samlevende par (fælles indtægtsgrundlag for pensionstillæg/tillægsprocent). |
| `pdf_parser.py` | Udtrækker strukturerede data fra en PensionsInfo-PDF via `pdfplumber` + Claude, til det format `engine.py` forventer. |
| `prompts.py` | Al tekst AI'en ser eller siger — systemprompt, forklaringsniveauer, ordningsforklaringer, PDF-ekstraktionsprompt. **Ingen** hårdkodede satser — alt hentes fra `satser_2026.py`. |
| `app.py` | FastAPI-routing, sessionshåndtering, streaming-chat, caching. Ingen beregning, ingen prompt-tekst — kun orkestrering. |
| `static/index.html` | Frontend: diagram, tidslinje, interview-panel, chat-UI. |
| `tests/` | Regressionstest — se "Test og verifikation" nedenfor. |

### Den vigtigste invariant: AI'en beregner aldrig selv

`engine.py` kører **altid** synkront og fuldstændigt færdig, før AI'en (Claude) overhovedet begynder at generere et svar (se `app.py`'s `_get_dynamic_context`/`_kør_engine`). AI'ens eneste opgave er at **præsentere og forklare** de færdigberegnede tal — aldrig selv regne dem ud. Dette er en eksplicit, gentagen instruktion i `prompts.py`:

> ⚠️ KRITISK: Du må ALDRIG præsentere et beregnet tal der ikke er hentet direkte fra "BEREGNET PENSIONSANALYSE" i kontekstblokken nedenfor. [...] Du har IKKE adgang til korrekte skatteberegninger — engine'en har. Brug KUN engine-tal.

Den ene, snævert afgrænsede undtagelse (fordeling af et engangsbeløb over 10 år ved en eksplicit brugerkommando) er selv dokumenteret som en undtagelse i prompten, med en fast formel — ikke fri beregning.

Konsekvensen for en revisionsgennemgang: at verificere værktøjets **tal** er et spørgsmål om at verificere `engine.py` og `satser_2026.py` — ikke AI-modellens outputs. AI'ens del af opgaven (formulering, forklaringsniveau) er et separat, mindre kritisk ansvarsområde dækket i `prompts.py`.

## Satser og provenance

Alle satser og beløbsgrænser (skattetrin, folkepension, pensionstillæg, lofter osv.) er samlet i `satser_2026.py` som `Sats`-objekter:

```python
@dataclass(frozen=True)
class Sats:
    id: str            # unik nøgle, fx "skat.am_bidrag"
    vaerdi: float | bool
    kilde: str          # hvor tallet kommer fra — lovhenvisning eller kildehenvisning
    verificeret: bool = True
```

Hver sats bærer sin egen kilde og et `verificeret`-flag. `verificeret=False` betyder: tallet er et forsigtigt skøn eller genbrugt fra et tidligere år, IKKE bekræftet mod en officiel 2026-kilde. En `strict`-parameter findes allerede i infrastrukturen (`satser_2026.brug()`) til at kunne kaste en fejl hvis en uverificeret sats bruges — slået fra i dag, men klar til at aktiveres hvis værktøjet nogensinde skal bruges i en kontekst hvor det kræves.

**Satser markeret `verificeret=False` i skrivende stund** (efter et gennemsyn 2026-09-20 mod officielle kilder — se nedenfor for hvad der blev rettet):

| Sats | Værdi | Hvorfor stadig uverificeret |
|---|---|---|
| `LIVSVARIG_ESTIMAT_AAR` | 18 år | Kræver Finanstilsynets faktiske K2023-levetidsbenchmark (en aldersspecifik restlevetidstabel, ikke ét samlet tal) for reel verifikation — ikke gjort ved dette gennemsyn |
| `FOLKEPENSION_VENTEPROCENT_PR_AAR` | 5,71%/år | Strukturelt umuligt at "verificere" som én flad sats — den reelle regel er forholdet mellem måneders udskydelse og restlevetiden ved DEN alder pensionen opsættes fra, altså alders-/levetidsafhængig. Værdien er opdateret fra en uafklaret 6%/år-arv til det konkrete, kildehenviste eksempel for udskydelse fra 67 år i 2026 (Ældre Sagen/borger.dk) |

**Verificeret 2026-09-20 mod primære, officielle kilder** (tidligere `verificeret=False`, nu rettet og bekræftet — se `satser_2026.py`'s `kilde`-felter for de fulde citater):

| Sats | Gammel værdi | Ny, verificeret værdi | Kilde |
|---|---|---|---|
| `PERSONFRADRAG_AAR` | 49.700 kr/år | **54.100 kr/år** | Skatteministeriet (svmn.dk), PSL § 20-regulering — hverken den gamle værdi eller "2025-niveau"-antagelsen bag den var korrekt; 2025 var faktisk 51.600 kr. |
| `RATEPENSION_LOFT` | 63.100 kr/år | **68.700 kr/år** | skat.dk, direkte citat: "Du kan samlet set få fradrag for indbetalinger på op til 68.700 kr. i 2026 (65.500 kr. i 2025)." |
| `ATP_MDR_STANDARD` | 1.825 kr/mdr | **1.445 kr/mdr** | ATP.dk's pressemeddelelse om 2026-stigningen: 17.340 kr./år for et gennemsnitligt medlem |
| `MEDIECHECK` | 1.552 kr/år | 1.552 kr/år (uændret — nu bekræftet) | borger.dk: "får du 1.552 kr. (2026) før skat" |
| `app.py`'s `KOMMUNESKAT` (98 kommuner) | 2025-niveau, flere ukorrekte/uramte nøgler | **Alle 98 kommuner opdateret** | Skatteministeriets officielle 2026-regneark (svmn.dk) |

**En vigtig pointe fundet undervejs**: `satser_2026.py`'s progressive topskattetrin (mellemskat 641.200 kr / topskat 777.900 kr / top-topskat 2.592.700 kr) var allerede korrekte og blev bekræftet direkte mod skat.dk — de var IKKE blandt de rettede tal, på trods af at flere uafhængige (ikke-officielle) aggregatorsider undervejs i researchen viste helt andre tal for de samme grænser (det viste sig at være de samme grænser opgjort FØR AM-bidrag i stedet for efter — ikke en fejl, blot en anden opgørelsesmåde). God grund til altid at bekræfte mod en primær kilde (skat.dk/Skatteministeriet), ikke en sekundær opsummering.

**Ventetillægget ved udskudt folkepension fik en reel modelforbedring, ikke kun en satsrettelse**: den tidligere flade tilnærmelse (samme procent uanset hvor mange år man udskyder) er nu erstattet af `satser_2026.VENTEPROCENT_LIVSVARIG_2026_FRA_67` — en nøjagtig, år-for-år-tabel (Ældre Sagen, 2026-niveau) for det almindelige tilfælde: opsætning fra 67 år, højst 10 år. Tabellen er bevidst IKKE lineær (år 10 giver over 15 gange så stort et tillæg som år 1, fordi restlevetiden falder jo ældre man opsætter fra) — noget en enkelt flad sats aldrig kunne gengive. `engine._fp_venteprocent()` bruger denne tabel når den passer, og falder ellers tilbage til den forenklede, fortsat uverificerede sats. Sekventeringsoptimeringens svar (`/api/optimer`) markerer nu eksplicit via `usikker_venteprocent`, hvilken af de to der er brugt for en given vinderplan.

**En ny, dokumenteret men bevidst IKKE modelleret ydelse**: `satser_2026.CHECK_2026_ENGANGS` — en skattefri engangscheck til folkepensionister i 2026 (hastelov, betinget af likvid formue ≤ 350.000 kr., fm.dk-faktaark 27.01.2026). Den indgår ikke i selve udbetalingsfremskrivningen, se `satser_2026.py`'s kommentar for hvorfor.

Alt andet i `satser_2026.py` (progressiv topskat, folkepensionens grundbeløb, pensionstillæg, ældrecheck, formuegrænse, aldersopsparingens lofter) er markeret `verificeret=True` med en konkret lov- eller kildehenvisning i `kilde`-feltet.

**Hvorfor det er vigtigt at det KUN står ét sted**: en tidligere version af koden havde flere uafhængige kopier af de samme tal — `engine.py` havde sine egne bare konstanter, systemprompten i `app.py` havde en hel sats-sektion hånd-skrevet som prosa (i to versioner, én stemplet "2025" og én "2026", med forskellige tal for topskattegrænsen), en helt separat `pension_rules.py`-fil havde en tredje, indbyrdes modstridende kopi, og frontendens JavaScript havde sin egen kommuneskat-tabel. Ved en gennemgang i forbindelse med dette dokument blev det opdaget at flere af disse kopier reelt var **forkerte** eller **indbyrdes uenige** — bl.a. brugte frontendens ratepensionsloft (68.700 kr) en anden værdi end selve beregningen (63.100 kr, som det viste sig var den forældede af de to), og flere steder viste stadig den forældede 2025-folkepension (7.955 kr/mdr) og den forældede, ikke-progressive topskattegrænse (588.900 kr) i tekst brugeren faktisk så. Alle disse steder er nu rettet til at læse fra `satser_2026.py` i stedet for at duplikere tallet — se "Årlig satsopdatering" nedenfor for hvordan det forbliver sådan.

## Årlig satsopdatering — checkliste

Når nye satser for et kommende år offentliggøres (typisk Skatteministeriet, skat.dk's "Den juridiske vejledning", og Lov om social pensions satstabeller i december/januar):

1. **Opdatér kun `satser_2026.py`** (eller opret en ny `satser_2027.py` og skift importen i `engine.py`/`app.py`/`prompts.py` — vælg den model der passer bedst når det bliver aktuelt). Rediger `vaerdi` og `kilde`-feltet for hver ændret sats; sæt `verificeret=True` når kilden er bekræftet.
2. **Rør ikke ved `engine.py`, `app.py` eller `prompts.py`s tal** — de læser alle satser fra `satser_2026.py`. Systemprompten (`prompts.py`'s `_byg_noeglesatser_afsnit`) og ordningsforklaringerne (`_byg_ordning_forklaringer`) genereres dynamisk derfra ved opstart.
3. **Kommuneskat**: opdatér `app.py`'s `KOMMUNESKAT`-dict manuelt (alle 98 kommuner, typisk fra KL's egen oversigt eller skat.dk) — det er den ene sats-samling der ikke er i `satser_2026.py`'s `Sats`-format, fordi det er 98 enkeltværdier uden individuel lovhenvisning.
4. **Kør hele testsuiten** (se nedenfor) — alle regressionstests skal fortsat bestå. Nye/ændrede satser bør give nye, forventede tal i de tests der bruger dem direkte (`tests/test_samspil.py` m.fl.), ikke bare "stadig grønt".
5. **Verificér i browseren**: upload en test-rapport, bekræft at Tabel 2's skattegrænse-noter og "NØGLESATSER"-teksten i chatten viser de nye tal.

## Kendte forenklinger og antagelser

Ud over ovenstående `verificeret=False`-satser er der modelleringsvalg værd at være eksplicit om:

- **Folkepensions-ventetillæg ved udskudt folkepension**: for opsætning fra 67 år i højst 10 år bruges en nøjagtig, kildehenvist tabel (`satser_2026.VENTEPROCENT_LIVSVARIG_2026_FRA_67`). For enhver anden opsætningsalder/-periode falder beregningen tilbage til en flad tilnærmelse (`FOLKEPENSION_VENTEPROCENT_PR_AAR`, 5,71%/år) — den reelle regel er restlevetidsbaseret og afhænger af den alder pensionen rent faktisk opsættes fra, så en flad sats kan aldrig gengive den præcist for andre aldre. Se `usikker_venteprocent`-feltet i `/api/optimer`-svaret for hvilken af de to der er brugt.
- **Samspil mellem gifte/samlevende** (`husstand.py`): det kombinerede indtægtsgrundlag for pensionstillæg/tillægsprocent er en forenklet tilnærmelse til Lov om social pension § 29's fulde modregningsregel. Uden partnerens egen uploadede rapport er partnerens indkomst desuden kun et fast årligt overslagstal, ikke en år-for-år-fremskrivning.
- **CPR-fødselsårs-parsing** (`pdf_parser.py`): antager fødselsår i 1900-tallet uden at tjekke CPR-nummerets 7. ciffer (den formelle regel for at skelne 1900- fra 2000-tal). Forsvarligt fordi værktøjets aldersgrænser (typisk 55-85 år) gør at ingen reel bruger kan være født i 2000-tallet.
- **PDF-ekstraktionens back-udledte fremtidsværdier** (`pdf_parser.py`s `_fv_fra_payout`): når en PensionsInfo-rapport ikke direkte oplyser en opsparingssaldo, "fortrydes" annuiteten fra det viste udbetalingsbeløb med en uafhængig, konservativ diskonteringssats (4%) og et skønnet antal år (25 år for livsvarig pension/livrente) — bevidst adskilt fra brugerens egen afkastantagelse og fra `LIVSVARIG_ESTIMAT_AAR`, som løser en anden opgave (fremadrettet udbetalingsperiode, ikke et gæt på en manglende saldo).
- **Aldersopsparingens diskvalifikations-/overførselsstrafafgifter** (`satser_2026.ALDERSOPSPARING_DISKVALIFIKATION_AAR` m.fl.): optaget som verificerede, kildehenviste fakta, men bruges **ikke** i selve beregningen — det ville kræve profildata (hvornår en udbetaling konkret er påbegyndt) som `pdf_parser.py` i dag ikke udtrækker fra rapporten.
- **Engangsbeløb spredt over flere år** ("kapital_rate" i `engine.py`): fordelen ved at sprede en kapitalpension/aldersopsparing i stedet for at tage den samlet kommer udelukkende af at pengene fortsætter til pensionsafkastskat (PAL) i stedet for en lavere, personligt beskattet alternativ-rente — IKKE fra at undgå indkomstskat. Kræver i praksis ofte at ordningen omlægges hos selskabet; ikke en generel ret for enhver ordning.
- **Sekventeringsoptimeringen** (`sekventering.py`) afsøger et beskåret, multi-start koordinatsøgnings-rum (ikke en udtømmende gennemgang af alle kombinationer) — rapporteres eksplicit til brugeren når det fulde søgerum er større end det antal der reelt blev evalueret.

## Test og verifikation

Al testkode ligger i `tests/`, navngivet efter pytest-konventionen (`test_*.py`) så suiten er auto-discoverbar, men skrevet i et let, ikke-pytest-afhængigt print-baseret PASS/FAIL-format der kan køres direkte:

```bash
python tests/test_samspil.py
python tests/test_sekventering.py
# ... eller kør dem alle:
for f in tests/test_*.py; do python "$f"; done
```

`tests/test_api.py`, `tests/test_deploy.py` og `tests/test_final.py` kræver at appen kører lokalt (`python -m uvicorn app:app`) da de tester rigtige HTTP-endpoints; resten kører uafhængigt af en kørende server.

Ca. 190 regressionstests dækker bl.a.:
- **Golden-cases** porteret fra `pension-core` (en TypeScript-prototype) med håndverificerede facit-tal for progressiv skat, pensionstillæg og tillægsprocent (`test_samspil.py`).
- **Sats-eskalering**: at fremtidige års skattegrænser satsreguleres nominelt i stedet for at bruge et fast 2026-tal langt ude i fremtiden.
- **Sekventeringsoptimeringens** søgerum, hårde gennemførlighedsgrænser og multi-start-koordinatsøgning (`test_sekventering.py`).
- **Husstandskoblingen** mellem to personers pensionstillæg/tillægsprocent (`test_husstand.py`).
- **Buffer/jævn-udjævnings**-logikken der glatter engangsbeløb ud over pensionsperioden (`test_buffer.py`, `test_jaevn.py`).
- **Regressionstest for hver ny funktion**, inkl. den spredte engangsudbetaling (`test_kapitalrate.py`) og aldersopsparingens indbetalingsloft.

Testene var indtil 2026-09-20 ikke committet til git — de eksisterede kun lokalt og var derfor usynlige for enhver der klonede repoet. De er nu en del af repoet under `tests/`, netop for at gøre beviset for beregningernes korrekthed en del af den offentlige historik, ikke noget der skal tages på tro.
