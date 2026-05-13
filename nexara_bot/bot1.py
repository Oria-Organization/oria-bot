import discord
from discord import app_commands
from discord.ext import commands, tasks
import os

from nexara_bot.logs import send_log, build_log, setup_dm_listener
from nexara_bot import wiki as wiki_module
from nexara_bot import blacklist as bl_module

# ----------------------------
# IDs staff (depuis .env)
# STAFF_GUILD_ID=1376905559671832748
# STAFF_ROLE_ID=1380133720748462090
# ----------------------------

def get_staff_guild_id() -> int:
    return int(os.getenv("STAFF_GUILD_ID", "0"))

def get_staff_role_id() -> int:
    return int(os.getenv("STAFF_ROLE_ID", "0"))


# ----------------------------
# Intents & configuration bot
# ----------------------------

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

statuses = [
    "{members} membres",
    "{guilds} serveurs",
    "Version 0.4.0"
]

status_index = 0


# ----------------------------
# Utilitaires
# ----------------------------

def get_unique_member_count(bot: commands.Bot) -> int:
    unique_ids = set()
    for guild in bot.guilds:
        for member in guild.members:
            unique_ids.add(member.id)
    return len(unique_ids)


def get_allowed_ids() -> list[int]:
    """
    Récupère la liste des IDs autorisés à utiliser les commandes restreintes.
    Format dans .env : ALLOWED_IDS=123456789,987654321
    """
    raw = os.getenv("ALLOWED_IDS", "")
    if not raw:
        return []
    return [int(id_.strip()) for id_ in raw.split(",") if id_.strip().isdigit()]


def get_log_guild(bot: commands.Bot) -> discord.Guild | None:
    """
    Retourne directement le serveur de logs via LOG_GUILD_ID.
    Utilisé pour garantir l'envoi des logs peu importe le serveur
    depuis lequel une commande a été exécutée.
    """
    raw = os.getenv("LOG_GUILD_ID", "0")
    guild_id = int(raw) if raw.isdigit() else 0
    return bot.get_guild(guild_id)


def is_staff(user: discord.User | discord.Member) -> bool:
    """
    Retourne True si l'utilisateur possède le rôle staff sur le serveur principal.
    Fonctionne même si la commande est lancée depuis un autre serveur.
    """
    staff_guild = bot.get_guild(get_staff_guild_id())
    if staff_guild is None:
        return False
    member = staff_guild.get_member(user.id)
    if member is None:
        return False
    return any(role.id == get_staff_role_id() for role in member.roles)


# ----------------------------
# Modals génériques
# ----------------------------

class TextModal(discord.ui.Modal):
    """
    Modal générique à texte long, réutilisable pour n'importe quelle commande.

    Paramètres :
        title       -> Titre du modal
        label       -> Label du champ texte
        placeholder -> Texte d'aide affiché dans le champ vide
        callback    -> Fonction async appelée avec (interaction, contenu)
    """

    def __init__(self, title: str, label: str, placeholder: str, callback):
        super().__init__(title=title)
        self.champ = discord.ui.TextInput(
            label=label,
            style=discord.TextStyle.long,
            placeholder=placeholder,
            required=True,
            max_length=2000
        )
        self.add_item(self.champ)
        self._callback = callback

    async def on_submit(self, interaction: discord.Interaction):
        await self._callback(interaction, self.champ.value)


# ----------------------------
# Commandes slash — publiques
# ----------------------------

@bot.tree.command(name="wiki", description="Consulter un document du wiki Oria")
@app_commands.describe(document="Le document à afficher")
async def wiki(interaction: discord.Interaction, document: str):
    await wiki_module.display_wiki_document(interaction, document)


@wiki.autocomplete("document")
async def wiki_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    choices = await wiki_module.get_autocomplete_choices()
    return [
        app_commands.Choice(name=c["name"], value=c["value"])
        for c in choices
        if current.lower() in c["name"].lower()
    ][:25]


# ----------------------------
# Commandes slash — staff uniquement
# @app_commands.default_permissions(manage_guild=True) masque la commande
# dans la barre slash pour tous les membres sans permission Administrateur.
# La double vérification via ALLOWED_IDS reste active pour la sécurité.
# ----------------------------

@bot.tree.command(name="mp", description="Envoyer un message privé à un membre (staff uniquement)")
@app_commands.describe(utilisateur_id="L'identifiant Discord du membre à contacter")
@app_commands.default_permissions(manage_guild=True)
async def mp(interaction: discord.Interaction, utilisateur_id: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'es pas autorisé à utiliser cette commande.",
            ephemeral=True
        )
        return

    if not utilisateur_id.strip().isdigit():
        await interaction.response.send_message(
            "❌ L'identifiant fourni n'est pas valide (chiffres uniquement).",
            ephemeral=True
        )
        return

    membre = interaction.guild.get_member(int(utilisateur_id))
    if not membre:
        await interaction.response.send_message(
            f"❌ Aucun membre avec l'ID `{utilisateur_id}` trouvé sur ce serveur.",
            ephemeral=True
        )
        return

    async def envoyer_mp(inter: discord.Interaction, contenu: str):
        try:
            await membre.send(contenu)
        except discord.Forbidden:
            await inter.response.send_message(
                f"❌ Impossible d'envoyer un MP à **{membre}** (MPs désactivés ou bot bloqué).",
                ephemeral=True
            )
            return
        except Exception as e:
            await inter.response.send_message(f"❌ Erreur : {e}", ephemeral=True)
            return

        await inter.response.send_message(
            f"✅ Message envoyé à **{membre}** (`{membre.id}`).",
            ephemeral=True
        )

        # Fix multi-serveur : on passe le serveur de logs directement
        # pour garantir l'envoi même si la commande vient d'un autre serveur
        log_guild = get_log_guild(bot)
        if log_guild:
            embed = build_log(
                title="📨 Nouveau MP envoyé",
                color=discord.Color.blue(),
                fields=[
                    ("Destinataire", f"{membre.mention} (`{membre.id}`)", False),
                    ("Envoyé par", f"{inter.user.mention} (`{inter.user.id}`)", False),
                    ("Depuis le serveur", f"{inter.guild.name} (`{inter.guild.id}`)" if inter.guild else "Inconnu", False),
                    ("Contenu", contenu, False),
                ]
            )
            await send_log(log_guild, embed)

    await interaction.response.send_modal(
        TextModal(
            title="Envoyer un message privé",
            label="Contenu du message",
            placeholder="Écris ici le message à envoyer...",
            callback=envoyer_mp
        )
    )


@bot.tree.command(name="wiki-refresh", description="Recharger le cache du wiki depuis GitHub (staff uniquement)")
@app_commands.default_permissions(manage_guild=True)
async def wiki_refresh(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'es pas autorisé à utiliser cette commande.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    await wiki_module.refresh_wiki_cache()

    log_guild = get_log_guild(bot)
    if log_guild:
        embed = build_log(
            title="🔄 Cache wiki rechargé",
            color=discord.Color.teal(),
            fields=[
                ("Rechargé par", f"{interaction.user.mention} (`{interaction.user.id}`)", False),
            ]
        )
        await send_log(log_guild, embed)

    await interaction.followup.send("✅ Cache wiki rechargé avec succès.", ephemeral=True)


@bot.tree.command(name="blacklist-staff", description="Blacklister un membre du staff (retire ses rôles élevés sur tous les serveurs)")
@app_commands.describe(
    utilisateur_id="ID Discord du membre à blacklister",
    raison="Raison de la blacklist",
    image1="Preuve 1 (optionnelle)",
    image2="Preuve 2 (optionnelle)",
    image3="Preuve 3 (optionnelle)",
)
@app_commands.default_permissions(manage_guild=True)
async def blacklist_staff(
    interaction: discord.Interaction,
    utilisateur_id: str,
    raison: str,
    image1: discord.Attachment = None,
    image2: discord.Attachment = None,
    image3: discord.Attachment = None,
):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Tu n'es pas autorisé à utiliser cette commande.", ephemeral=True)
        return
    await bl_module.cmd_blacklist_staff(interaction, utilisateur_id, raison, image1, image2, image3, bot)


@bot.tree.command(name="blacklist", description="Blacklister et bannir un membre de tous les serveurs")
@app_commands.describe(
    utilisateur_id="ID Discord du membre à blacklister",
    raison="Raison de la blacklist",
    image1="Preuve 1 (optionnelle)",
    image2="Preuve 2 (optionnelle)",
    image3="Preuve 3 (optionnelle)",
)
@app_commands.default_permissions(manage_guild=True)
async def blacklist(
    interaction: discord.Interaction,
    utilisateur_id: str,
    raison: str,
    image1: discord.Attachment = None,
    image2: discord.Attachment = None,
    image3: discord.Attachment = None,
):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Tu n'es pas autorisé à utiliser cette commande.", ephemeral=True)
        return
    await bl_module.cmd_blacklist_ban(interaction, utilisateur_id, raison, image1, image2, image3, bot)


@bot.tree.command(name="blacklists", description="Consulter la fiche d'un membre blacklisté")
@app_commands.describe(utilisateur="Le membre blacklisté à consulter")
async def blacklists(interaction: discord.Interaction, utilisateur: str):
    await bl_module.cmd_blacklists(interaction, utilisateur, is_staff)


@blacklists.autocomplete("utilisateur")
async def blacklists_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return await bl_module.blacklist_autocomplete(interaction, current)


@bot.tree.command(name="unbl", description="Retirer un membre de la blacklist")
@app_commands.describe(
    utilisateur_id="ID Discord du membre à retirer",
    raison="Raison du retrait",
)
@app_commands.default_permissions(manage_guild=True)
async def unbl(interaction: discord.Interaction, utilisateur_id: str, raison: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Tu n'es pas autorisé à utiliser cette commande.", ephemeral=True)
        return
    await bl_module.cmd_unbl(interaction, utilisateur_id, raison, bot)


# ----------------------------
# Événements
# ----------------------------

@bot.event
async def on_ready():
    await bot.tree.sync()
    setup_dm_listener(bot)

    if not change_status.is_running():
        change_status.start()

    print(f"-> Bot connecté en tant que {bot.user} (ID: {bot.user.id})")
    await wiki_module.get_autocomplete_choices()


@bot.event
async def on_member_join(member: discord.Member):
    """Rebannit immédiatement un ban-blacklisté qui tente de rejoindre un serveur."""
    await bl_module.enforce_ban_on_join(member)


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    """Rebannit un ban-blacklisté si quelqu'un tente de le débannir."""
    await bl_module.enforce_unban_attempt(bot, guild, user)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Retire les rôles élevés d'un staff-blacklisté dès qu'on tente de lui en attribuer."""
    await bl_module.enforce_staff_blacklist_on_update(before, after)


@tasks.loop(seconds=10)
async def change_status():
    global status_index

    members = get_unique_member_count(bot)

    text = statuses[status_index].format(
        guilds=len(bot.guilds),
        members=members
    )

    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=text
        )
    )

    status_index = (status_index + 1) % len(statuses)