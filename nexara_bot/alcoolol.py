def est_voyelle(c: str) -> bool:
    return c.lower() in "aeiouy"


def traduire_mot(mot: str) -> str:
    if not mot:
        return mot

    derniere = mot[-1]
    base = mot[:-1]

    # dernière lettre voyelle → remplacée par ol
    if est_voyelle(derniere):
        return base + "ol"

    # consonne → on garde + ol
    return base + derniere + "ol"


def traduire_texte(texte: str) -> str:
    mots = texte.split()
    return " ".join(traduire_mot(m) for m in mots)