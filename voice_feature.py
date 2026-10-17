# ============================================================
# FITUR VOICE: /join dan /leave (bot join voice lalu diam saja)
#
# Cara pasang di bot.py:
# 1. Di bagian import paling atas, tambahkan:
#        from typing import Optional
# 2. Paste semua kode di bawah garis ini TEPAT DI ATAS baris
#    "# BOT EVENTS"
# 3. requirements.txt harus berisi:  discord.py[voice]
#    (tanpa ini, join voice akan error "needed in order to use voice")
# 4. Restart bot. Slash command baru otomatis ke-sync di on_ready.
# ============================================================


# VOICE (JOIN & DIAM)

@bot.tree.command(name="join", description="Suruh bot join voice channel dan diam di sana (Admin Command)")
@app_commands.guild_only()
@app_commands.describe(channel="Voice channel tujuan (kosongkan untuk pakai voice channel kamu sekarang)")
async def join_voice(interaction: discord.Interaction, channel: Optional[discord.VoiceChannel] = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Kamu bukan admin!", ephemeral=True)
        return

    # Kalau channel tidak diisi, pakai voice channel yang sedang dipakai user
    if channel is None:
        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message(
                "❌ Masuk voice channel dulu, atau pilih channel-nya di command!",
                ephemeral=True
            )
            return
        channel = interaction.user.voice.channel

    if not channel.permissions_for(interaction.guild.me).connect:
        await interaction.response.send_message(
            f"❌ Bot tidak punya izin **Connect** di {channel.mention}!",
            ephemeral=True
        )
        return

    # Connect ke voice bisa lebih dari 3 detik, jadi defer dulu
    await interaction.response.defer(ephemeral=True)

    try:
        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            if vc.channel.id == channel.id:
                await interaction.followup.send(f"ℹ️ Bot sudah ada di {channel.mention}.", ephemeral=True)
                return
            await vc.move_to(channel)
        else:
            if vc:  # sisa koneksi lama yang sudah mati
                await vc.disconnect(force=True)
            # self_deaf + self_mute = bot benar-benar standby, tidak ngapa-ngapain
            await channel.connect(self_deaf=True, self_mute=True)
    except asyncio.TimeoutError:
        await interaction.followup.send("❌ Timeout saat connect ke voice. Coba lagi.", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"❌ Gagal join voice: `{e}`", ephemeral=True)
        return

    await interaction.followup.send(f"✅ Bot join ke {channel.mention} dan standby diam.", ephemeral=True)


@bot.tree.command(name="leave", description="Suruh bot keluar dari voice channel (Admin Command)")
@app_commands.guild_only()
async def leave_voice(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Kamu bukan admin!", ephemeral=True)
        return

    vc = interaction.guild.voice_client
    if vc is None:
        await interaction.response.send_message(
            "❌ Bot lagi tidak ada di voice channel manapun!",
            ephemeral=True
        )
        return

    await vc.disconnect(force=True)
    await interaction.response.send_message("✅ Bot sudah keluar dari voice channel.", ephemeral=True)
