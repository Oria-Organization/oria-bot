import discord
import os


def get_log_channel_id() -> int:
    """Récupère l'ID du salon de logs depuis les variables d'environnement."""
    return int(os.getenv("LOG_CHANNEL_ID", "0"))


def get_log_guild_id() -> int:
    """Récupère l'ID du serveur de logs depuis les variables d'environnement."""
    return int(os.getenv("LOG_GUILD_ID", "0"))


async def send_log(guild: discord.Guild, embed: discord.Embed) -> None:
    """
    Envoie un embed dans le salon de logs.
    Utilise LOG_GUILD_ID + LOG_CHANNEL_ID pour trouver le bon salon,
    peu importe depuis quel serveur la commande a été exécutée.

    Paramètres :
        guild  -> L'instance du bot (ou n'importe quel guild, utilisé pour accéder au bot)
        embed  -> L'embed à envoyer
    """
    log_channel_id = get_log_channel_id()
    log_guild_id = get_log_guild_id()

    # On récupère le bon serveur via le bot
    bot = guild._state._get_client()
    log_guild = bot.get_guild(log_guild_id)

    if log_guild is None:
        print(f"-> Serveur de logs introuvable (ID: {log_guild_id})")
        return

    log_channel = log_guild.get_channel(log_channel_id)

    if log_channel is None:
        print(f"-> Salon de logs introuvable (ID: {log_channel_id}) sur le serveur {log_guild.name}")
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


def setup_dm_listener(bot: discord.Client) -> None:
    """
    Active l'écoute des MPs reçus par le bot et les log dans le salon de logs.
    À appeler une fois au démarrage du bot (dans on_ready ou setup_hook).

    Paramètres :
        bot -> L'instance du bot Discord
    """

    @bot.event
    async def on_message(message: discord.Message):
        # On ignore les messages du bot lui-même
        if message.author == bot.user:
            return

        # On vérifie que c'est bien un MP (DM) et pas un message de serveur
        if not isinstance(message.channel, discord.DMChannel):
            await bot.process_commands(message)
            return

        # On utilise le serveur de logs directement plutôt que le premier serveur en commun
        log_guild_id = get_log_guild_id()
        guild = bot.get_guild(log_guild_id)

        if guild is None:
            print(f"-> MP reçu de {message.author} mais serveur de logs introuvable.")
            return

        # Construction du log
        contenu = message.content or "*[Message sans texte - peut contenir un fichier ou une image]*"

        embed = build_log(
            title="📩 MP reçu par le bot",
            color=discord.Color.orange(),
            fields=[
                ("Expéditeur", f"{message.author.mention} (`{message.author.id}`)", False),
                ("Nom d'utilisateur", str(message.author), False),
                ("Contenu", contenu, False),
            ]
        )

        # Ajout des pièces jointes si présentes
        if message.attachments:
            attachments_list = "\n".join([a.url for a in message.attachments])
            embed.add_field(name="Pièces jointes", value=attachments_list, inline=False)

        await send_log(guild, embed)
        await bot.process_commands(message)