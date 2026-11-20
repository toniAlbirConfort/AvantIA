"""Geographic scope of the business.

AvantIA manages holiday apartments in Albir, a coastal area of the municipality of
l'Alfàs del Pi (Alicante, Comunitat Valenciana, Spain). Every location-dependent
calculation (public holidays, market comparables) uses this single scope instead of
per-property data entered by hand.
"""

COUNTRY = "España"
AUTONOMOUS_COMMUNITY = "Comunitat Valenciana"
PROVINCE = "Alicante"
MUNICIPALITY = "l'Alfàs del Pi"
LOCALITY = "Albir"

SCOPE_LABEL = f"{LOCALITY}, {MUNICIPALITY} ({PROVINCE}, {AUTONOMOUS_COMMUNITY}, {COUNTRY})"


def scope_view() -> dict:
    return {
        "country": COUNTRY,
        "autonomous_community": AUTONOMOUS_COMMUNITY,
        "province": PROVINCE,
        "municipality": MUNICIPALITY,
        "locality": LOCALITY,
        "label": SCOPE_LABEL,
    }
