import discord
from discord import app_commands
from discord.ext import commands
import os

from nexara_bot.logs import send_log, build_log, setup_dm_listener
from nexara_bot import wiki as wiki_module


# ----------------------------
# Intents & configuration bot
# ----------------------------

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ----------------------------
# Utilitaires
# ----------------------------

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
    # Vérification des permissions
    if interaction.user.id not in get_allowed_ids():
        await interaction.response.send_message(
            "❌ Tu n'es pas autorisé à utiliser cette commande.",
            ephemeral=True
        )
        return

    # Vérification du format de l'ID
    if not utilisateur_id.strip().isdigit():
        await interaction.response.send_message(
            "❌ L'identifiant fourni n'est pas valide (chiffres uniquement).",
            ephemeral=True
        )
        return

    # Recherche du membre sur le serveur
    membre = interaction.guild.get_member(int(utilisateur_id))
    if not membre:
        await interaction.response.send_message(
            f"❌ Aucun membre avec l'ID `{utilisateur_id}` trouvé sur ce serveur.",
            ephemeral=True
        )
        return

    # Callback appelé une fois le modal soumis
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

        # Log dans le salon dédié
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

    # Ouverture du modal
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
    """Charge dynamiquement les titres depuis GitHub à chaque appel."""
    choices = await wiki_module.get_autocomplete_choices()
    return [
        app_commands.Choice(name=c["name"], value=c["value"])
        for c in choices
        if current.lower() in c["name"].lower()
    ][:25]  # Discord limite à 25 choix


# ----------------------------
# Événements
# ----------------------------

@bot.event
async def on_ready():
    await bot.tree.sync()
    setup_dm_listener(bot)
    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"V. 0.1.1.2 | {len(bot.guilds)} serveur(s)"
        )
    )
    print(f"-> Bot connecté en tant que {bot.user} (ID: {bot.user.id})")
    # Préchargement du cache wiki dès le démarrage pour que l'autocomplétion
    # soit instantanée dès la première utilisation de /wiki
    await wiki_module.get_autocomplete_choices()
    print("-> Cache wiki chargé.")
