# nexara_bot/addwiki.py
# Création, modification et revue de propositions wiki via Pull Requests GitHub.

import asyncio
import base64
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands

from nexara_bot.logs import build_log, send_log

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9+ fournit zoneinfo.
    ZoneInfo = None


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20)

BASE_BRANCH = os.getenv("GITHUB_BASE_BRANCH", "main")
BRANCH_PREFIX = "wiki-proposal"

MAX_AUTOCOMPLETE_CHOICES = 25
MAX_CHOICE_NAME = 100
MAX_CHOICE_VALUE = 100
MAX_MODAL_BODY = 4000
MAX_EMBED_FIELD = 1024

PROPOSITIONS = {
    "NEX": {
        "label": "Nexara",
        "directory": "dossiers/nexara/contenu",
    },
    "SCP": {
        "label": "Fondation SCP",
        "directory": "dossiers/fondation_scp/contenu",
    },
}

FRENCH_MONTHS = {
    1: "janvier",
    2: "février",
    3: "mars",
    4: "avril",
    5: "mai",
    6: "juin",
    7: "juillet",
    8: "août",
    9: "septembre",
    10: "octobre",
    11: "novembre",
    12: "décembre",
}

PR_TITLE_RE = re.compile(
    r"^(?P<discord_name>.+)-(?P<discord_id>\d{5,})-(?P<sequence>\d+)$"
)

GENERATED_FOOTER_RE = re.compile(
    r"\n*---\s*\n+\s*Créé le .+?\.\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Erreurs métier
# ---------------------------------------------------------------------------

class AddWikiError(Exception):
    """Erreur affichable à l'utilisateur Discord."""


class GitHubConfigError(AddWikiError):
    """Configuration GitHub absente ou incomplète."""


class GitHubAPIError(AddWikiError):
    """Erreur retournée par l'API GitHub."""


class GitHubNotFound(GitHubAPIError):
    """Ressource GitHub introuvable."""


class GitHubConflict(GitHubAPIError):
    """Conflit GitHub, par exemple branche déjà existante."""


# ---------------------------------------------------------------------------
# Modèles internes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GitHubConfig:
    owner: str
    repo: str
    token: str

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class GitHubFile:
    path: str
    content: str
    sha: str


@dataclass(frozen=True)
class WikiProposal:
    pr_number: int
    pr_title: str
    discord_name: str
    discord_id: str
    sequence: int
    branch: str
    html_url: str
    file_path: str
    filename: str
    proposition: str


# ---------------------------------------------------------------------------
# Utilitaires texte / chemins
# ---------------------------------------------------------------------------

def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _quote_path(path: str) -> str:
    """Encode un chemin GitHub en conservant les séparateurs de dossiers."""
    return "/".join(quote(part, safe="") for part in path.split("/"))


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _safe_discord_name(value: str) -> str:
    """Produit la partie pseudo utilisée dans le titre de PR."""
    ascii_value = _strip_accents(value).lower().strip()
    ascii_value = re.sub(r"\s+", "_", ascii_value)
    ascii_value = re.sub(r"[^a-z0-9_.-]", "", ascii_value)
    ascii_value = ascii_value.strip("._-")
    return ascii_value or "utilisateur"


def _safe_document_suffix(value: str) -> str:
    """Nettoie le nom demandé et force le segment document en majuscules."""
    ascii_value = _strip_accents(value).upper().strip()
    ascii_value = re.sub(r"\s+", "-", ascii_value)
    ascii_value = re.sub(r"[^A-Z0-9-]", "", ascii_value)
    ascii_value = re.sub(r"-+", "-", ascii_value).strip("-")
    return ascii_value or "SANS-NOM"


def build_document_filename(proposition: str, document_name: str) -> str:
    proposition = proposition.upper()
    suffix = _safe_document_suffix(document_name)
    return f"Document-{proposition}-{suffix}.md"


def _normalise_filename(filename: str) -> str:
    filename = filename.strip().replace("\\", "/").split("/")[-1]
    if not filename.lower().endswith(".md"):
        filename = f"{filename}.md"
    return filename


def _document_directory(proposition: str) -> str:
    proposition = proposition.upper()
    if proposition not in PROPOSITIONS:
        raise AddWikiError("Proposition invalide.")
    return PROPOSITIONS[proposition]["directory"]


def _document_path(proposition: str, filename: str) -> str:
    return f"{_document_directory(proposition)}/{_normalise_filename(filename)}"


def _index_path(proposition: str) -> str:
    return f"{_document_directory(proposition)}/index.json"


def _proposition_from_path(path: str) -> Optional[str]:
    normalized = path.replace("\\", "/")
    for proposition, data in PROPOSITIONS.items():
        directory = data["directory"].rstrip("/") + "/"
        if normalized.startswith(directory):
            return proposition
    return None


def _index_entry_to_filename(entry) -> Optional[str]:
    if isinstance(entry, str) and entry.strip():
        return _normalise_filename(entry)

    if isinstance(entry, dict):
        for key in ("file", "fichier", "path", "name"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return _normalise_filename(value)

    return None


def _build_index_content(entries: list[str], add_filename: str, remove_filename: str | None = None) -> str:
    """Construit un index.json simple sans doublons."""
    add_filename = _normalise_filename(add_filename)
    remove_key = _normalise_filename(remove_filename).lower() if remove_filename else None
    seen: set[str] = set()
    cleaned: list[str] = []

    for entry in entries:
        filename = _normalise_filename(entry)
        key = filename.lower()

        if remove_key and key == remove_key:
            continue

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(filename)

    if add_filename.lower() not in seen:
        cleaned.append(add_filename)

    return json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n"


def _today_fr() -> str:
    timezone = ZoneInfo("Europe/Paris") if ZoneInfo else None
    now = datetime.now(timezone)
    return f"{now.day} {FRENCH_MONTHS[now.month]} {now.year}"


def _strip_generated_footer(content: str) -> str:
    return GENERATED_FOOTER_RE.sub("", content).strip()


def build_document_content(title: str, markdown_body: str) -> str:
    """Ajoute le frontmatter et la signature obligatoire du bot."""
    clean_title = " ".join(title.strip().splitlines()) or "Sans titre"
    clean_body = _strip_generated_footer(markdown_body)

    return (
        "---\n"
        f"title: {clean_title}\n"
        "---\n\n"
        f"{clean_body}\n\n"
        "---\n\n"
        f"Créé le {_today_fr()}."
    )


def parse_document_content(content: str) -> tuple[str, str]:
    """Sépare un document existant en titre modifiable et corps Markdown."""
    lines = content.splitlines()
    title = ""
    body_start = 0

    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], start=1):
            stripped = line.strip()

            if stripped.lower().startswith("title:"):
                title = stripped.split(":", 1)[1].strip().strip('"').strip("'")

            if stripped == "---":
                body_start = index + 1
                break

    if not title:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip()
                break

    body = "\n".join(lines[body_start:]).strip()
    body = _strip_generated_footer(body)

    return title or "Sans titre", body


def _parse_pr_title(title: str) -> Optional[tuple[str, str, int]]:
    match = PR_TITLE_RE.match(title.strip())
    if not match:
        return None

    return (
        match.group("discord_name"),
        match.group("discord_id"),
        int(match.group("sequence")),
    )


# ---------------------------------------------------------------------------
# Client GitHub REST async
# ---------------------------------------------------------------------------

class GitHubClient:
    """Petit client REST GitHub, limité aux actions nécessaires au wiki."""

    def __init__(self, config: GitHubConfig):
        self.config = config
        self.session: aiohttp.ClientSession | None = None

    @classmethod
    def from_env(cls) -> "GitHubClient":
        token = os.getenv("GITHUB_TOKEN", "").strip()
        owner = os.getenv("GITHUB_OWNER", "").strip()
        repo = os.getenv("GITHUB_REPO", "").strip()

        missing = [
            name
            for name, value in (
                ("GITHUB_TOKEN", token),
                ("GITHUB_OWNER", owner),
                ("GITHUB_REPO", repo),
            )
            if not value
        ]

        if missing:
            raise GitHubConfigError(
                "Configuration GitHub manquante : " + ", ".join(missing)
            )

        return cls(GitHubConfig(owner=owner, repo=repo, token=token))

    async def __aenter__(self) -> "GitHubClient":
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.config.token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        self.session = aiohttp.ClientSession(headers=headers, timeout=HTTP_TIMEOUT)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.session:
            await self.session.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected: tuple[int, ...] = (200,),
        params: dict | None = None,
        json_body: dict | None = None,
    ):
        if self.session is None:
            raise GitHubAPIError("Session GitHub non initialisée.")

        clean_path = path.strip("/")
        url = f"{GITHUB_API_ROOT}/repos/{self.config.repo_full_name}/{clean_path}".rstrip("/")
        async with self.session.request(
            method,
            url,
            params=params,
            json=json_body,
        ) as response:
            text = await response.text()
            payload = None

            if text:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = text

            if response.status not in expected:
                message = ""

                if isinstance(payload, dict):
                    message = payload.get("message", "")

                if response.status in (403, 429):
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    if remaining == "0":
                        message = "GitHub API rate limit atteint."

                message = message or f"Erreur GitHub HTTP {response.status}."

                if response.status == 404:
                    raise GitHubNotFound(message)

                if response.status in (409, 422):
                    raise GitHubConflict(message)

                raise GitHubAPIError(message)

            return payload

    async def _paginate(self, path: str, *, params: dict | None = None) -> list:
        results = []
        page = 1

        while True:
            page_params = dict(params or {})
            page_params.update({"per_page": 100, "page": page})
            payload = await self._request("GET", path, params=page_params)

            if not isinstance(payload, list):
                return results

            results.extend(payload)

            if len(payload) < 100:
                return results

            page += 1

    async def get_default_branch(self) -> str:
        repo = await self._request("GET", "")
        return repo.get("default_branch") or BASE_BRANCH

    async def get_ref_sha(self, branch: str) -> str:
        payload = await self._request("GET", f"git/ref/heads/{quote(branch, safe='/')}")
        return payload["object"]["sha"]

    async def create_branch(self, branch: str, source_sha: str) -> None:
        await self._request(
            "POST",
            "git/refs",
            expected=(201,),
            json_body={"ref": f"refs/heads/{branch}", "sha": source_sha},
        )

    async def _get_commit_tree_sha(self, commit_sha: str) -> str:
        payload = await self._request("GET", f"git/commits/{commit_sha}")
        return payload["tree"]["sha"]

    async def _create_blob(self, content: str) -> str:
        payload = await self._request(
            "POST",
            "git/blobs",
            expected=(201,),
            json_body={"content": content, "encoding": "utf-8"},
        )
        return payload["sha"]

    async def _create_tree(self, base_tree_sha: str, entries: list[dict]) -> str:
        payload = await self._request(
            "POST",
            "git/trees",
            expected=(201,),
            json_body={"base_tree": base_tree_sha, "tree": entries},
        )
        return payload["sha"]

    async def _create_commit(self, message: str, tree_sha: str, parent_sha: str) -> str:
        payload = await self._request(
            "POST",
            "git/commits",
            expected=(201,),
            json_body={
                "message": message,
                "tree": tree_sha,
                "parents": [parent_sha],
            },
        )
        return payload["sha"]

    async def _update_ref(self, branch: str, commit_sha: str) -> None:
        await self._request(
            "PATCH",
            f"git/refs/heads/{quote(branch, safe='/')}",
            json_body={"sha": commit_sha, "force": False},
        )

    async def commit_files(
        self,
        *,
        branch: str,
        files: dict[str, str],
        message: str,
        delete_paths: list[str] | None = None,
    ) -> str:
        """Crée un commit atomique : ajout/update de fichiers et suppressions."""
        head_sha = await self.get_ref_sha(branch)
        base_tree_sha = await self._get_commit_tree_sha(head_sha)

        entries: list[dict] = []

        for path, content in files.items():
            blob_sha = await self._create_blob(content)
            entries.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            )

        for path in delete_paths or []:
            if path in files:
                continue

            entries.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": None,
                }
            )

        tree_sha = await self._create_tree(base_tree_sha, entries)
        commit_sha = await self._create_commit(message, tree_sha, head_sha)
        await self._update_ref(branch, commit_sha)
        return commit_sha

    async def get_file(self, path: str, ref: str) -> GitHubFile:
        payload = await self._request(
            "GET",
            f"contents/{_quote_path(path)}",
            params={"ref": ref},
        )

        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise GitHubNotFound("Fichier GitHub introuvable.")

        encoded = payload.get("content", "")
        content = base64.b64decode(encoded).decode("utf-8")
        return GitHubFile(path=path, content=content, sha=payload["sha"])

    async def file_exists(self, path: str, ref: str) -> bool:
        try:
            await self.get_file(path, ref)
            return True
        except GitHubNotFound:
            return False

    async def list_pull_requests(self, state: str = "open") -> list[dict]:
        return await self._paginate(
            "pulls",
            params={"state": state, "sort": "created", "direction": "desc"},
        )

    async def list_pull_files(self, pr_number: int) -> list[dict]:
        return await self._paginate(f"pulls/{pr_number}/files")

    async def get_pull_request(self, pr_number: int) -> dict:
        return await self._request("GET", f"pulls/{pr_number}")

    async def create_pull_request(
        self,
        *,
        title: str,
        branch: str,
        base_branch: str,
        body: str,
    ) -> dict:
        return await self._request(
            "POST",
            "pulls",
            expected=(201,),
            json_body={
                "title": title,
                "head": branch,
                "base": base_branch,
                "body": body,
                "maintainer_can_modify": True,
            },
        )

    async def approve_pull_request(self, pr_number: int) -> dict:
        return await self._request(
            "POST",
            f"pulls/{pr_number}/reviews",
            expected=(200, 201),
            json_body={
                "event": "APPROVE",
                "body": "Proposition wiki approuvée par le staff depuis Discord.",
            },
        )

    async def close_pull_request(self, pr_number: int) -> dict:
        return await self._request(
            "PATCH",
            f"pulls/{pr_number}",
            json_body={"state": "closed"},
        )

    async def delete_branch(self, branch: str) -> None:
        if branch in {"main", "master", BASE_BRANCH}:
            raise AddWikiError("Suppression refusée : branche protégée.")

        await self._request(
            "DELETE",
            f"git/refs/heads/{quote(branch, safe='/')}",
            expected=(204,),
        )


# ---------------------------------------------------------------------------
# Helpers propositions / PR
# ---------------------------------------------------------------------------

async def fetch_index_entries(
    github: GitHubClient,
    proposition: str,
    ref: str,
) -> list[str]:
    try:
        index_file = await github.get_file(_index_path(proposition), ref)
    except GitHubNotFound:
        return []

    try:
        parsed = json.loads(index_file.content)
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, list):
        return []

    return [
        filename
        for entry in parsed
        if (filename := _index_entry_to_filename(entry))
    ]


async def _proposal_from_pr(github: GitHubClient, pr: dict) -> Optional[WikiProposal]:
    parsed_title = _parse_pr_title(pr.get("title", ""))
    if parsed_title is None:
        return None

    head_repo = (pr.get("head", {}).get("repo") or {}).get("full_name", "")
    if head_repo.lower() != github.config.repo_full_name.lower():
        return None

    discord_name, discord_id, sequence = parsed_title
    files = await github.list_pull_files(pr["number"])

    markdown_files = [
        file
        for file in files
        if file.get("filename", "").lower().endswith(".md")
        and file.get("status") != "removed"
        and _proposition_from_path(file.get("filename", "")) is not None
    ]

    if not markdown_files:
        return None

    file_path = markdown_files[0]["filename"]
    proposition = _proposition_from_path(file_path)

    if proposition is None:
        return None

    return WikiProposal(
        pr_number=pr["number"],
        pr_title=pr["title"],
        discord_name=discord_name,
        discord_id=discord_id,
        sequence=sequence,
        branch=pr["head"]["ref"],
        html_url=pr["html_url"],
        file_path=file_path,
        filename=file_path.rsplit("/", 1)[-1],
        proposition=proposition,
    )


async def list_open_proposals(
    github: GitHubClient,
    *,
    user_id: int | None = None,
) -> list[WikiProposal]:
    pulls = await github.list_pull_requests("open")
    semaphore = asyncio.Semaphore(8)

    async def collect(pr: dict) -> Optional[WikiProposal]:
        async with semaphore:
            return await _proposal_from_pr(github, pr)

    proposals = await asyncio.gather(
        *(collect(pr) for pr in pulls),
        return_exceptions=True,
    )

    filtered: list[WikiProposal] = []

    for proposal in proposals:
        if not isinstance(proposal, WikiProposal):
            continue

        if user_id is not None and proposal.discord_id != str(user_id):
            continue

        filtered.append(proposal)

    return sorted(filtered, key=lambda item: item.pr_number, reverse=True)


async def get_open_proposal(github: GitHubClient, pr_number: int) -> Optional[WikiProposal]:
    pr = await github.get_pull_request(pr_number)

    if pr.get("state") != "open":
        return None

    return await _proposal_from_pr(github, pr)


async def next_user_sequence(github: GitHubClient, user: discord.User | discord.Member) -> int:
    pulls = await github.list_pull_requests("all")
    highest = 0

    for pr in pulls:
        parsed = _parse_pr_title(pr.get("title", ""))
        if parsed is None:
            continue

        _, discord_id, sequence = parsed

        if discord_id == str(user.id):
            highest = max(highest, sequence)

    return highest + 1


async def document_is_duplicate(
    github: GitHubClient,
    *,
    proposition: str,
    filename: str,
    ignore_pr: int | None = None,
    ignore_path: str | None = None,
) -> bool:
    """Vérifie les doublons sur main, index.json et les PR ouvertes."""
    filename = _normalise_filename(filename)
    target_path = _document_path(proposition, filename)

    if ignore_path is None or target_path.lower() != ignore_path.lower():
        if await github.file_exists(target_path, BASE_BRANCH):
            return True

    index_entries = await fetch_index_entries(github, proposition, BASE_BRANCH)
    if filename.lower() in {entry.lower() for entry in index_entries}:
        if ignore_path is None or target_path.lower() != ignore_path.lower():
            return True

    for proposal in await list_open_proposals(github):
        if ignore_pr is not None and proposal.pr_number == ignore_pr:
            continue

        if proposal.file_path.lower() == target_path.lower():
            return True

    return False


def build_pr_body(
    *,
    interaction: discord.Interaction,
    proposition: str,
    file_path: str,
) -> str:
    return (
        "Proposition wiki créée depuis Discord.\n\n"
        f"- Discord: {interaction.user} (`{interaction.user.id}`)\n"
        f"- Proposition: {PROPOSITIONS[proposition]['label']} (`{proposition}`)\n"
        f"- Fichier: `{file_path}`\n\n"
        "Cette Pull Request doit être relue manuellement. "
        "Le bot ne merge jamais automatiquement."
    )


async def create_document_proposal(
    interaction: discord.Interaction,
    *,
    proposition: str,
    document_name: str,
    title: str,
    markdown_body: str,
) -> dict:
    filename = build_document_filename(proposition, document_name)
    file_path = _document_path(proposition, filename)
    content = build_document_content(title, markdown_body)

    async with GitHubClient.from_env() as github:
        if await document_is_duplicate(github, proposition=proposition, filename=filename):
            raise AddWikiError(f"Un document nommé `{filename}` existe déjà.")

        base_branch = await github.get_default_branch()
        index_entries = await fetch_index_entries(github, proposition, base_branch)
        index_content = _build_index_content(index_entries, filename)
        sequence = await next_user_sequence(github, interaction.user)
        user_slug = _safe_discord_name(interaction.user.name)

        # Petite boucle anti-conflit : si deux créations arrivent en même temps,
        # on tente le numéro suivant sans recréer une PR doublon.
        for attempt in range(5):
            current_sequence = sequence + attempt
            pr_title = f"{user_slug}-{interaction.user.id}-{current_sequence}"
            branch = (
                f"{BRANCH_PREFIX}/{interaction.user.id}/"
                f"{current_sequence}-{filename[:-3].lower()}"
            )

            try:
                base_sha = await github.get_ref_sha(base_branch)
                await github.create_branch(branch, base_sha)
                break
            except GitHubConflict:
                if attempt == 4:
                    raise AddWikiError("Impossible de créer une branche GitHub unique.")
        else:  # pragma: no cover - gardé pour la lisibilité.
            raise AddWikiError("Impossible de créer une branche GitHub unique.")

        await github.commit_files(
            branch=branch,
            files={
                file_path: content,
                _index_path(proposition): index_content,
            },
            message=f"Add wiki proposal {filename}",
        )

        pr = await github.create_pull_request(
            title=pr_title,
            branch=branch,
            base_branch=base_branch,
            body=build_pr_body(
                interaction=interaction,
                proposition=proposition,
                file_path=file_path,
            ),
        )

    return {
        "filename": filename,
        "file_path": file_path,
        "branch": branch,
        "pr": pr,
    }


async def update_document_proposal(
    interaction: discord.Interaction,
    *,
    proposal: WikiProposal,
    document_name: str,
    title: str,
    markdown_body: str,
) -> dict:
    new_filename = build_document_filename(proposal.proposition, document_name)
    new_path = _document_path(proposal.proposition, new_filename)
    new_content = build_document_content(title, markdown_body)
    old_filename = proposal.filename
    old_path = proposal.file_path

    async with GitHubClient.from_env() as github:
        fresh = await get_open_proposal(github, proposal.pr_number)

        if fresh is None:
            raise AddWikiError("Cette Pull Request n'est plus ouverte.")

        if fresh.discord_id != str(interaction.user.id):
            raise AddWikiError("Tu ne peux modifier que tes propres propositions.")

        if await document_is_duplicate(
            github,
            proposition=fresh.proposition,
            filename=new_filename,
            ignore_pr=fresh.pr_number,
            ignore_path=fresh.file_path,
        ):
            raise AddWikiError(f"Un document nommé `{new_filename}` existe déjà.")

        try:
            index_entries = await fetch_index_entries(github, fresh.proposition, fresh.branch)
        except GitHubAPIError:
            index_entries = await fetch_index_entries(github, fresh.proposition, BASE_BRANCH)

        index_content = _build_index_content(
            index_entries,
            new_filename,
            remove_filename=old_filename if new_path.lower() != old_path.lower() else None,
        )

        delete_paths = [old_path] if new_path.lower() != old_path.lower() else []

        await github.commit_files(
            branch=fresh.branch,
            files={
                new_path: new_content,
                _index_path(fresh.proposition): index_content,
            },
            delete_paths=delete_paths,
            message=f"Update wiki proposal {new_filename}",
        )

    return {
        "filename": new_filename,
        "file_path": new_path,
        "branch": proposal.branch,
        "pr_number": proposal.pr_number,
    }


# ---------------------------------------------------------------------------
# Logs Discord
# ---------------------------------------------------------------------------

async def _send_log(
    interaction: discord.Interaction,
    *,
    title: str,
    color: discord.Color,
    fields: list[tuple[str, str, bool]],
) -> None:
    if interaction.guild is None:
        return

    safe_fields = [
        (name, _truncate(value or "—", MAX_EMBED_FIELD), inline)
        for name, value, inline in fields
    ]

    embed = build_log(title=title, color=color, fields=safe_fields)
    await send_log(interaction.guild, embed)


# ---------------------------------------------------------------------------
# Autocomplétions
# ---------------------------------------------------------------------------

async def proposition_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    del interaction
    current_lower = current.lower()

    return [
        app_commands.Choice(name=data["label"], value=code)
        for code, data in PROPOSITIONS.items()
        if current_lower in data["label"].lower() or current_lower in code.lower()
    ][:MAX_AUTOCOMPLETE_CHOICES]


def _proposal_choice(proposal: WikiProposal, *, include_user: bool) -> app_commands.Choice[str]:
    proposition_label = PROPOSITIONS[proposal.proposition]["label"]
    name_parts = [proposal.filename, proposition_label, f"PR #{proposal.pr_number}"]

    if include_user:
        name_parts.append(proposal.discord_name)

    return app_commands.Choice(
        name=_truncate(" • ".join(name_parts), MAX_CHOICE_NAME),
        value=str(proposal.pr_number)[:MAX_CHOICE_VALUE],
    )


async def user_documents_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    try:
        async with GitHubClient.from_env() as github:
            proposals = await list_open_proposals(github, user_id=interaction.user.id)
    except AddWikiError:
        return []

    current_lower = current.lower()
    return [
        _proposal_choice(proposal, include_user=False)
        for proposal in proposals
        if current_lower in proposal.filename.lower()
        or current_lower in PROPOSITIONS[proposal.proposition]["label"].lower()
    ][:MAX_AUTOCOMPLETE_CHOICES]


async def all_documents_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    del interaction

    try:
        async with GitHubClient.from_env() as github:
            proposals = await list_open_proposals(github)
    except AddWikiError:
        return []

    current_lower = current.lower()
    return [
        _proposal_choice(proposal, include_user=True)
        for proposal in proposals
        if current_lower in proposal.filename.lower()
        or current_lower in proposal.discord_name.lower()
        or current_lower in str(proposal.pr_number)
    ][:MAX_AUTOCOMPLETE_CHOICES]


# ---------------------------------------------------------------------------
# Discord UI : Modals
# ---------------------------------------------------------------------------

class CreateDocumentModal(discord.ui.Modal):
    """Modal de création d'une proposition de document wiki."""

    def __init__(self, proposition: str):
        super().__init__(title=f"Créer un document {proposition}")
        self.proposition = proposition

        self.document_name = discord.ui.TextInput(
            label="Nom du document",
            style=discord.TextStyle.short,
            placeholder="Exemple : ARCHIVES ou 9454",
            required=True,
            max_length=80,
        )
        self.document_title = discord.ui.TextInput(
            label="Titre du document",
            style=discord.TextStyle.short,
            placeholder="Titre affiché dans le frontmatter YAML",
            required=True,
            max_length=200,
        )
        self.markdown_body = discord.ui.TextInput(
            label="Contenu markdown",
            style=discord.TextStyle.long,
            placeholder="Écrivez le document en Markdown.",
            required=True,
            max_length=MAX_MODAL_BODY,
        )

        self.add_item(self.document_name)
        self.add_item(self.document_title)
        self.add_item(self.markdown_body)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            result = await create_document_proposal(
                interaction,
                proposition=self.proposition,
                document_name=str(self.document_name.value),
                title=str(self.document_title.value),
                markdown_body=str(self.markdown_body.value),
            )
        except AddWikiError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await interaction.followup.send(
                "❌ Impossible de contacter GitHub pour le moment.",
                ephemeral=True,
            )
            return

        pr = result["pr"]

        await _send_log(
            interaction,
            title="📝 Proposition wiki créée",
            color=discord.Color.blurple(),
            fields=[
                ("Auteur", f"{interaction.user.mention} (`{interaction.user.id}`)", False),
                ("Document", result["filename"], False),
                ("Branche", result["branch"], False),
                ("Pull Request", pr["html_url"], False),
            ],
        )

        embed = discord.Embed(
            title="✅ Proposition créée",
            description="La Pull Request GitHub a été créée. Elle doit être relue manuellement.",
            color=discord.Color.green(),
            url=pr["html_url"],
        )
        embed.add_field(name="Document", value=f"`{result['filename']}`", inline=False)
        embed.add_field(name="Pull Request", value=f"[Ouvrir la PR]({pr['html_url']})", inline=False)
        embed.add_field(name="Branche", value=f"`{result['branch']}`", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


class EditDocumentModal(discord.ui.Modal):
    """Modal de modification d'une PR déjà ouverte."""

    def __init__(self, proposal: WikiProposal, title: str, body: str):
        super().__init__(title="Modifier la proposition")
        self.proposal = proposal

        default_name = proposal.filename
        expected_prefix = f"Document-{proposal.proposition}-"
        if default_name.startswith(expected_prefix) and default_name.lower().endswith(".md"):
            default_name = default_name[len(expected_prefix):-3]

        self.document_name = discord.ui.TextInput(
            label="Nom du document",
            style=discord.TextStyle.short,
            default=_truncate(default_name, 80),
            required=True,
            max_length=80,
        )
        self.document_title = discord.ui.TextInput(
            label="Titre du document",
            style=discord.TextStyle.short,
            default=_truncate(title, 200),
            required=True,
            max_length=200,
        )
        self.markdown_body = discord.ui.TextInput(
            label="Contenu markdown",
            style=discord.TextStyle.long,
            placeholder="Écrivez le document en Markdown.",
            default=body,
            required=True,
            max_length=MAX_MODAL_BODY,
        )

        self.add_item(self.document_name)
        self.add_item(self.document_title)
        self.add_item(self.markdown_body)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            result = await update_document_proposal(
                interaction,
                proposal=self.proposal,
                document_name=str(self.document_name.value),
                title=str(self.document_title.value),
                markdown_body=str(self.markdown_body.value),
            )
        except AddWikiError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await interaction.followup.send(
                "❌ Impossible de contacter GitHub pour le moment.",
                ephemeral=True,
            )
            return

        await _send_log(
            interaction,
            title="✏️ Proposition wiki modifiée",
            color=discord.Color.teal(),
            fields=[
                ("Auteur", f"{interaction.user.mention} (`{interaction.user.id}`)", False),
                ("Document", result["filename"], False),
                ("PR", f"#{result['pr_number']}", True),
                ("Branche", result["branch"], False),
            ],
        )

        await interaction.followup.send(
            f"✅ Proposition mise à jour dans la PR `#{result['pr_number']}`.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Discord UI : Staff review
# ---------------------------------------------------------------------------

class ProposalReviewView(discord.ui.View):
    """Boutons staff pour approuver ou refuser une proposition."""

    def __init__(
        self,
        proposal: WikiProposal,
        is_staff_check: Callable[[discord.User | discord.Member], bool],
    ):
        super().__init__(timeout=180)
        self.proposal = proposal
        self.is_staff_check = is_staff_check

    async def _ensure_staff(self, interaction: discord.Interaction) -> bool:
        if self.is_staff_check(interaction.user):
            return True

        await interaction.response.send_message(
            "❌ Tu n'es pas autorisé à gérer cette proposition.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Accorder", style=discord.ButtonStyle.success)
    async def approve_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button

        if not await self._ensure_staff(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            async with GitHubClient.from_env() as github:
                await github.approve_pull_request(self.proposal.pr_number)
        except AddWikiError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await interaction.followup.send(
                "❌ Impossible de contacter GitHub pour le moment.",
                ephemeral=True,
            )
            return

        await _send_log(
            interaction,
            title="✅ Proposition wiki approuvée",
            color=discord.Color.green(),
            fields=[
                ("Staff", f"{interaction.user.mention} (`{interaction.user.id}`)", False),
                ("Document", self.proposal.filename, False),
                ("PR", self.proposal.html_url, False),
            ],
        )

        await interaction.followup.send(
            "✅ Pull Request approuvée. Aucun merge automatique n'a été effectué.",
            ephemeral=True,
        )

    @discord.ui.button(label="Refuser", style=discord.ButtonStyle.danger)
    async def refuse_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button

        if not await self._ensure_staff(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            async with GitHubClient.from_env() as github:
                await github.close_pull_request(self.proposal.pr_number)
        except AddWikiError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await interaction.followup.send(
                "❌ Impossible de contacter GitHub pour le moment.",
                ephemeral=True,
            )
            return

        await _send_log(
            interaction,
            title="🗑️ Pull Request wiki fermée",
            color=discord.Color.red(),
            fields=[
                ("Staff", f"{interaction.user.mention} (`{interaction.user.id}`)", False),
                ("Document", self.proposal.filename, False),
                ("PR", self.proposal.html_url, False),
            ],
        )

        try:
            async with GitHubClient.from_env() as github:
                await github.delete_branch(self.proposal.branch)
        except AddWikiError as exc:
            await interaction.followup.send(
                f"⚠️ PR fermée, mais suppression de branche impossible : {exc}",
                ephemeral=True,
            )
            return
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await interaction.followup.send(
                "⚠️ PR fermée, mais GitHub ne répond pas pour supprimer la branche.",
                ephemeral=True,
            )
            return

        await _send_log(
            interaction,
            title="🌿 Branche wiki supprimée",
            color=discord.Color.dark_red(),
            fields=[
                ("Staff", f"{interaction.user.mention} (`{interaction.user.id}`)", False),
                ("Document", self.proposal.filename, False),
                ("Branche", self.proposal.branch, False),
            ],
        )

        await interaction.followup.send(
            "✅ Pull Request fermée et branche supprimée.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Slash commands appelées depuis bot1.py
# ---------------------------------------------------------------------------

async def cmd_creer_document(interaction: discord.Interaction, proposition: str) -> None:
    proposition = proposition.upper().strip()

    if proposition not in PROPOSITIONS:
        await interaction.response.send_message("❌ Proposition invalide.", ephemeral=True)
        return

    await interaction.response.send_modal(CreateDocumentModal(proposition))


async def cmd_modifier_document(interaction: discord.Interaction, document: str) -> None:
    if not document.isdigit():
        await interaction.response.send_message("❌ Document invalide.", ephemeral=True)
        return

    try:
        async with GitHubClient.from_env() as github:
            proposal = await get_open_proposal(github, int(document))

            if proposal is None:
                await interaction.response.send_message(
                    "❌ Cette proposition n'est plus ouverte.",
                    ephemeral=True,
                )
                return

            if proposal.discord_id != str(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Tu ne peux modifier que tes propres propositions.",
                    ephemeral=True,
                )
                return

            current_file = await github.get_file(proposal.file_path, proposal.branch)

    except AddWikiError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        return
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await interaction.response.send_message(
            "❌ Impossible de contacter GitHub pour le moment.",
            ephemeral=True,
        )
        return

    title, body = parse_document_content(current_file.content)

    if len(body) > MAX_MODAL_BODY:
        await interaction.response.send_message(
            "❌ Ce document dépasse la limite de modification via modal Discord.",
            ephemeral=True,
        )
        return

    await interaction.response.send_modal(EditDocumentModal(proposal, title, body))


async def cmd_view_document(
    interaction: discord.Interaction,
    document: str,
    is_staff_check: Callable[[discord.User | discord.Member], bool],
) -> None:
    if not is_staff_check(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'es pas autorisé à utiliser cette commande.",
            ephemeral=True,
        )
        return

    if not document.isdigit():
        await interaction.response.send_message("❌ Document invalide.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        async with GitHubClient.from_env() as github:
            proposal = await get_open_proposal(github, int(document))

            if proposal is None:
                await interaction.followup.send(
                    "❌ Cette proposition n'est plus ouverte.",
                    ephemeral=True,
                )
                return

            current_file = await github.get_file(proposal.file_path, proposal.branch)

    except AddWikiError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await interaction.followup.send(
            "❌ Impossible de contacter GitHub pour le moment.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"Proposition wiki #{proposal.pr_number}",
        color=discord.Color.blurple(),
        url=proposal.html_url,
    )
    embed.add_field(name="Document", value=f"`{proposal.filename}`", inline=False)
    embed.add_field(
        name="Discord",
        value=f"{proposal.discord_name} (`{proposal.discord_id}`)",
        inline=False,
    )
    embed.add_field(name="Pull Request", value=f"[Ouvrir sur GitHub]({proposal.html_url})", inline=False)
    embed.add_field(name="Branche", value=f"`{proposal.branch}`", inline=False)
    embed.add_field(
        name="Contenu markdown",
        value=f"```md\n{_truncate(current_file.content, 980)}\n```",
        inline=False,
    )

    await interaction.followup.send(
        embed=embed,
        view=ProposalReviewView(proposal, is_staff_check),
        ephemeral=True,
    )
