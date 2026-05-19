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


# conserve emojis, mentions, salons, liens
TOKEN_REGEX = r"(<@!?\d+>|<#\d+>|https?://\S+|\w+|[^\w\s])"


def traduire_texte(texte: str) -> str:
    tokens = re.findall(TOKEN_REGEX, texte)

    resultat = []

    for t in tokens:
        # on ne touche pas aux mentions / emojis / liens
        if t.startswith("<@") or t.startswith("<#") or t.startswith("http"):
            resultat.append(t)
        elif len(t) == 1 and not t.isalnum():
            resultat.append(t)  # emoji / ponctuation
        else:
            resultat.append(traduire_mot(t))

    return "".join(resultat)