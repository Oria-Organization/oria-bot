import discord
from discord import app_commands
from discord.ext import commands,tasks
import os

from nexara_bot.logs import send_log, build_log, setup_dm_listener
from nexara_bot import wiki as wiki_module
from nexara_bot import blacklist as bl_module


# ----------------------------
# Intents & configuration bot
# ----------------------------

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

statuses = [
    "Wiki : https://oria-organization.github.io/oria-wiki/",
    "{members} membres",
    "{guilds} serveurs",
    "Version 0.3.6"
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
# Commandes slash
# ----------------------------

@bot.tree.command(name="mp", description="Envoyer un message privé à un membre (staff uniquement)")
@app_commands.describe(utilisateur_id="L'identifiant Discord du membre à contacter")
async def mp(interaction: discord.Interaction, utilisateur_id: str):
    if interaction.user.id not in get_allowed_ids():
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

        embed = build_log(
            title="📨 Nouveau MP envoyé",
            color=discord.Color.blue(),
            fields=[
                ("Destinataire", f"{membre.mention} (`{membre.id}`)", False),
                ("Envoyé par", f"{inter.user.mention} (`{inter.user.id}`)", False),
                ("Contenu", contenu, False),
            ]
        )
        await send_log(inter.guild, embed)

    await interaction.response.send_modal(
        TextModal(
            title="Envoyer un message privé",
            label="Contenu du message",
            placeholder="Écris ici le message à envoyer...",
            callback=envoyer_mp
        )
    )


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


@bot.tree.command(name="blacklist-staff", description="Blacklister un membre du staff (retire ses rôles élevés sur tous les serveurs)")
@app_commands.describe(
    utilisateur_id="ID Discord du membre à blacklister",
    raison="Raison de la blacklist",
    image1="Preuve 1 (optionnelle)",
    image2="Preuve 2 (optionnelle)",
    image3="Preuve 3 (optionnelle)",
)
async def blacklist_staff(
    interaction: discord.Interaction,
    utilisateur_id: str,
    raison: str,
    image1: discord.Attachment = None,
    image2: discord.Attachment = None,
    image3: discord.Attachment = None,
):
    if interaction.user.id not in get_allowed_ids():
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
async def blacklist(
    interaction: discord.Interaction,
    utilisateur_id: str,
    raison: str,
    image1: discord.Attachment = None,
    image2: discord.Attachment = None,
    image3: discord.Attachment = None,
):
    if interaction.user.id not in get_allowed_ids():
        await interaction.response.send_message("❌ Tu n'es pas autorisé à utiliser cette commande.", ephemeral=True)
        return
    await bl_module.cmd_blacklist_ban(interaction, utilisateur_id, raison, image1, image2, image3, bot)


@bot.tree.command(name="blacklists", description="Consulter la fiche d'un membre blacklisté")
@app_commands.describe(utilisateur="Le membre blacklisté à consulter")
async def blacklists(interaction: discord.Interaction, utilisateur: str):
    await bl_module.cmd_blacklists(interaction, utilisateur, get_allowed_ids())


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
async def unbl(interaction: discord.Interaction, utilisateur_id: str, raison: str):
    if interaction.user.id not in get_allowed_ids():
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
    print("-> Cache wiki chargé.")


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