"""Test: build_oversigt_tabel viser produkter selv når ordning mangler saldo"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import build_oversigt_tabel

PASS, FAIL = [], []
def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

# ── Test A: ordning med opsparing=None, men sub-produkter har saldi ───────────
print("\n=== A: Ordning opsparing=None — fallback til sub-produkter ===\n")
profil_a = {
    "ordninger": [
        {"selskab": "Velliv", "aftalenr": "V123", "opsparing": None, "produkttype": "Firmapension"},
    ],
    "pensionsprodukter": [
        {"selskab": "Velliv", "aftalenr": "V123", "produkttype": "Ratepension",    "opsparing": 350000, "skat_type": "S"},
        {"selskab": "Velliv", "aftalenr": "V123", "produkttype": "Livsvarig pension","opsparing": 150000, "skat_type": "S"},
    ],
}
tabel_a = build_oversigt_tabel(profil_a)
check("Velliv vises i tabel", "Velliv" in tabel_a)
check("Ratepension vises som sub-produkt", "Ratepension" in tabel_a)
check("Livsvarig pension vises som sub-produkt", "Livsvarig pension" in tabel_a)
check("Total 500.000 kr beregnet", "500.000" in tabel_a)
pass  # uddrag springes over

# ── Test B: ordning med opsparing sat korrekt (uændret adfærd) ──────────────
print("\n=== B: Ordning med opsparing — normal adfærd bevaret ===\n")
profil_b = {
    "ordninger": [
        {"selskab": "Danica", "aftalenr": "D1", "opsparing": 800000, "produkttype": "Ratepension"},
    ],
    "pensionsprodukter": [
        {"selskab": "Danica", "aftalenr": "D1", "produkttype": "Ratepension", "opsparing": 800000, "skat_type": "S"},
    ],
}
tabel_b = build_oversigt_tabel(profil_b)
check("Danica vises", "Danica" in tabel_b)
check("800.000 vises", "800.000" in tabel_b)

# ── Test C: produkt UDEN ordning-match vises i oversigten ───────────────────
print("\n=== C: Produkt i pensionsprodukter uden ordning ===\n")
profil_c = {
    "ordninger": [
        {"selskab": "Danica", "aftalenr": "D1", "opsparing": 800000, "produkttype": "Ratepension"},
    ],
    "pensionsprodukter": [
        {"selskab": "Danica",  "aftalenr": "D1",  "produkttype": "Ratepension",    "opsparing": 800000, "skat_type": "S"},
        {"selskab": "Nordea",  "aftalenr": "N99", "produkttype": "Aldersopsparing","opsparing": 75000,  "skat_type": "F"},
    ],
}
tabel_c = build_oversigt_tabel(profil_c)
check("Nordea (ingen ordning) vises alligevel", "Nordea" in tabel_c)
check("75.000 kr. vises", "75.000" in tabel_c)

# ── Test D: ordning med opsparing=0 og ingen sub-saldi → udelades ───────────
print("\n=== D: Opsparing=0 og ingen sub-saldi -- udelades (forsikring) ===\n")
profil_d = {
    "ordninger": [
        {"selskab": "PFA", "aftalenr": "P1", "opsparing": 0,   "produkttype": "Gruppelivsordning", "kun_forsikring": True},
        {"selskab": "PFA", "aftalenr": "P2", "opsparing": None, "produkttype": "Ratepension"},
    ],
    "pensionsprodukter": [],
}
tabel_d = build_oversigt_tabel(profil_d)
check("PFA Gruppelivsordning (kun_forsikring) udelades", "Gruppelivsordning" not in tabel_d)
check("PFA Ratepension uden saldo og ingen produkter udelades", tabel_d.count("PFA") == 0)

print(f"\n{'='*44}")
print(f"RESULTAT: {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    for f in FAIL: print(f"  - {f}")
