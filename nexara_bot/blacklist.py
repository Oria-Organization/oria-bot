# nexara_bot/blacklist.py
# Logique complète du système de blacklist

import discord
import json
import os
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Constantes & chemins
# ---------------------------------------------------------------------------

BLACKLIST_PATH = "json/blacklist.json"

# Structure du fichier JSON :
# {
#   "blacklist": {
#     "user_id": {
#       "type": "staff" | "ban",
#       "raison": "...",
#       "images": ["url1", "url2", ...],
#       "added_by": user_id,
#       "added_at": "ISO8601",
#       "username": "nom#discriminator"
#     }
#   },
#   "immunised": [guild_id, ...]   <- serveurs immunisés contre le /blacklist ban (pas le staff)
# }


# ---------------------------------------------------------------------------
# Lecture / écriture JSON
# ---------------------------------------------------------------------------

def _load() -> dict:
    """Charge le fichier blacklist.json, le crée s'il n'existe pas."""
    os.makedirs("json", exist_ok=True)
    if not os.path.exists(BLACKLIST_PATH):
        _save({"blacklist": {}, "immunised": []})
    with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    """Sauvegarde les données dans blacklist.json."""
    os.makedirs("json", exist_ok=True)
    with open(BLACKLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def get_immunised_ids() -> list[int]:
    """Récupère les IDs de serveurs immunisés depuis le .env."""
    raw = os.getenv("IMMUNISED_GUILDS", "")
    if not raw:
        return []
    return [int(i.strip()) for i in raw.split(",") if i.strip().isdigit()]


def is_high_permission(role: discord.Role) -> bool:
    """Retourne True si le rôle possède des permissions élevées."""
    high_perms = [
        "administrator",
        "manage_guild",
        "manage_roles",
        "manage_channels",
        "kick_members",
        "ban_members",
        "manage_messages",
        "manage_webhooks",
        "manage_expressions",
        "moderate_members",
    ]
    for perm in high_perms:
        if getattr(role.permissions, perm, False):
            return True
    return False


def get_entry(user_id: int) -> Optional[dict]:
    """Retourne l'entrée blacklist d'un utilisateur, ou None."""
    data = _load()
    return data["blacklist"].get(str(user_id))


def is_blacklisted(user_id: int) -> bool:
    return get_entry(user_id) is not None


def is_staff_blacklisted(user_id: int) -> bool:
    entry = get_entry(user_id)
    return entry is not None and entry["type"] == "staff"


def is_ban_blacklisted(user_id: int) -> bool:
    entry = get_entry(user_id)
    return entry is not None and entry["type"] == "ban"


# ---------------------------------------------------------------------------
# Actions sur la blacklist
# ---------------------------------------------------------------------------

def add_blacklist(
    user: discord.User,
    bl_type: str,  # "staff" ou "ban"
    raison: str,
    images: list[str],
    added_by: discord.User,
) -> None:
    """Ajoute un utilisateur à la blacklist."""
    data = _load()
    data["blacklist"][str(user.id)] = {
        "type": bl_type,
        "raison": raison,
        "images": images,
        "added_by": added_by.id,
        "added_by_name": str(added_by),
        "added_at": datetime.now(timezone.utc).isoformat(),
        "username": str(user),
    }
    _save(data)


def remove_blacklist(user_id: int) -> bool:
    """Retire un utilisateur de la blacklist. Retourne True si trouvé."""
    data = _load()
    key = str(user_id)
    if key not in data["blacklist"]:
        return False
    del data["blacklist"][key]
    _save(data)
    return True


def get_all_blacklisted() -> dict:
    """Retourne tout le dict blacklist."""
    return _load()["blacklist"]


# ---------------------------------------------------------------------------
# Autocomplétion /blacklists
# ---------------------------------------------------------------------------

async def blacklist_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[discord.app_commands.Choice[str]]:
    """Autocomplétion avec les usernames des membres blacklistés."""
    entries = get_all_blacklisted()
    choices = []
    for user_id, entry in entries.items():
        username = entry.get("username", user_id)
        if current.lower() in username.lower():
            label = f"[{entry['type'].upper()}] {username}"
            choices.append(discord.app_commands.Choice(name=label[:100], value=user_id))
    return choices[:25]


# ---------------------------------------------------------------------------
# Application de la blacklist (ban automatique)
# ---------------------------------------------------------------------------

async def apply_ban_blacklist(bot: discord.Client, user_id: int) -> None:
    """
    Bannit l'utilisateur de tous les serveurs non immunisés
    où il est présent ou peut être banni via l'API.
    """
    entry = get_entry(user_id)
    if not entry or entry["type"] != "ban":
        return

    immunised = get_immunised_ids()

    for guild in bot.guilds:
        if guild.id in immunised:
            continue
        try:
            await guild.ban(
                discord.Object(id=user_id),
                reason=f"[Blacklist Nexara] {entry['raison']}",
                delete_message_days=0,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass


async def enforce_ban_on_join(member: discord.Member) -> None:
    """
    Appelé dans on_member_join : si le membre est ban-blacklisté
    et que le serveur n'est pas immunisé, on le rebannit immédiatement.
    """
    if not is_ban_blacklisted(member.id):
        return

    immunised = get_immunised_ids()
    if member.guild.id in immunised:
        return

    entry = get_entry(member.id)
    raison = entry["raison"] if entry else "Blacklist Nexara"

    # MP à la personne qui a essayé de le faire rejoindre (impossible à identifier)
    # On bannit directement
    try:
        await member.guild.ban(
            member,
            reason=f"[Blacklist Nexara] {raison}",
            delete_message_days=0,
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


async def enforce_unban_attempt(
    bot: discord.Client,
    guild: discord.Guild,
    user: discord.User,
) -> None:
    """
    Appelé dans on_member_unban : si l'utilisateur est ban-blacklisté
    et que le serveur n'est pas immunisé, on le rebannit et on prévient
    le responsable de la tentative de débannissement.
    """
    if not is_ban_blacklisted(user.id):
        return

    immunised = get_immunised_ids()
    if guild.id in immunised:
        return

    entry = get_entry(user.id)
    raison = entry["raison"] if entry else "Blacklist Nexara"
    images = entry.get("images", []) if entry else []

    # Rebannir
    try:
        await guild.ban(
            discord.Object(id=user.id),
            reason=f"[Blacklist Nexara — Tentative de débannissement bloquée] {raison}",
            delete_message_days=0,
        )
    except (discord.Forbidden, discord.HTTPException):
        pass

    # Chercher qui a débanni (audit log)
    moderator = None
    try:
        async for log_entry in guild.audit_logs(
            limit=5, action=discord.AuditLogAction.unban
        ):
            if log_entry.target.id == user.id:
                moderator = log_entry.user
                break
    except (discord.Forbidden, discord.HTTPException):
        pass

    if moderator:
        await _mp_tentative(
            moderator,
            user,
            raison,
            images,
            action="débannissement",
        )


async def enforce_staff_blacklist_on_update(
    before: discord.Member,
    after: discord.Member,
) -> None:
    """
    Appelé dans on_member_update : si un rôle élevé est ajouté à un
    staff-blacklisté, on le retire immédiatement et on prévient l'auteur.
    """
    if not is_staff_blacklisted(after.id):
        return

    # Rôles ajoutés lors de cet update
    added_roles = [r for r in after.roles if r not in before.roles]
    high_roles = [r for r in added_roles if is_high_permission(r)]

    if not high_roles:
        return

    entry = get_entry(after.id)
    raison = entry["raison"] if entry else "Blacklist Staff Nexara"
    images = entry.get("images", []) if entry else []

    # Retirer les rôles élevés
    for role in high_roles:
        try:
            await after.remove_roles(role, reason=f"[Blacklist Staff Nexara] {raison}")
        except (discord.Forbidden, discord.HTTPException):
            pass

    # Identifier qui a attribué le rôle via l'audit log
    moderator = None
    try:
        async for log_entry in after.guild.audit_logs(
            limit=5, action=discord.AuditLogAction.member_role_update
        ):
            if log_entry.target.id == after.id:
                moderator = log_entry.user
                break
    except (discord.Forbidden, discord.HTTPException):
        pass

    if moderator:
        await _mp_tentative(
            moderator,
            after,
            raison,
            images,
            action="attribution de rôle élevé",
        )


# ---------------------------------------------------------------------------
# MP de notification de tentative
# ---------------------------------------------------------------------------

async def _mp_tentative(
    destinataire: discord.User | discord.Member,
    cible: discord.User | discord.Member,
    raison: str,
    images: list[str],
    action: str,
) -> None:
    """Envoie un MP au modérateur qui a tenté une action sur un blacklisté."""
    embed = discord.Embed(
        title="⚠️ Action bloquée — Membre blacklisté",
        description=(
            f"Tu as tenté une **{action}** sur un membre blacklisté par Nexara.\n"
            f"Cette action a été annulée automatiquement."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(name="Membre concerné", value=f"{cible} (`{cible.id}`)", inline=False)
    embed.add_field(name="Raison de la blacklist", value=raison, inline=False)

    if images:
        embed.add_field(name="Preuves", value="\n".join(images), inline=False)
        # On met la première image en thumbnail
        embed.set_image(url=images[0])

    try:
        await destinataire.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


# ---------------------------------------------------------------------------
# Commandes slash (appelées depuis bot1.py)
# ---------------------------------------------------------------------------

async def cmd_blacklist_staff(
    interaction: discord.Interaction,
    utilisateur_id: str,
    raison: str,
    image1: Optional[discord.Attachment],
    image2: Optional[discord.Attachment],
    image3: Optional[discord.Attachment],
    bot: discord.Client,
) -> None:
    from nexara_bot.logs import send_log, build_log

    if not utilisateur_id.strip().isdigit():
        await interaction.response.send_message("❌ ID invalide.", ephemeral=True)
        return

    uid = int(utilisateur_id)

    # Résolution de l'utilisateur
    try:
        user = await bot.fetch_user(uid)
    except discord.NotFound:
        await interaction.response.send_message("❌ Utilisateur introuvable.", ephemeral=True)
        return

    images = [a.url for a in [image1, image2, image3] if a is not None]
    add_blacklist(user, "staff", raison, images, interaction.user)

    # Application immédiate : retirer les rôles élevés sur tous les serveurs
    for guild in bot.guilds:
        member = guild.get_member(uid)
        if not member:
            continue
        for role in member.roles:
            if is_high_permission(role):
                try:
                    await member.remove_roles(role, reason=f"[Blacklist Staff Nexara] {raison}")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    await interaction.response.send_message(
        f"✅ **{user}** (`{uid}`) ajouté à la blacklist staff.", ephemeral=True
    )

    # Log
    embed = build_log(
        title="🚫 Blacklist Staff ajoutée",
        color=discord.Color.orange(),
        fields=[
            ("Membre", f"{user} (`{uid}`)", False),
            ("Raison", raison, False),
            ("Ajouté par", f"{interaction.user.mention} (`{interaction.user.id}`)", False),
            ("Preuves", "\n".join(images) if images else "Aucune", False),
        ],
    )
    await send_log(interaction.guild, embed)


async def cmd_blacklist_ban(
    interaction: discord.Interaction,
    utilisateur_id: str,
    raison: str,
    image1: Optional[discord.Attachment],
    image2: Optional[discord.Attachment],
    image3: Optional[discord.Attachment],
    bot: discord.Client,
) -> None:
    from nexara_bot.logs import send_log, build_log

    if not utilisateur_id.strip().isdigit():
        await interaction.response.send_message("❌ ID invalide.", ephemeral=True)
        return

    uid = int(utilisateur_id)

    try:
        user = await bot.fetch_user(uid)
    except discord.NotFound:
        await interaction.response.send_message("❌ Utilisateur introuvable.", ephemeral=True)
        return

    images = [a.url for a in [image1, image2, image3] if a is not None]
    add_blacklist(user, "ban", raison, images, interaction.user)

    # Ban immédiat sur tous les serveurs non immunisés
    await apply_ban_blacklist(bot, uid)

    await interaction.response.send_message(
        f"✅ **{user}** (`{uid}`) blacklisté et banni de tous les serveurs.", ephemeral=True
    )

    embed = build_log(
        title="🔨 Blacklist Ban ajoutée",
        color=discord.Color.red(),
        fields=[
            ("Membre", f"{user} (`{uid}`)", False),
            ("Raison", raison, False),
            ("Ajouté par", f"{interaction.user.mention} (`{interaction.user.id}`)", False),
            ("Preuves", "\n".join(images) if images else "Aucune", False),
        ],
    )
    await send_log(interaction.guild, embed)


async def cmd_blacklists(
    interaction: discord.Interaction,
    utilisateur: str,  # valeur venant de l'autocomplétion = user_id (str)
) -> None:
    entry = get_all_blacklisted().get(utilisateur)
    if not entry:
        await interaction.response.send_message("❌ Utilisateur non trouvé dans la blacklist.", ephemeral=True)
        return

    images = entry.get("images", [])
    added_at_raw = entry.get("added_at", "")
    try:
        added_at = f"<t:{int(datetime.fromisoformat(added_at_raw).timestamp())}:F>"
    except Exception:
        added_at = added_at_raw or "Inconnue"

    bl_type = "🔨 Ban" if entry["type"] == "ban" else "🚫 Staff"

    embed = discord.Embed(
        title=f"{bl_type} — {entry.get('username', utilisateur)}",
        color=discord.Color.red() if entry["type"] == "ban" else discord.Color.orange(),
    )
    embed.add_field(name="ID", value=utilisateur, inline=True)
    embed.add_field(name="Type", value=entry["type"].upper(), inline=True)
    embed.add_field(name="Raison", value=entry["raison"], inline=False)
    embed.add_field(name="Ajouté par", value=f"{entry.get('added_by_name', '?')} (`{entry.get('added_by', '?')}`)", inline=False)
    embed.add_field(name="Date", value=added_at, inline=False)

    if images:
        embed.add_field(name="Preuves", value="\n".join(images), inline=False)
        embed.set_image(url=images[0])

    await interaction.response.send_message(embed=embed, ephemeral=True)


async def cmd_unbl(
    interaction: discord.Interaction,
    utilisateur_id: str,
    raison: str,
    bot: discord.Client,
) -> None:
    from nexara_bot.logs import send_log, build_log

    if not utilisateur_id.strip().isdigit():
        await interaction.response.send_message("❌ ID invalide.", ephemeral=True)
        return

    uid = int(utilisateur_id)
    entry = get_entry(uid)

    if not entry:
        await interaction.response.send_message("❌ Cet utilisateur n'est pas blacklisté.", ephemeral=True)
        return

    bl_type = entry["type"]
    username = entry.get("username", str(uid))

    # On retire d'abord de la blacklist AVANT de débannir,
    # pour que l'event on_member_unban ne rebannisse pas immédiatement
    removed = remove_blacklist(uid)

    if not removed:
        await interaction.response.send_message("❌ Impossible de retirer la blacklist.", ephemeral=True)
        return

    # Si c'était un ban, on débannit sur tous les serveurs où il est banni
    if bl_type == "ban":
        for guild in bot.guilds:
            try:
                await guild.unban(
                    discord.Object(id=uid),
                    reason=f"[Unbl Nexara] {raison}",
                )
            except discord.NotFound:
                pass  # Pas banni sur ce serveur, on ignore
            except (discord.Forbidden, discord.HTTPException):
                pass

    await interaction.response.send_message(
        f"✅ **{username}** (`{uid}`) retiré de la blacklist"
        + (" et débanni de tous les serveurs." if bl_type == "ban" else "."),
        ephemeral=True
    )

    embed = build_log(
        title="✅ Blacklist retirée",
        color=discord.Color.green(),
        fields=[
            ("Membre", f"{username} (`{uid}`)", False),
            ("Type retiré", bl_type.upper(), False),
            ("Raison du retrait", raison, False),
            ("Retiré par", f"{interaction.user.mention} (`{interaction.user.id}`)", False),
        ],
    )
    await send_log(interaction.guild, embed)