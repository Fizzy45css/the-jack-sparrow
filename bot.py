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
DISCORD_TOKEN       = "MTUxMjAwNzk4ODg1NDkxNTExMw.GbF-MD.2aozQoUuzNS-bw5sakYQvBSyXUggbFjlOkq400"
PTERODACTYL_URL     = "https://stellar.jagoanhosting.id"
PTERODACTYL_API_KEY = "ptlc_NV9KjOJmOIIms4ZmMMTDoU38HIAPW9z47xZ5KD7rQSn"
SERVER_ID           = "0da161b4"
ADMIN_CHANNEL_ID    = 1512053806442086481  # Channel Moderator (Approve/Reject)
PLAYER_CHANNEL_ID   = 1512011864161124442  # Channel Player (Request Whitelist)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
synced = False
pending_requests = load_pending()  # Load dari file supaya persistent saat bot restart


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


class WhitelistView(discord.ui.View):
    def __init__(self, requester: discord.Member, mc_username: str):
        super().__init__(timeout=86400)  # 24 jam timeout
        self.requester = requester
        self.mc_username = mc_username

    async def on_timeout(self):
        pending_requests.discard(self.requester.id)
        save_pending()

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Kamu bukan admin!", ephemeral=True)
            return

        success = await send_mc_command(f"whitelist add {self.mc_username}")

        if success:
            embed = discord.Embed(
                title="✅ Whitelist Approved",
                description=f"`{self.mc_username}` Telah ditambahkan ke whitelist!",
                color=discord.Color.green()
            )
            embed.add_field(name="Approved by", value=interaction.user.mention, inline=True)
            embed.add_field(name="Player", value=self.requester.mention, inline=True)
            await interaction.message.edit(embed=embed, view=None)
            await interaction.response.send_message("✅ Whitelist Berhasil!", ephemeral=True)

            # Kirim ke channel player
            player_channel = bot.get_channel(PLAYER_CHANNEL_ID)
            if player_channel:
                await player_channel.send(
                    f"✅ {self.requester.mention} Whitelist request untuk **{self.mc_username}** telah diapprove! Selamat bergabung! 🎮"
                )

            # Kirim DM ke player
            try:
                await self.requester.send(
                    f"✅ Whitelist request untuk **{self.mc_username}** telah diapprove!\n"
                    f"Selamat bergabung di server! 🎮"
                )
            except:
                pass
        else:
            await interaction.response.send_message("❌ Gagal mengirim command ke server!", ephemeral=True)

        pending_requests.discard(self.requester.id)  # Hapus dari pending setelah selesai
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

        # Kirim ke channel player
        player_channel = bot.get_channel(PLAYER_CHANNEL_ID)
        if player_channel:
            await player_channel.send(
                f"❌ {self.requester.mention} Whitelist request untuk **{self.mc_username}** telah direject oleh admin."
            )

        # Kirim DM ke player
        try:
            await self.requester.send(
                f"❌ Whitelist request untuk **{self.mc_username}** telah direject oleh admin."
            )
        except:
            pass

        pending_requests.discard(self.requester.id)  # Hapus dari pending setelah selesai
        save_pending()
        self.stop()


# ==============================
# SLASH COMMANDS
# ==============================

@bot.tree.command(name="whitelist", description="Whitelist Request ke server Minecraft")
@app_commands.describe(username="Gamertag")
async def whitelist_request(interaction: discord.Interaction, username: str):
    # Klaim interaksi duluan — instance kedua akan gagal di sini dan berhenti
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.InteractionResponded:
        return  # Instance lain sudah menangani interaksi ini

    # Hanya boleh di channel player
    if interaction.channel_id != PLAYER_CHANNEL_ID:
        await interaction.followup.send(
            f"❌ Command ini hanya bisa digunakan di <#{PLAYER_CHANNEL_ID}>!",
            ephemeral=True
        )
        return

    # Cek apakah user sudah punya request pending
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

    embed = discord.Embed(
        title="📋 New Whitelist Request",
        description=f"{interaction.user.mention} ingin bergabung ke server!",
        color=discord.Color.blue()
    )
    embed.add_field(name="🎮 Minecraft Username", value=f"`{username}`", inline=True)
    embed.add_field(name="💬 Discord", value=interaction.user.mention, inline=True)
    embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{username}")
    embed.set_footer(text=f"User ID: {interaction.user.id}")

    pending_requests.add(interaction.user.id)  # Tandai user sebagai pending
    save_pending()
    view = WhitelistView(interaction.user, username)
    await admin_channel.send(embed=embed, view=view)

    await interaction.followup.send(
        f"✅ Request whitelist untuk **{username}** sudah dikirim ke admin!\nTunggu approval ya!",
        ephemeral=True
    )


@bot.tree.command(name="unwhitelist", description="Hapus player dari whitelist (Admin Command)")
@app_commands.describe(username="Gamertag")
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


@bot.tree.command(name="soggycat", description="soggy cat")
async def soggycat(interaction: discord.Interaction):
    embed = discord.Embed()
    embed.set_image(url="https://soggy.cat/static/ssoggycat/main/images/soggycat.webp")
    await interaction.response.send_message(embed=embed)


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