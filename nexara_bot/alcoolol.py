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


# IMPORTANT : on capture séparément mentions / liens / mots
TOKEN_REGEX = r"(<@!?\d+>|<#\d+>|https?://\S+|[A-Za-z0-9À-ÿ']+|[^\w\s])"


def traduire_texte(texte: str) -> str:
    texte = enlever_accents(texte)

    tokens = re.findall(TOKEN_REGEX, texte)

    resultat = []

    for t in tokens:

        # ❌ jamais toucher aux mentions
        if t.startswith("<@") or t.startswith("<#"):
            resultat.append(t)

        # liens intacts
        elif t.startswith("http"):
            resultat.append(t)

        # mots → traduction
        elif re.match(r"[A-Za-z0-9À-ÿ']+", t):
            resultat.append(traduire_mot(t))

        # ponctuation / emojis / x) :)
        else:
            resultat.append(t)

    return "".join(resultat)