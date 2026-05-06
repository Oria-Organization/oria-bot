import discord
import os


def get_log_channel_id() -> int:
    """Récupère l'ID du salon de logs depuis les variables d'environnement."""
    return int(os.getenv("LOG_CHANNEL_ID", "1408354449172463686"))


async def send_log(guild: discord.Guild, embed: discord.Embed) -> None:
    """
    Envoie un embed dans le salon de logs.

    Paramètres :
        guild  -> Le serveur Discord
        embed  -> L'embed à envoyer
    """
    log_channel_id = get_log_channel_id()
    log_channel = guild.get_channel(log_channel_id)

    if log_channel is None:
        print(f"-> Salon de logs introuvable (ID: {log_channel_id})")
        return

    try:
        await log_channel.send(embed=embed)
    except discord.Forbidden:
        print(f"-> Permission refusée pour envoyer dans le salon de logs (ID: {log_channel_id})")
    except Exception as e:
        print(f"-> Erreur lors de l'envoi du log : {e}")


def build_log(title: str, color: discord.Color, fields: list[tuple[str, str, bool]] = []) -> discord.Embed:
    """
    Construit un embed de log générique.

    Paramètres :
        title  -> Titre de l'embed
        color  -> Couleur de l'embed
        fields -> Liste de tuples (nom, valeur, inline)

    Exemple d'utilisation :
        embed = build_log(
            title="📨 Nouveau MP envoyé",
            color=discord.Color.blue(),
            fields=[
                ("Destinataire", f"{membre.mention} (`{membre.id}`)", False),
                ("Envoyé par", f"{user.mention} (`{user.id}`)", False),
                ("Contenu", contenu, False),
            ]
        )
    """
    embed = discord.Embed(title=title, color=color)
    for name, value, inline in fields:
        embed.add_field(name=name, value=value, inline=inline)
    return embed
