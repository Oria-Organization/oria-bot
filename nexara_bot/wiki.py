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


async def fetch_index(session: aiohttp.ClientSession, dossier: str) -> list[str]:
    """Récupère et parse le index.json d'un dossier donné."""
    url = f"{GITHUB_API_BASE}/contents/dossiers/{dossier}/contenu/index.json"

    async with session.get(url, headers=HEADERS) as resp:
        if resp.status == 429:
            raise RuntimeError("GitHub API rate limit atteint.")

        if resp.status == 404:
            return []

        if resp.status != 200:
            return []

        data = await resp.json()

    # Le contenu est encodé en base64 par l'API GitHub
    content_b64 = data.get("content", "")
    content_bytes = base64.b64decode(content_b64)

    try:
        parsed = json.loads(content_bytes.decode("utf-8"))

        # index.json = liste simple
        if isinstance(parsed, list):
            return parsed

        return []

    except json.JSONDecodeError:
        return []


async def fetch_md_content(session: aiohttp.ClientSession, dossier: str, fichier: str) -> str:
    """Récupère le contenu brut d'un fichier .md."""

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

            for fichier_name in index:

                if not fichier_name:
                    continue

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
    """
    Extrait le titre depuis le frontmatter YAML :

    ---
    title: Mon titre
    ---

    Fallback :
    première ligne Markdown commençant par #
    """

    lines = content.splitlines()

    # -------------------------------------------------------------------
    # Frontmatter YAML
    # -------------------------------------------------------------------

    if lines and lines[0].strip() == "---":

        for line in lines[1:]:

            stripped = line.strip()

            # Fin du frontmatter
            if stripped == "---":
                break

            # title:
            if stripped.lower().startswith("title:"):

                title = stripped.split(":", 1)[1].strip()

                # retire les guillemets éventuels
                title = title.strip('"').strip("'")

                return title

    # -------------------------------------------------------------------
    # Fallback Markdown classique
    # -------------------------------------------------------------------

    for line in lines:

        stripped = line.strip()

        if stripped.startswith("# "):
            return stripped[2:].strip()

    return "Sans titre"


# ---------------------------------------------------------------------------
# Conversion Markdown → liste d'Embeds Discord (pagination)
# ---------------------------------------------------------------------------

def md_to_embeds(
    content: str,
    colour: discord.Colour = discord.Colour.blurple()
) -> list[discord.Embed]:

    """
    Convertit un fichier Markdown en une liste d'embeds Discord paginés.

    Règles :
    - Le titre de l'embed principal = titre YAML OU premier #
    - Chaque ## crée un field dans l'embed
    - Si le total dépasse ~5800 caractères, on coupe en plusieurs embeds
    """

    lines = content.splitlines()

    # -------------------------------------------------------------------
    # Titre
    # -------------------------------------------------------------------

    title = _extract_title_from_md(content)

    sections: list[tuple[str, str]] = []

    current_heading: Optional[str] = None
    current_lines: list[str] = []

    intro_lines: list[str] = []

    in_yaml = False

    for i, line in enumerate(lines):

        stripped = line.strip()

        # Ignore le frontmatter YAML
        if stripped == "---":

            if i == 0:
                in_yaml = True
                continue

            elif in_yaml:
                in_yaml = False
                continue

        if in_yaml:
            continue

        # Ignore le titre markdown principal
        if stripped.startswith("# "):
            continue

        # Sous-sections
        if stripped.startswith("## "):

            if current_heading is None:

                intro_text = "\n".join(intro_lines).strip()

                if intro_text:
                    sections.append(("\u200b", intro_text))

            else:

                sections.append((
                    current_heading,
                    "\n".join(current_lines).strip()
                ))

            current_heading = stripped[3:].strip()
            current_lines = []

        else:

            if current_heading is None:
                intro_lines.append(line)

            else:
                current_lines.append(line)

    # Dernière section
    if current_heading is not None:

        sections.append((
            current_heading,
            "\n".join(current_lines).strip()
        ))

    elif intro_lines:

        intro_text = "\n".join(intro_lines).strip()

        if intro_text:
            sections.append(("\u200b", intro_text))

# -------------------------------------------------------------------
# Pagination embeds
# -------------------------------------------------------------------

MAX_EMBED_CHARS = 5800
MAX_FIELD_VALUE = 1024

embeds: list[discord.Embed] = []

current_embed = discord.Embed(
    title=title,
    colour=colour
)

current_chars = len(title)

# -------------------------------------------------------------------
# Ajout des sections + pagination réelle
# -------------------------------------------------------------------

for (heading, body) in sections:

    if not body:
        body = "*— vide —*"

    # Découpe du body en morceaux de 1024 caractères max
    chunks = [
        body[i:i + MAX_FIELD_VALUE]
        for i in range(0, len(body), MAX_FIELD_VALUE)
    ]

    for chunk_index, chunk in enumerate(chunks):

        field_name = heading

        # Si plusieurs morceaux → ajoute "(suite)"
        if chunk_index > 0:
            field_name = f"{heading} (suite)"

        field_chars = len(field_name) + len(chunk)

        # Nouvel embed si dépassement
        if (
            current_chars + field_chars > MAX_EMBED_CHARS
            and current_embed.fields
        ):

            embeds.append(current_embed)

            current_embed = discord.Embed(
                title=f"{title} (suite)",
                colour=colour
            )

            current_chars = len(title) + 7

        current_embed.add_field(
            name=field_name,
            value=chunk,
            inline=False,
        )

        current_chars += field_chars

# -------------------------------------------------------------------
# Finalisation
# -------------------------------------------------------------------

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
    """Vue Discord avec boutons Précédent / Suivant."""

    def __init__(
        self,
        embeds: list[discord.Embed],
        timeout: float = 120.0
    ):
        super().__init__(timeout=timeout)

        self.embeds = embeds
        self.current_page = 0

        self._update_buttons()

    def _update_buttons(self):

        self.prev_button.disabled = self.current_page == 0

        self.next_button.disabled = (
            self.current_page == len(self.embeds) - 1
        )

    @discord.ui.button(
        label="◀ Précédent",
        style=discord.ButtonStyle.secondary
    )
    async def prev_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        self.current_page -= 1

        self._update_buttons()

        await interaction.response.edit_message(
            embed=self.embeds[self.current_page],
            view=self
        )

    @discord.ui.button(
        label="Suivant ▶",
        style=discord.ButtonStyle.secondary
    )
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        self.current_page += 1

        self._update_buttons()

        await interaction.response.edit_message(
            embed=self.embeds[self.current_page],
            view=self
        )

    async def on_timeout(self):

        for item in self.children:
            item.disabled = True


# ---------------------------------------------------------------------------
# Point d'entrée principal
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
            "❌ Identifiant de document invalide.",
            ephemeral=True
        )

        return

    dossier, fichier = parts

    await interaction.response.defer()

    async with aiohttp.ClientSession() as session:

        try:
            content = await fetch_md_content(
                session,
                dossier,
                fichier
            )

        except FileNotFoundError as e:

            await interaction.followup.send(
                str(e),
                ephemeral=True
            )

            return

        except RuntimeError as e:

            await interaction.followup.send(
                f"❌ {e}",
                ephemeral=True
            )

            return

    embeds = md_to_embeds(content)

    if len(embeds) == 1:

        await interaction.followup.send(
            embed=embeds[0]
        )

    else:

        view = WikiView(embeds)

        msg = await interaction.followup.send(
            embed=embeds[0],
            view=view
        )

        view.message = msg