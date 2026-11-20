"""Test: pdf_parser._filter_relevant_pages — lange rapporter og layout-varianter."""
import os, re, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pdf_parser as pp

PASS, FAIL = [], []
def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

def scenario_pages(alder):
    return [
        f"Hvis du går på pension som {alder}-årig får du (1 | 2)\nOverblik",
        f"Hvis du går på pension som {alder}-årig får du (2 | 2)\nPensioner {alder} år Fra 76 år",
    ]

def kept_pages(tekst):
    return [int(x) for x in re.findall(r"=== SIDE (\d+) ===", tekst)]

grundsider = ["Pensionsoplysninger for:\nTest", "Information", "Nuværende pensionsopsparinger", "Dine aftaler"]

print("\n=== Lang rapport: kun det tidligste prognose-scenarie beholdes ===")
pages = list(grundsider)
for alder in range(60, 76):
    pages += scenario_pages(alder)
pages += ["Pension - flere oplysninger"]
tekst = pp._filter_relevant_pages(pages)
sider = kept_pages(tekst)
check("Kun ét scenarie-sæt (2 sider) ud af 32 prognose-sider er med",
      sum(1 for s in sider if "60-årig" in pages[s - 1]) == 2 and not any("61-årig" in pages[s - 1] for s in sider),
      f"sider={sider}")
check("Grundsider og 'flere oplysninger' er stadig med", all(s in sider for s in (1, 3, 4, len(pages))))

print("\n=== Variant: løs 'tidligst hæve fra 60-årig'-tekst uden en 60-års-prognose ===")
pages = list(grundsider)
pages[1] = "Du kan tidligst hæve fra 60-årig"
for alder in range(62, 68):
    pages += scenario_pages(alder)
tekst = pp._filter_relevant_pages(pages)
check("Prognosen for det tidligste FAKTISKE scenarie (62) beholdes — ikke droppet pga. den løse 60",
      any("62-årig" in pages[s - 1] for s in kept_pages(tekst)))
check("Senere scenarier (63+) droppes stadig",
      not any("63-årig" in pages[s - 1] for s in kept_pages(tekst)))

print("\n=== Variant: ingen kendte overskrifter -> tekstbaseret fallback ===")
pages = list(grundsider) + ["Tidligste pensionsalder: 61", "Noget om en 61-årig... som 61-årig får du X"]
tekst = pp._filter_relevant_pages(pages)
check("Fallback finder stadig sider", len(tekst) > 0)

print("\n=== Størrelsesloft ===")
pages = ["Dine aftaler\n" + "x" * 5000 for _ in range(40)]
check("Den filtrerede tekst overstiger aldrig MAX_RELEVANT_TEKST_TEGN",
      len(pp._filter_relevant_pages(pages)) <= pp.MAX_RELEVANT_TEKST_TEGN)

print("\n" + "=" * 44)
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    for f in FAIL:
        print(f"  - {f}")
