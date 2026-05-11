# nexara_bot/wiki.py
# Logique de récupération et d'affichage du wiki depuis GitHub

import aiohttp
import discord
import base64
import json
from typing import Optional

GITHUB_API_BASE = "https://api.github.com/repos/oria-organization/oria-wiki"
RAW_BASE = "https://raw.githubusercontent.com/oria-organization/oria-wiki/main"
DOSSIERS_ENDPOINT = f"{GITHUB_API_BASE}/contents/dossiers"

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# ---------------------------------------------------------------------------
# Récupération des données GitHub
# ---------------------------------------------------------------------------

async def fetch_dossiers(session: aiohttp.ClientSession) -> list[str]:
    """Retourne la liste des noms de dossiers disponibles dans /dossiers."""
    async with session.get(DOSSIERS_ENDPOINT, headers=HEADERS) as resp:
        if resp.status == 429:
            raise RuntimeError("GitHub API rate limit atteint. Réessaie dans quelques minutes.")
        if resp.status != 200:
            raise RuntimeError(f"Impossible de récupérer les dossiers (HTTP {resp.status}).")
        data = await resp.json()
    return [item["name"] for item in data if item["type"] == "dir"]


async def fetch_index(session: aiohttp.ClientSession, dossier: str) -> dict:
    """Récupère et parse le index.json d'un dossier donné."""
    url = f"{GITHUB_API_BASE}/contents/dossiers/{dossier}/contenu/index.json"
    async with session.get(url, headers=HEADERS) as resp:
        if resp.status == 429:
            raise RuntimeError("GitHub API rate limit atteint.")
        if resp.status == 404:
            return {}
        if resp.status != 200:
            return {}
        data = await resp.json()

    # Le contenu est encodé en base64 par l'API GitHub
    content_b64 = data.get("content", "")
    content_bytes = base64.b64decode(content_b64)
    try:
        return json.loads(content_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


async def fetch_md_content(session: aiohttp.ClientSession, dossier: str, fichier: str) -> str:
    """Récupère le contenu brut d'un fichier .md."""
    # On s'assure que le nom de fichier n'a pas déjà l'extension
    if not fichier.endswith(".md"):
        fichier = fichier + ".md"
    url = f"{RAW_BASE}/dossiers/{dossier}/contenu/{fichier}"
    async with session.get(url) as resp:
        if resp.status == 429:
            raise RuntimeError("GitHub API rate limit atteint.")
        if resp.status == 404:
            raise FileNotFoundError(f"Fichier introuvable : `{dossier}/{fichier}`")
        if resp.status != 200:
            raise RuntimeError(f"Impossible de récupérer le fichier (HTTP {resp.status}).")
        return await resp.text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Construction de l'autocomplétion (avec cache en mémoire)
# ---------------------------------------------------------------------------

# Cache global : liste de dicts {name, value}, None = pas encore chargé
_choices_cache: Optional[list[dict]] = None


async def _build_choices() -> list[dict]:
    """Charge tous les choix depuis GitHub (appelé une seule fois)."""
    choices = []
    async with aiohttp.ClientSession() as session:
        try:
            dossiers = await fetch_dossiers(session)
        except RuntimeError:
            return []

        for dossier in dossiers:
            index = await fetch_index(session, dossier)
            fichiers: list[dict] = index.get("fichiers", [])

            for entry in fichiers:
                fichier_name: str = entry.get("fichier", "")
                if not fichier_name:
                    continue

                # Titre : champ "titre" dans index.json OU première ligne # du .md
                titre: Optional[str] = entry.get("titre")
                if not titre:
                    try:
                        md = await fetch_md_content(session, dossier, fichier_name)
                        titre = _extract_title_from_md(md)
                    except (FileNotFoundError, RuntimeError):
                        titre = fichier_name

                if not fichier_name.endswith(".md"):
                    fichier_name = fichier_name + ".md"

                display = f"{dossier.replace('_', ' ').title()} — {titre}"
                value = f"{dossier}/{fichier_name}"

                choices.append({
                    "name": display[:100],
                    "value": value[:100],
                })

    return choices


async def get_autocomplete_choices() -> list[dict]:
    """
    Retourne la liste des choix pour l'autocomplétion.
    Utilise le cache en mémoire — GitHub n'est appelé qu'une seule fois
    pour tout le cycle de vie du bot.
    """
    global _choices_cache
    if _choices_cache is None:
        _choices_cache = await _build_choices()
    return _choices_cache


async def refresh_wiki_cache() -> None:
    """
    Force le rechargement du cache depuis GitHub.
    Utile si le wiki est mis à jour sans redémarrer le bot.
    """
    global _choices_cache
    _choices_cache = None
    _choices_cache = await _build_choices()


def _extract_title_from_md(content: str) -> str:
    """Extrait la première ligne commençant par # comme titre."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return "Sans titre"


# ---------------------------------------------------------------------------
# Conversion Markdown → liste d'Embeds Discord (pagination)
# ---------------------------------------------------------------------------

def md_to_embeds(content: str, colour: discord.Colour = discord.Colour.blurple()) -> list[discord.Embed]:
    """
    Convertit un fichier Markdown en une liste d'embeds Discord paginés.

    Règles :
    - Le titre de l'embed principal = premier # du fichier
    - Chaque ## crée un field dans l'embed
    - Si le total dépasse ~5800 caractères, on coupe en plusieurs embeds
    - Les boutons Précédent/Suivant sont gérés par WikiView
    """
    lines = content.splitlines()
    title = "Document"
    sections: list[tuple[str, str]] = []  # (sous-titre, contenu)

    current_heading: Optional[str] = None
    current_lines: list[str] = []
    intro_lines: list[str] = []
    found_title = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("# ") and not found_title:
            title = stripped[2:].strip()
            found_title = True
            continue

        if stripped.startswith("## "):
            if current_heading is None:
                # On sauvegarde l'intro (texte avant le premier ##)
                intro_text = "\n".join(intro_lines).strip()
                if intro_text:
                    sections.append(("\u200b", intro_text))
            else:
                sections.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = stripped[3:].strip()
            current_lines = []
        else:
            if current_heading is None:
                intro_lines.append(line)
            else:
                current_lines.append(line)

    # Dernière section
    if current_heading is not None:
        sections.append((current_heading, "\n".join(current_lines).strip()))
    elif intro_lines:
        intro_text = "\n".join(intro_lines).strip()
        if intro_text:
            sections.append(("\u200b", intro_text))

    # --- Construction des embeds paginés ---
    MAX_EMBED_CHARS = 5800  # marge de sécurité sous la limite Discord de 6000
    MAX_FIELD_VALUE = 1024

    embeds: list[discord.Embed] = []
    current_embed = discord.Embed(title=title, colour=colour)
    current_chars = len(title)

    def _truncate(text: str, limit: int = MAX_FIELD_VALUE) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    for (heading, body) in sections:
        if not body:
            body = "*— vide —*"

        field_chars = len(heading) + len(body[:MAX_FIELD_VALUE])

        # Si ce field fait déborder l'embed courant → nouvel embed
        if current_chars + field_chars > MAX_EMBED_CHARS and current_embed.fields:
            embeds.append(current_embed)
            current_embed = discord.Embed(title=f"{title} (suite)", colour=colour)
            current_chars = len(title) + 7  # "(suite)"

        current_embed.add_field(
            name=heading,
            value=_truncate(body),
            inline=False,
        )
        current_chars += field_chars

    if current_embed.fields or not embeds:
        embeds.append(current_embed)

    # Numérotation des pages
    total = len(embeds)
    for i, embed in enumerate(embeds):
        embed.set_footer(text=f"Page {i + 1} / {total}")

    return embeds


# ---------------------------------------------------------------------------
# Vue de pagination
# ---------------------------------------------------------------------------

class WikiView(discord.ui.View):
    """Vue Discord avec boutons Précédent / Suivant pour paginer les embeds."""

    def __init__(self, embeds: list[discord.Embed], timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.current_page = 0
        self._update_buttons()

    def _update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.embeds) - 1

    @discord.ui.button(label="◀ Précédent", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="Suivant ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    async def on_timeout(self):
        """Désactive les boutons à l'expiration du timeout."""
        for item in self.children:
            item.disabled = True
        # La vue est expirée ; le message ne peut plus être modifié ici sans la référence au message,
        # mais discord.py s'en charge si message= est passé lors du send.


# ---------------------------------------------------------------------------
# Point d'entrée principal : afficher un document wiki
# ---------------------------------------------------------------------------

async def display_wiki_document(
    interaction: discord.Interaction,
    document_value: str,
) -> None:
    """
    Récupère et affiche un document wiki dans le channel.
    document_value : chemin "dossier/fichier.md"
    """
    parts = document_value.split("/", 1)
    if len(parts) != 2:
        await interaction.response.send_message(
            "❌ Identifiant de document invalide.", ephemeral=True
        )
        return

    dossier, fichier = parts

    await interaction.response.defer()  # peut prendre un peu de temps

    async with aiohttp.ClientSession() as session:
        try:
            content = await fetch_md_content(session, dossier, fichier)
        except FileNotFoundError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except RuntimeError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

    embeds = md_to_embeds(content)

    if len(embeds) == 1:
        await interaction.followup.send(embed=embeds[0])
    else:
        view = WikiView(embeds)
        msg = await interaction.followup.send(embed=embeds[0], view=view)
        # Optionnel : stocker la référence au message pour désactiver les boutons au timeout
        view.message = msg
