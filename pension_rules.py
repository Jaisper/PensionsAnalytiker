"""
Strukturerede data om dansk pensionslovgivning, satser og regler.
Opdateret til 2025/2026.
"""

PENSION_REGLER = {
    "satser_2025": {
        "ratepension_loft": 63_100,
        "aldersopsparing_loft_naer_pension": 58_900,  # under 5 år til pensionsalder
        "aldersopsparing_loft_normal": 9_100,
        "pal_skat_pct": 15.3,
        "amb_pct": 8.0,
        "folkepension_grundbeloeb_maanedlig": 7_955,
        "folkepension_pensionstillaeg_max_maanedlig": 8_891,  # enlig
        "folkepension_alder": 67,  # stiger til 68 i 2030, 69 i 2035
        "pensionsalder_tidligst": 62,  # PALFP 2025 (tidligere 60/61)
        "atp_bidrag_fuld_tid_aarlig": 3_556,
    },
    "ordningstyper": {
        "ratepension": {
            "beskrivelse": "Udbetales over en periode (typisk 10-30 år). Fradragsret ved indbetaling (AM-bidrag fratrukket). Loft: 63.100 kr/år (2025).",
            "skat_ved_udbetaling": "Skattepligtig (S)",
            "loft_2025": 63_100,
            "min_udbetaling_aar": 10,
            "max_udbetaling_aar": 30,
        },
        "livrente": {
            "beskrivelse": "Livsvarig pension - udbetales så længe du lever. Ingen loft på indbetalinger. Fradragsret. Anbefales som risikoafdækning mod at leve længe.",
            "skat_ved_udbetaling": "Skattepligtig (S)",
            "loft_2025": None,  # ingen loft
        },
        "aldersopsparing": {
            "beskrivelse": "Afløste kapitalpension. Ingen fradragsret, men afgiftsfri udbetaling (kun PAL). Loft: 9.100 kr/år (eller 58.900 kr hvis under 5 år til pension).",
            "skat_ved_udbetaling": "Afgiftsfri (F)",
            "loft_normal_2025": 9_100,
            "loft_naer_pension_2025": 58_900,
        },
        "kapitalpension": {
            "beskrivelse": "Lukket for nye indbetalinger siden 2013. Eksisterende depoter bevares. Udbetales som engangsbeløb med 40% afgift (kan nedsættes).",
            "skat_ved_udbetaling": "Afgiftspligtig (A) - 40% afgift",
        },
        "atp": {
            "beskrivelse": "Lovpligtig. Udbetales livsvarigt fra folkepensionsalderen. Indeksreguleres.",
            "skat_ved_udbetaling": "Skattepligtig (S)",
        },
    },
    "pensionsalder_regler": {
        "folkepension": "67 år (stiger til 68 i 2030, 69 i 2035 og 70 i 2040 med forbehold for levetidsindeks)",
        "tidligst_private": "62 år (PALFP - 5 år før folkepensionsalder). Visse ordninger fra 60 år for aftaler oprettet før 01.05.2007.",
        "atp": "Udbetales fra folkepensionsalder",
        "note_jesper": "Jesper er f. 1971, folkepensionsalder 68 år (2039). Tidligste private pension: 63 år.",
    },
    "skat_optimering": {
        "prioritering": [
            "1. Udnyt arbejdsgiverbidrag fuldt ud (gratis opsparing)",
            "2. Indbetal til livrente - ingen loft, fuld fradragsret",
            "3. Indbetal til ratepension op til loft (63.100 kr/år)",
            "4. Indbetal til aldersopsparing (op til 58.900 kr/år hvis under 5 år til pension)",
            "5. Fri opsparing eller aktier ved yderligere opsparing",
        ],
        "topskat_graense_2025": 568_900,
        "note": "Pensionsindbetalinger reducerer den skattepligtige indkomst og kan trække dig under topskattegrænsen",
    },
    "vigtige_regler": [
        "Pensionsbeskatningsloven (PBL) §16: Loft for ratepension",
        "PBL §12: Livrente - ingen loft ved arbejdsgiveradministreret",
        "PBL §10: Aldersopsparing - afgiftsfri udbetaling",
        "Lov om arbejdsmarkedets tillægspension: ATP-regler",
        "Pensionsopspareren kan tidligst hæve fra PALFP (pensionsalderens fratrækningspunkt)",
    ],
    "forsikring_anbefalinger": {
        "invalide_daekning": "Bør dække min. 60-80% af nettoløn frem til pension",
        "livsforsikring": "Afhænger af forsørgerpligt og gæld. Vurder om dækning til 65/70 år er tilstrækkelig",
        "kritisk_sygdom": "Engangsudbetaling ved alvorlig diagnose - dækker ekstraudgifter og eventuelt tab af erhvervsevne i overgangsperiode",
    },
}
