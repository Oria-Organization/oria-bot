# nexara_bot/utilitaires.py
# Fonctions utilitaires générales pour le bot.

from datetime import datetime
from discord.ext import commands


def get_uptime(start_time: datetime) -> str:
    """
    Calcule le temps écoulé depuis le démarrage du bot.
    Retourne une chaîne formatée ex: "2j 3h 14m 5s"
    """
    delta = datetime.now() - start_time
    total_seconds = int(delta.total_seconds())

    jours    = total_seconds // 86400
    heures   = (total_seconds % 86400) // 3600
    minutes  = (total_seconds % 3600) // 60
    secondes = total_seconds % 60

    parts = []
    if jours:
        parts.append(f"{jours}j")
    if heures:
        parts.append(f"{heures}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secondes}s")

    return " ".join(parts)


def get_bot_stats(bot: commands.Bot, start_time: datetime, version: str) -> dict:
    """
    Rassemble toutes les statistiques du bot en un seul dict.

    Paramètres :
        bot        -> Instance du bot
        start_time -> Datetime du démarrage (défini dans on_ready)
        version    -> Version lue depuis la liste statuses de bot1.py

    Retourne :
        version       -> str
        latence_ms    -> float (arrondi à 1 décimale)
        uptime        -> str formaté
        serveurs      -> int
        membres       -> int (uniques)
        commandes     -> int
    """
    unique_members = set()
    for guild in bot.guilds:
        for member in guild.members:
            unique_members.add(member.id)

    return {
        "version"   : version,
        "latence_ms": round(bot.latency * 1000, 1),
        "uptime"    : get_uptime(start_time),
        "serveurs"  : len(bot.guilds),
        "membres"   : len(unique_members),
        "commandes" : len(bot.tree.get_commands()),
    }