import re
import unicodedata


def est_voyelle(c: str) -> bool:
    return c.lower() in "aeiouy"


def enlever_accents(text: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


WORD_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+(?:'[A-Za-zÀ-ÖØ-öø-ÿ]+)*$")


# ----------------------------
# MODE STANDARD (ton actuel)
# ----------------------------
def traduire_standard(mot: str) -> str:
    if not mot:
        return mot

    derniere = mot[-1]
    base = mot[:-1]

    if est_voyelle(derniere):
        return base + "ol"
    return base + derniere + "ol"


# ----------------------------
# MODE AVANCÉ
# règle: chaque lettre devient lettre + ol (avec logique voyelle)
# ----------------------------
def traduire_avance(mot: str) -> str:
    result = []

    for c in mot:
        if est_voyelle(c):
            result.append("ol")
        else:
            result.append(c + "ol")

    return "".join(result)

def traduire_texte(texte: str, mode: str = "standard") -> str:
    texte = enlever_accents(texte)

    tokens = re.findall(r"\s+|[^\s]+", texte)

    resultat = []

    for t in tokens:

        if t.isspace():
            resultat.append(t)
            continue

        if WORD_RE.match(t):
            if mode == "avance":
                resultat.append(traduire_avance(t))
            else:
                resultat.append(traduire_standard(t))
        else:
            resultat.append(t)

    return "".join(resultat)