import discord
from discord import app_commands
from discord.ext import commands
import os


def get_allowed_ids() -> list[int]:
    """
    Récupère la liste des IDs autorisés à utiliser /mp depuis les variables d'environnement.
    Format dans .env : ALLOWED_IDS=123456789,987654321
    """
    raw = os.getenv("ALLOWED_IDS", "")
    if not raw:
        return []
    return [int(id_.strip()) for id_ in raw.split(",") if id_.strip().isdigit()]


def get_log_channel_id() -> int:
    """Récupère l'ID du salon de logs depuis les variables d'environnement."""
    return int(os.getenv("LOG_CHANNEL_ID", "1408354449172463686"))


class MPModal(discord.ui.Modal, title="Envoyer un message privé"):
    """Formulaire (modal) qui s'ouvre pour saisir le contenu du MP."""

    contenu = discord.ui.TextInput(
        label="Contenu du message",
        style=discord.TextStyle.long,
        placeholder="Écris ici le message à envoyer...",
        required=True,
        max_length=2000
    )

    def __init__(self, membre: discord.Member):
        super().__init__()
        self.membre = membre  # Le membre qui va recevoir le MP

    async def on_submit(self, interaction: discord.Interaction):
        contenu = self.contenu.value

        # Tentative d'envoi du MP au membre
        try:
            await self.membre.send(contenu)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ Impossible d'envoyer un MP à **{self.membre}** (MPs désactivés ou bot bloqué).",
                ephemeral=True
            )
            return
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Une erreur est survenue : {e}",
                ephemeral=True
            )
            return

        # Confirmation éphémère à celui qui a utilisé la commande
        await interaction.response.send_message(
            f"✅ Message envoyé à **{self.membre}** (`{self.membre.id}`).",
            ephemeral=True
        )

        # Envoi de la notification dans le salon de logs
        log_channel_id = get_log_channel_id()
        log_channel = interaction.guild.get_channel(log_channel_id)

        if log_channel:
            embed = discord.Embed(
                title="📨 Nouveau MP envoyé",
                color=discord.Color.blue()
            )
            embed.add_field(name="Destinataire", value=f"{self.membre.mention} (`{self.membre.id}`)", inline=False)
            embed.add_field(name="Envoyé par", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
            embed.add_field(name="Contenu", value=contenu, inline=False)
            embed.set_footer(text=f"ID du message : Non disponible via API Discord")

            await log_channel.send(embed=embed)


class MPCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="mp", description="Envoyer un message privé à un membre (staff uniquement)")
    @app_commands.describe(utilisateur_id="L'identifiant Discord du membre à contacter")
    async def mp(self, interaction: discord.Interaction, utilisateur_id: str):
        # Vérification si l'utilisateur est dans la liste des IDs autorisés
        allowed_ids = get_allowed_ids()
        if interaction.user.id not in allowed_ids:
            await interaction.response.send_message(
                "❌ Tu n'es pas autorisé à utiliser cette commande.",
                ephemeral=True
            )
            return

        # Vérification que l'ID est bien un nombre
        if not utilisateur_id.strip().isdigit():
            await interaction.response.send_message(
                "❌ L'identifiant fourni n'est pas valide. Il doit être composé uniquement de chiffres.",
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

        # Ouverture du formulaire pour saisir le message
        await interaction.response.send_modal(MPModal(membre=membre))