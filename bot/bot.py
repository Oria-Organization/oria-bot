import discord
from discord.ext import commands
import os


# Intents nécessaires pour accéder aux membres du serveur
intents = discord.Intents.default()
intents.members = True


class OriaBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",  # Pas vraiment utilisé car on utilise les slash commands
            intents=intents
        )

    async def setup_hook(self):
        # Chargement automatique de tous les cogs dans bot/cogs/
        from bot.cogs.mp import MPCog
        await self.add_cog(MPCog(self))

        # Synchronisation des slash commands avec Discord
        await self.tree.sync()
        print("-> Slash commands synchronisées.")

    async def on_ready(self):
        print(f"-> Bot connecté en tant que {self.user} (ID: {self.user.id})")


bot = OriaBot()