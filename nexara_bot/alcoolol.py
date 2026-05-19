import re
import unicodedata


def est_voyelle(c: str) -> bool:
    return c.lower() in "aeiouy"


def enlever_accents(text: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


# uniquement mots "propres"
WORD_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+(?:'[A-Za-zÀ-ÖØ-öø-ÿ]+)*$")


def traduire_mot(mot: str) -> str:
    if not mot:
        return mot

    derniere = mot[-1]
    base = mot[:-1]

    if est_voyelle(derniere):
        return base + "ol"
    return base + derniere + "ol"


def traduire_texte(texte: str) -> str:
    texte = enlever_accents(texte)

    tokens = re.findall(r"\s+|[^\s]+", texte)

    resultat = []

    for t in tokens:

        # espaces conservés
        if t.isspace():
            resultat.append(t)
            continue

        # on ne traite QUE les vrais mots
        if WORD_RE.match(t):
            resultat.append(traduire_mot(t))
        else:
            # tout le reste ignoré totalement
            resultat.append(t)

    return "".join(resultat)