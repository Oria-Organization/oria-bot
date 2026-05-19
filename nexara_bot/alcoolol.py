import re

def est_voyelle(c: str) -> bool:
    return c.lower() in "aeiouy"


def traduire_mot(mot: str) -> str:
    if not mot:
        return mot

    derniere = mot[-1]
    base = mot[:-1]

    if est_voyelle(derniere):
        return base + "ol"
    return base + derniere + "ol"


# tokens propres Discord
TOKEN_REGEX = r"(<@!?\d+>|<#\d+>|https?://\S+|[^\s]+)"


def traduire_texte(texte: str) -> str:
    tokens = re.findall(TOKEN_REGEX, texte)

    resultat = []

    for t in tokens:

        # mentions Discord intactes
        if t.startswith("<@") or t.startswith("<#"):
            resultat.append(t)

        # liens intacts
        elif t.startswith("http"):
            resultat.append(t)

        # emojis (approx Unicode simple)
        elif len(t) == 1 and not t.isalnum():
            resultat.append(t)

        # mots normaux
        else:
            resultat.append(traduire_mot(t))

    return " ".join(resultat)