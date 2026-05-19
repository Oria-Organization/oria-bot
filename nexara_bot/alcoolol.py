import re
import unicodedata


def est_voyelle(c: str) -> bool:
    return c.lower() in "aeiouy"


def enlever_accents(texte: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', texte)
        if unicodedata.category(c) != 'Mn'
    )


def traduire_mot(mot: str) -> str:
    if not mot:
        return mot

    derniere = mot[-1]
    base = mot[:-1]

    if est_voyelle(derniere):
        return base + "ol"
    return base + derniere + "ol"


# mots uniquement (pas emojis, pas ponctuation)
WORD_REGEX = r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+"


def traduire_texte(texte: str) -> str:
    texte = enlever_accents(texte)

    # on remplace uniquement les mots, tout le reste reste intact
    def replacer(match):
        mot = match.group(0)
        return traduire_mot(mot)

    return re.sub(WORD_REGEX, replacer, texte)