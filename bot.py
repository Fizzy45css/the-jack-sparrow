import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import json
import os

PENDING_FILE = "pending.json"

def load_pending() -> set:
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_pending():
    with open(PENDING_FILE, "w") as f:
        json.dump(list(pending_requests), f)

# KONFIGURASI
DISCORD_TOKEN           = "MTUxMjAwNzk4ODg1NDkxNTExMw.GxQJUg.5kbQZL9huI9GiitEM-V-0VjohfT99_g6yTx7gI"
PTERODACTYL_URL         = "https://stellar.jagoanhosting.id"
PTERODACTYL_API_KEY     = "ptlc_OfK40qweZie4mkGqikZlzAqixOQTiDh1N7Gy93fJ4iY"
SERVER_ID               = "0da161b4"
ADMIN_CHANNEL_ID        = 1512053806442086481
PLAYER_CHANNEL_ID       = 1512247207896682506
MINECRAFT_MEMBER_ROLE   = 954264022654550027

# Path whitelist.json di dalam server Minecraft (sesuaikan jika beda)
# CATATAN: Path untuk Pterodactyl Files API itu RELATIF terhadap root
# direktori server (yang sejajar dengan server.properties), BUKAN
# path filesystem absolut "/home/container/...". Jadi cukup "/whitelist.json"
# kalau file-nya ada di root server.
WHITELIST_PATH = "/whitelist.json"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
synced = False
pending_requests = load_pending()


# ==============================
# PTERODACTYL HELPERS
# ==============================

async def send_mc_command(command: str) -> bool:
    url = f"{PTERODACTYL_URL}/api/client/servers/{SERVER_ID}/command"
    headers = {
        "Authorization": f"Bearer {PTERODACTYL_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={"command": command}) as resp:
            return resp.status == 204


async def read_whitelist_json() -> tuple[list | None, str | None]:
    """
    Baca whitelist.json dari server via Pterodactyl Files API.
    Return: (data, error_message)
    - Sukses: (list, None)
    - Gagal:  (None, "pesan error detail")
    """
    url = f"{PTERODACTYL_URL}/api/client/servers/{SERVER_ID}/files/contents"
    headers = {
        "Authorization": f"Bearer {PTERODACTYL_API_KEY}",
        "Accept": "application/json"
    }
    params = {"file": WHITELIST_PATH}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                text = await resp.text()
                if resp.status != 200:
                    return None, f"HTTP {resp.status}: {text[:300]}"
                try:
                    return json.loads(text), None
                except json.JSONDecodeError as e:
                    return None, f"JSON parse error: {e}\nContent: {text[:200]}"
    except Exception as e:
        return None, f"Request error: {e}"


async def write_whitelist_json(data: list) -> bool:
    """Tulis whitelist.json ke server via Pterodactyl Files API."""
    url = f"{PTERODACTYL_URL}/api/client/servers/{SERVER_ID}/files/write"
    headers = {
        "Authorization": f"Bearer {PTERODACTYL_API_KEY}",
        "Content-Type": "application/octet-stream",
    }
    params = {"file": WHITELIST_PATH}
    content = json.dumps(data, indent=2)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, params=params, data=content.encode("utf-8")) as resp:
            return resp.status == 204


# ==============================
# MCPROFILE.IO — FLOODGATE UUID
# ==============================

def xuid_to_floodgate_uuid(xuid: str) -> str | None:
    """
    Konversi XUID (decimal string) ke format Floodgate UUID.
    Format: 00000000-0000-0000-xxxx-xxxxxxxxxxxx
    """
    try:
        xuid_hex = format(int(xuid), "016x")  # 16 karakter hex, zero-padded
        return f"00000000-0000-0000-{xuid_hex[:4]}-{xuid_hex[4:]}"
    except (ValueError, TypeError):
        return None


async def fetch_floodgate_uuid(gamertag: str) -> str | None:
    """
    Ambil Floodgate UUID dari mcprofile.io untuk player Bedrock.

    Response mcprofile.io untuk /api/v1/bedrock/gamertag/{gamertag}:
    {
      "gamertag": "PlayerName",
      "xuid": "2535416109870123",
      "floodgate_uid": "00000000-0000-0000-xxxx-xxxxxxxxxxxx",
      "linked": false
    }

    - Prioritas: pakai field "floodgate_uid" langsung jika ada.
    - Fallback: konstruksi dari "xuid" jika "floodgate_uid" tidak ada.
    """
    url = f"https://mcprofile.io/api/v1/bedrock/gamertag/{gamertag}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            try:
                data = await resp.json(content_type=None)

                # Coba ambil floodgate_uid langsung
                fuid = data.get("floodgate_uid")
                if fuid:
                    return fuid

                # Fallback: bangun dari xuid
                xuid = data.get("xuid")
                if xuid:
                    return xuid_to_floodgate_uuid(xuid)

                return None
            except (KeyError, TypeError, aiohttp.ContentTypeError):
                return None


# ==============================
# WHITELIST VIEW
# ==============================

class WhitelistView(discord.ui.View):
    def __init__(self, requester: discord.Member, mc_username: str, platform: str):
        super().__init__(timeout=86400)
        self.requester = requester
        self.mc_username = mc_username
        self.platform = platform  # "java" atau "bedrock"

    async def on_timeout(self):
        pending_requests.discard(self.requester.id)
        save_pending()

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Kamu bukan admin!", ephemeral=True)
            return

        # Defer karena proses Bedrock butuh beberapa API call (bisa > 3 detik)
        await interaction.response.defer(ephemeral=True)

        success = False

        if self.platform == "bedrock":
            # ── BEDROCK: tulis langsung ke whitelist.json via UUID ──
            gamertag = self.mc_username.lstrip(".")  # hapus prefix "." untuk query

            # 1. Ambil Floodgate UUID dari mcprofile.io
            floodgate_uuid = await fetch_floodgate_uuid(gamertag)
            if not floodgate_uuid:
                await interaction.followup.send(
                    f"❌ Gagal mendapatkan UUID Floodgate untuk `{gamertag}` dari mcprofile.io.\n"
                    f"Cek apakah gamertag benar atau coba whitelist manual.",
                    ephemeral=True
                )
                return

            # 2. Baca whitelist.json yang ada
            whitelist, read_error = await read_whitelist_json()
            if whitelist is None:
                await interaction.followup.send(
                    f"❌ Gagal membaca `whitelist.json` dari server!\n```\n{read_error}\n```",
                    ephemeral=True
                )
                return

            # Validasi UUID sebelum lanjut
            if not floodgate_uuid or not isinstance(floodgate_uuid, str):
                await interaction.followup.send(
                    f"❌ UUID Floodgate tidak valid: `{floodgate_uuid}`",
                    ephemeral=True
                )
                return

            # 3. Cek duplikat (UUID atau nama sudah ada)
            already_exists = any(
                entry.get("uuid") == floodgate_uuid or
                entry.get("name", "").lower() == self.mc_username.lower()
                for entry in whitelist
            )

            if not already_exists:
                # Format standar whitelist.json Minecraft: hanya uuid dan name
                whitelist.append({
                    "uuid": floodgate_uuid,
                    "name": self.mc_username
                })

            # Validasi JSON sebelum ditulis ke server
            try:
                json.dumps(whitelist)
            except (TypeError, ValueError) as e:
                await interaction.followup.send(
                    f"❌ Data whitelist tidak valid, batal menulis!\n```\n{e}\n```",
                    ephemeral=True
                )
                return

            # 4. Tulis kembali whitelist.json
            write_ok = await write_whitelist_json(whitelist)
            if not write_ok:
                await interaction.followup.send(
                    "❌ Gagal menulis `whitelist.json` ke server!",
                    ephemeral=True
                )
                return

            # CATATAN: whitelist reload SENGAJA tidak dijalankan karena
            # Paper/Spigot akan membuat whitelist.json.backup dan mengosongkan
            # file saat reload dipicu dari luar lewat API.
            success = True

        else:
            # ── JAVA: pakai command whitelist add seperti biasa ──
            success = await send_mc_command(f"whitelist add {self.mc_username}")

        if success:
            minecraft_role = interaction.guild.get_role(MINECRAFT_MEMBER_ROLE)
            role_given = False
            if minecraft_role:
                if minecraft_role not in self.requester.roles:
                    await self.requester.add_roles(minecraft_role)
                    role_given = True

            embed = discord.Embed(
                title="✅ Whitelist Approved",
                description=f"`{self.mc_username}` Telah ditambahkan ke whitelist!",
                color=discord.Color.green()
            )
            embed.add_field(name="Approved by", value=interaction.user.mention, inline=True)
            embed.add_field(name="Player", value=self.requester.mention, inline=True)
            if role_given:
                embed.add_field(name="✳️Dan juga Role", value=f"<@&{MINECRAFT_MEMBER_ROLE}> diberikan!", inline=True)
            await interaction.message.edit(embed=embed, view=None)
            await interaction.followup.send("✅ Whitelist Berhasil!", ephemeral=True)

            player_channel = bot.get_channel(PLAYER_CHANNEL_ID)
            if player_channel:
                await player_channel.send(
                    f"✅ {self.requester.mention} Whitelist request untuk **{self.mc_username}** telah diapprove! Selamat bergabung! 🎮"
                )

            try:
                dm_msg = (
                    f"✅ Whitelist request untuk **{self.mc_username}** telah diapprove!\n"
                    f"Selamat bergabung di server! 🎮"
                )
                if role_given:
                    dm_msg += f"\n✳️Kamu juga mendapatkan role **Minecraft Member**!"
                await self.requester.send(dm_msg)
            except:
                pass
        else:
            await interaction.followup.send("❌ Gagal mengirim command ke server!", ephemeral=True)

        pending_requests.discard(self.requester.id)
        save_pending()
        self.stop()

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Terdeteksi, kamu bukan admin!", ephemeral=True)
            return

        embed = discord.Embed(
            title="❌ Whitelist Rejected",
            description=f"Request `{self.mc_username}` Rejected.",
            color=discord.Color.red()
        )
        embed.add_field(name="Rejected by", value=interaction.user.mention, inline=True)
        embed.add_field(name="Player", value=self.requester.mention, inline=True)
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("✅ Request direject!", ephemeral=True)

        player_channel = bot.get_channel(PLAYER_CHANNEL_ID)
        if player_channel:
            await player_channel.send(
                f"❌ {self.requester.mention} Whitelist request untuk **{self.mc_username}** telah direject oleh admin."
            )

        try:
            await self.requester.send(
                f"❌ Whitelist request untuk **{self.mc_username}** telah direject oleh admin."
            )
        except:
            pass

        pending_requests.discard(self.requester.id)
        save_pending()
        self.stop()


# ==============================
# SLASH COMMANDS
# ==============================

@bot.tree.command(name="whitelist", description="Whitelist Request ke server Minecraft")
@app_commands.describe(
    platform="Platform kamu (Java / Bedrock)",
    username="Username / Gamertag kamu"
)
@app_commands.choices(platform=[
    app_commands.Choice(name="☕ Java", value="java"),
    app_commands.Choice(name="🪨 Bedrock", value="bedrock"),
])
async def whitelist_request(interaction: discord.Interaction, platform: app_commands.Choice[str], username: str):
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.InteractionResponded:
        return

    if interaction.channel_id != PLAYER_CHANNEL_ID:
        await interaction.followup.send(
            f"❌ Command ini hanya bisa digunakan di <#{PLAYER_CHANNEL_ID}>!",
            ephemeral=True
        )
        return

    if interaction.user.id in pending_requests:
        await interaction.followup.send(
            "❌ Kamu sudah punya request whitelist yang sedang menunggu approval!\nTunggu sampai di-approve atau direject dulu.",
            ephemeral=True
        )
        return

    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if not admin_channel:
        await interaction.followup.send("❌ Admin channel tidak ditemukan!", ephemeral=True)
        return

    if platform.value == "bedrock":
        mc_username = f".{username}"
        platform_label = "🪨 Bedrock"
    else:
        mc_username = username
        platform_label = "☕ Java"

    embed = discord.Embed(
        title="📋 New Whitelist Request",
        description=f"{interaction.user.mention} ingin bergabung ke server!",
        color=discord.Color.blue()
    )
    embed.add_field(name="🎮 Minecraft Username", value=f"`{mc_username}`", inline=True)
    embed.add_field(name="🖥️ Platform", value=platform_label, inline=True)
    embed.add_field(name="💬 Discord", value=interaction.user.mention, inline=False)
    embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{mc_username}")
    embed.set_footer(text=f"User ID: {interaction.user.id}")

    pending_requests.add(interaction.user.id)
    save_pending()
    view = WhitelistView(interaction.user, mc_username, platform.value)  # platform diteruskan
    await admin_channel.send(embed=embed, view=view)

    await interaction.followup.send(
        f"✅ Request whitelist untuk **{mc_username}** ({platform_label}) sudah dikirim ke admin!\nTunggu approval ya!",
        ephemeral=True
    )


@bot.tree.command(name="unwhitelist", description="Hapus player dari whitelist (Admin Command)")
@app_commands.describe(username="Gamertag (tambah titik di depan untuk Bedrock, contoh: .Steve)")
async def unwhitelist(interaction: discord.Interaction, username: str):
    if interaction.channel_id != ADMIN_CHANNEL_ID:
        await interaction.response.send_message(
            f"❌ Command ini hanya bisa digunakan di <#{ADMIN_CHANNEL_ID}>!",
            ephemeral=True
        )
        return

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Terdeteksi, Kamu bukan admin!", ephemeral=True)
        return

    success = await send_mc_command(f"whitelist remove {username}")
    if success:
        await interaction.response.send_message(f"✅ **{username}** berhasil dihapus dari whitelist!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Gagal mengirim command ke server!", ephemeral=True)


@bot.tree.command(name="ban", description="Ban player dari server (Admin Command)")
@app_commands.describe(username="Gamertag", reason="Reason")
async def ban_player(interaction: discord.Interaction, username: str, reason: str = "Tidak ada alasan"):
    if interaction.channel_id != ADMIN_CHANNEL_ID:
        await interaction.response.send_message(
            f"❌ Command ini hanya bisa digunakan di <#{ADMIN_CHANNEL_ID}>!",
            ephemeral=True
        )
        return

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Terdeteksi, Kamu bukan admin!", ephemeral=True)
        return

    success = await send_mc_command(f"ban {username} {reason}")
    if success:
        embed = discord.Embed(title="🔨 Player Banned", color=discord.Color.dark_red())
        embed.add_field(name="Username", value=f"`{username}`", inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Banned by", value=interaction.user.mention, inline=True)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("❌ Gagal kirim command ke server!", ephemeral=True)


@bot.tree.command(name="unban", description="Unban player dari server (Admin Command)")
@app_commands.describe(username="Gamertag")
async def unban_player(interaction: discord.Interaction, username: str):
    if interaction.channel_id != ADMIN_CHANNEL_ID:
        await interaction.response.send_message(
            f"❌ Command ini hanya bisa digunakan di <#{ADMIN_CHANNEL_ID}>!",
            ephemeral=True
        )
        return

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Terdeteksi, Kamu bukan admin!", ephemeral=True)
        return

    success = await send_mc_command(f"pardon {username}")
    if success:
        await interaction.response.send_message(f"✅ **{username}** berhasil di-unban!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Gagal mengirim command ke server!", ephemeral=True)


@bot.tree.command(name="broadcast", description="Kirim gambar ke channel manapun (Admin Command)")
@app_commands.describe(
    channel="Channel tujuan",
    image="Gambar yang ingin dikirim",
    caption="Judul embed (opsional)"
)
async def broadcast(interaction: discord.Interaction, channel: discord.TextChannel, image: discord.Attachment, caption: str = ""):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Kamu bukan admin!", ephemeral=True)
        return

    if not image.content_type or not image.content_type.startswith("image/"):
        await interaction.response.send_message("❌ File harus berupa gambar!", ephemeral=True)
        return

    embed = discord.Embed(color=discord.Color.blurple())
    if caption:
        embed.title = caption
    embed.set_image(url=image.url)

    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Gambar berhasil dikirim ke {channel.mention}!", ephemeral=True)


@bot.tree.command(name="soggycat", description="soggy cat")
async def soggycat(interaction: discord.Interaction):
    await interaction.response.send_message("https://cdn.discordapp.com/attachments/1507566209833373800/1512033693869277194/soggycat.webp?ex=6a2b305c&is=6a29dedc&hm=8f24a58c07711c9917c047c63e9cc85f5993bf147d096e6846509953eccbf750&")


# BOT EVENTS

@bot.event
async def on_ready():
    global synced
    if not synced:
        await bot.tree.sync()
        synced = True
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Pirates of the Caribbean"))
    print(f"✅ Bot {bot.user} is Now Online!")


bot.run(DISCORD_TOKEN)