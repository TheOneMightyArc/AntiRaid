import discord
import datetime
from collections import defaultdict
from redbot.core import commands, Config

class AntiRaid(commands.Cog):
    """
    Monitors chat for spam velocity and mass mentions to prevent raids.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9944882211, force_registration=True)
        
        default_guild = {
            "enabled": False,
            "spam_limit": 7,
            "spam_interval": 5,
            "mention_limit": 5,
            "action": "mute",
            "mute_duration": 600,
            "whitelist_roles": [],
            # --- New Settings ---
            "log_channel": None,      # ID of channel to send detailed logs
            "ping_role": None,        # ID of role to ping
            "ping_message": "Raider detected! Action taken." # Custom message
        }
        self.config.register_guild(**default_guild)
        
        self.message_history = defaultdict(lambda: defaultdict(list))

    async def _punish_user(self, message: discord.Message, reason: str):
        """Executes the configured punishment, logs it, and pings mods."""
        guild = message.guild
        member = message.author
        settings = await self.config.guild(guild).all()
        action = settings["action"]

        if member.top_role >= guild.me.top_role:
            return 

        # --- 1. Execute Punishment ---
        try:
            # Delete spammy messages
            def check(m):
                return m.author == member and (message.created_at - m.created_at).total_seconds() < 10
            
            await message.channel.purge(limit=15, check=check)

            if action == "mute":
                duration = datetime.timedelta(seconds=settings["mute_duration"])
                await member.timeout(duration, reason=reason)
                action_verbed = "Muted"
            
            elif action == "kick":
                await member.kick(reason=reason)
                action_verbed = "Kicked"
            
            elif action == "ban":
                await member.ban(reason=reason, delete_message_days=1)
                action_verbed = "Banned"

        except discord.Forbidden:
            print(f"[AntiRaid] Missing permissions to punish {member.id} in {guild.name}.")
            return
        except Exception as e:
            print(f"[AntiRaid] Error: {e}")
            return

        # --- 2. Send Alert/Ping in Chat ---
        alert_content = f"🛡️ **AntiRaid:** {action_verbed} {member.mention} for spamming/raiding."
        
        if settings["ping_role"]:
            role = guild.get_role(settings["ping_role"])
            if role:
                # Add the ping and custom message
                alert_content = f"{role.mention} {settings['ping_message']}\n" + alert_content
        
        try:
            await message.channel.send(alert_content, allowed_mentions=discord.AllowedMentions(roles=True))
        except:
            pass

        # --- 3. Send Detailed Log ---
        if settings["log_channel"]:
            log_chan = guild.get_channel(settings["log_channel"])
            if log_chan:
                embed = discord.Embed(title="🛡️ AntiRaid Action Log", color=discord.Color.red(), timestamp=datetime.datetime.utcnow())
                embed.add_field(name="User", value=f"{member.name} ({member.id})", inline=True)
                embed.add_field(name="Action", value=action_verbed, inline=True)
                embed.add_field(name="Reason", value=reason, inline=False)
                embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                embed.set_thumbnail(url=member.display_avatar.url)
                
                try:
                    await log_chan.send(embed=embed)
                except:
                    print(f"[AntiRaid] Failed to send log to channel {settings['log_channel']}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        settings = await self.config.guild(message.guild).all()
        if not settings["enabled"]:
            return

        user_roles = [r.id for r in message.author.roles]
        if any(rid in settings["whitelist_roles"] for rid in user_roles):
            return
        if message.author.guild_permissions.administrator:
            return

        # --- CHECK 1: Mass Mentions ---
        mention_count = len(message.mentions) + len(message.role_mentions)
        if message.mention_everyone:
            mention_count += 1
        
        if mention_count >= settings["mention_limit"]:
            await self._punish_user(message, f"AntiRaid: Exceeded mention limit ({mention_count})")
            return

        # --- CHECK 2: Message Velocity ---
        now = message.created_at.timestamp()
        user_history = self.message_history[message.guild.id][message.author.id]
        
        user_history.append(now)
        
        cutoff = now - settings["spam_interval"]
        self.message_history[message.guild.id][message.author.id] = [
            t for t in user_history if t > cutoff
        ]
        
        if len(self.message_history[message.guild.id][message.author.id]) >= settings["spam_limit"]:
            self.message_history[message.guild.id][message.author.id] = []
            await self._punish_user(message, "AntiRaid: Exceeded message velocity limit")

    # --- Configuration Commands ---

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def antiraid(self, ctx: commands.Context):
        """Configure the AntiRaid system."""
        pass

    @antiraid.command(name="toggle")
    async def ar_toggle(self, ctx: commands.Context):
        """Enable or disable AntiRaid."""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        status = "Enabled" if not current else "Disabled"
        await ctx.send(f"AntiRaid is now **{status}**.")

    @antiraid.command(name="action")
    async def ar_action(self, ctx: commands.Context, action: str):
        """Set action: mute, kick, or ban."""
        if action.lower() not in ["mute", "kick", "ban"]:
            return await ctx.send("Action must be one of: `mute`, `kick`, `ban`.")
        await self.config.guild(ctx.guild).action.set(action.lower())
        await ctx.send(f"Punishment action set to: **{action.lower()}**.")

    @antiraid.command(name="spamlimit")
    async def ar_spamlimit(self, ctx: commands.Context, messages: int, seconds: int):
        """Set spam threshold (e.g. 7 messages in 5 seconds)."""
        await self.config.guild(ctx.guild).spam_limit.set(messages)
        await self.config.guild(ctx.guild).spam_interval.set(seconds)
        await ctx.send(f"Limit set: **{messages} messages** within **{seconds} seconds**.")

    @antiraid.command(name="mentionlimit")
    async def ar_mentionlimit(self, ctx: commands.Context, limit: int):
        """Set max mentions per message."""
        await self.config.guild(ctx.guild).mention_limit.set(limit)
        await ctx.send(f"Mention limit set to **{limit}** per message.")

    @antiraid.group(name="whitelist")
    async def ar_whitelist(self, ctx: commands.Context):
        """Manage exempt roles."""
        pass

    @ar_whitelist.command(name="add")
    async def wl_add(self, ctx: commands.Context, role: discord.Role):
        async with self.config.guild(ctx.guild).whitelist_roles() as wl:
            if role.id not in wl:
                wl.append(role.id)
                await ctx.send(f"Added {role.name} to whitelist.")
            else:
                await ctx.send("That role is already whitelisted.")

    @ar_whitelist.command(name="remove")
    async def wl_remove(self, ctx: commands.Context, role: discord.Role):
        async with self.config.guild(ctx.guild).whitelist_roles() as wl:
            if role.id in wl:
                wl.remove(role.id)
                await ctx.send(f"Removed {role.name} from whitelist.")
            else:
                await ctx.send("That role is not whitelisted.")

    # --- New Commands ---

    @antiraid.command(name="logchannel")
    async def ar_logchannel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set the log channel. Leave empty to disable logging."""
        if channel:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Logs will now be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("Logging disabled.")

    @antiraid.command(name="pingrole")
    async def ar_pingrole(self, ctx: commands.Context, role: discord.Role = None):
        """Set the role to ping when a raid is detected. Leave empty to disable."""
        if role:
            await self.config.guild(ctx.guild).ping_role.set(role.id)
            await ctx.send(f"I will ping {role.mention} when a raid triggers.")
        else:
            await self.config.guild(ctx.guild).ping_role.set(None)
            await ctx.send("Role pings disabled.")

    @antiraid.command(name="pingmessage")
    async def ar_pingmessage(self, ctx: commands.Context, *, message: str):
        """Set the custom text to send with the role ping."""
        await self.config.guild(ctx.guild).ping_message.set(message)
        await ctx.send(f"Ping message set to: `{message}`")

    @antiraid.command(name="view")
    async def ar_view(self, ctx: commands.Context):
        """View current settings."""
        s = await self.config.guild(ctx.guild).all()
        e = discord.Embed(title="AntiRaid Settings", color=discord.Color.red())
        e.add_field(name="Status", value="✅ Enabled" if s['enabled'] else "❌ Disabled")
        e.add_field(name="Action", value=s['action'].upper())
        e.add_field(name="Spam Limit", value=f"{s['spam_limit']} msgs / {s['spam_interval']} sec")
        e.add_field(name="Log Channel", value=f"<#{s['log_channel']}>" if s['log_channel'] else "None")
        e.add_field(name="Ping Role", value=f"<@&{s['ping_role']}>" if s['ping_role'] else "None")
        await ctx.send(embed=e)