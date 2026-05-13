# nexara_bot/wiki.py
# Logique de récupération et d'affichage du wiki depuis GitHub

import asyncio
import base64
import hashlib
import json
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord

GITHUB_API_BASE = "https://api.github.com/repos/oria-organization/oria-wiki"
RAW_BASE = "https://raw.githubusercontent.com/oria-organization/oria-wiki/main"
DOSSIERS_ENDPOINT = f"{GITHUB_API_BASE}/contents/dossiers"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)
MAX_TITLE_FETCHES = 8
MAX_CHOICE_NAME = 100
MAX_CHOICE_VALUE = 100

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _quote_github_path(path: str) -> str:
    """Encode correctement un chemin GitHub sans perdre les séparateurs."""
    return "/".join(quote(part, safe="") for part in path.split("/"))


# ---------------------------------------------------------------------------
# Récupération des données GitHub
# ---------------------------------------------------------------------------

async def fetch_dossiers(session: aiohttp.ClientSession) -> list[str]:
    """Retourne la liste des noms de dossiers disponibles dans /dossiers."""
    async with session.get(DOSSIERS_ENDPOINT, headers=HEADERS) as resp:
        if resp.status in (403, 429):
            raise RuntimeError("GitHub API rate limit atteint. Réessaie dans quelques minutes.")
        if resp.status != 200:
            raise RuntimeError(f"Impossible de récupérer les dossiers (HTTP {resp.status}).")

        data = await resp.json()

    if not isinstance(data, list):
        return []

    return sorted(
        item["name"]
        for item in data
        if item.get("type") == "dir" and item.get("name")
    )


async def fetch_index(session: aiohttp.ClientSession, dossier: str) -> list[str]:
    """Récupère et parse le index.json d'un dossier donné."""
    path = _quote_github_path(f"dossiers/{dossier}/contenu/index.json")
    url = f"{GITHUB_API_BASE}/contents/{path}"

    async with session.get(url, headers=HEADERS) as resp:
        if resp.status in (403, 429):
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

        if isinstance(parsed, list):
            return [
                document
                for entry in parsed
                if (document := _index_entry_to_filename(entry))
            ]

        return []

    except json.JSONDecodeError:
        return []


def _index_entry_to_filename(entry) -> Optional[str]:
    """Accepte un index simple ou quelques variantes objet courantes."""
    if isinstance(entry, str):
        return entry.strip()

    if isinstance(entry, dict):
        for key in ("file", "fichier", "path", "name"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


async def fetch_markdown_files(
    session: aiohttp.ClientSession,
    dossier: str,
    subpath: str = "",
) -> list[str]:
    """Liste les fichiers .md réellement présents dans le dossier contenu."""
    base_path = f"dossiers/{dossier}/contenu"
    api_path = f"{base_path}/{subpath}" if subpath else base_path
    url = f"{GITHUB_API_BASE}/contents/{_quote_github_path(api_path)}"

    async with session.get(url, headers=HEADERS) as resp:
        if resp.status in (403, 429):
            raise RuntimeError("GitHub API rate limit atteint.")

        if resp.status == 404:
            return []

        if resp.status != 200:
            return []

        data = await resp.json()

    if not isinstance(data, list):
        return []

    files: list[str] = []
    subdirs: list[str] = []

    for item in data:
        name = item.get("name")
        item_type = item.get("type")

        if not name:
            continue

        relative_path = f"{subpath}/{name}" if subpath else name

        if item_type == "file" and name.lower().endswith(".md"):
            files.append(relative_path)

        elif item_type == "dir":
            subdirs.append(relative_path)

    if subdirs:
        nested_results = await asyncio.gather(
            *(
                fetch_markdown_files(session, dossier, nested_path)
                for nested_path in subdirs
            ),
            return_exceptions=True,
        )

        for nested in nested_results:
            if isinstance(nested, RuntimeError):
                raise nested

            if isinstance(nested, list):
                files.extend(nested)

    return sorted(files)


async def fetch_md_content(session: aiohttp.ClientSession, dossier: str, fichier: str) -> str:
    """Récupère le contenu brut d'un fichier .md."""

    if not fichier.endswith(".md"):
        fichier = fichier + ".md"

    path = _quote_github_path(f"dossiers/{dossier}/contenu/{fichier}")
    url = f"{RAW_BASE}/{path}"

    async with session.get(url) as resp:
        if resp.status in (403, 429):
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
_choices_lock: Optional[asyncio.Lock] = None
_document_value_cache: dict[str, tuple[str, str]] = {}


def _normalise_document_name(fichier: str) -> str:
    fichier = fichier.strip().lstrip("/")

    if not fichier.lower().endswith(".md"):
        fichier = fichier + ".md"

    return fichier


def _merge_documents(index: list[str], discovered: list[str]) -> list[str]:
    documents: list[str] = []
    seen: set[str] = set()

    for filename in [*index, *discovered]:
        if not filename:
            continue

        document = _normalise_document_name(filename)
        key = document.lower()

        if key in seen:
            continue

        seen.add(key)
        documents.append(document)

    return documents


def _fallback_title_from_filename(fichier: str) -> str:
    filename = fichier.rsplit("/", 1)[-1]

    if filename.lower().endswith(".md"):
        filename = filename[:-3]

    title = filename.replace("_", " ").replace("-", " ").strip()
    return title.title() if title else "Sans titre"


def _document_choice_value(dossier: str, fichier: str) -> str:
    value = f"{dossier}/{fichier}"

    if len(value) <= MAX_CHOICE_VALUE:
        return value

    document_id = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    short_value = f"id:{document_id}"
    _document_value_cache[short_value] = (dossier, fichier)
    return short_value


def _resolve_document_value(document_value: str) -> Optional[tuple[str, str]]:
    if document_value.startswith("id:"):
        return _document_value_cache.get(document_value)

    parts = document_value.split("/", 1)

    if len(parts) != 2:
        return None

    dossier, fichier = parts
    return dossier, fichier


async def _build_choice_for_document(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    dossier: str,
    fichier: str,
) -> dict:
    async with semaphore:
        try:
            md = await fetch_md_content(session, dossier, fichier)
            titre = _extract_title_from_md(md)

        except (FileNotFoundError, RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
            titre = _fallback_title_from_filename(fichier)

    display = f"{dossier.replace('_', ' ').title()} — {titre}"

    return {
        "name": display[:MAX_CHOICE_NAME],
        "value": _document_choice_value(dossier, fichier),
    }


async def _build_choices() -> list[dict]:
    """Charge tous les choix depuis GitHub (appelé une seule fois)."""

    choices = []
    _document_value_cache.clear()

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:

        try:
            dossiers = await fetch_dossiers(session)

        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
            return []

        dossier_payloads = await asyncio.gather(
            *(
                asyncio.gather(
                    fetch_index(session, dossier),
                    fetch_markdown_files(session, dossier),
                    return_exceptions=True,
                )
                for dossier in dossiers
            ),
            return_exceptions=True,
        )

        semaphore = asyncio.Semaphore(MAX_TITLE_FETCHES)
        choice_tasks = []

        for dossier, payload in zip(dossiers, dossier_payloads):
            if isinstance(payload, Exception):
                continue

            index_result, discovered_result = payload
            index = index_result if isinstance(index_result, list) else []
            discovered = discovered_result if isinstance(discovered_result, list) else []
            documents = _merge_documents(index, discovered)

            for fichier_name in documents:
                choice_tasks.append(
                    _build_choice_for_document(
                        session,
                        semaphore,
                        dossier,
                        fichier_name,
                    )
                )

        if choice_tasks:
            choices = await asyncio.gather(*choice_tasks)

    return sorted(choices, key=lambda choice: choice["name"].lower())


async def get_autocomplete_choices() -> list[dict]:
    """
    Retourne la liste des choix pour l'autocomplétion.
    Utilise le cache en mémoire — GitHub n'est appelé qu'une seule fois
    pour tout le cycle de vie du bot.
    """

    global _choices_cache, _choices_lock

    if _choices_cache is None:
        if _choices_lock is None:
            _choices_lock = asyncio.Lock()

        async with _choices_lock:
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

    title = _extract_title_from_md(content)[:256]

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
    MAX_FIELDS = 25
    MAX_FIELD_NAME = 256
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

            field_name = heading or "\u200b"

            # Si plusieurs morceaux → ajoute "(suite)"
            if chunk_index > 0:
                field_name = f"{field_name} (suite)"

            field_name = field_name[:MAX_FIELD_NAME]
            field_chars = len(field_name) + len(chunk)

            # Nouvel embed si dépassement
            if (
                current_embed.fields
                and (
                    current_chars + field_chars > MAX_EMBED_CHARS
                    or len(current_embed.fields) >= MAX_FIELDS
                )
            ):

                embeds.append(current_embed)

                current_embed = discord.Embed(
                    title=f"{title} (suite)"[:256],
                    colour=colour
                )

                current_chars = len(current_embed.title or "")

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

    resolved = _resolve_document_value(document_value)

    if resolved is None:

        await interaction.response.send_message(
            "❌ Identifiant de document invalide.",
            ephemeral=True
        )

        return

    dossier, fichier = resolved

    await interaction.response.defer()

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:

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

        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as e:

            await interaction.followup.send(
                f"❌ {e or 'Temps d’attente dépassé pendant la récupération du document.'}",
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