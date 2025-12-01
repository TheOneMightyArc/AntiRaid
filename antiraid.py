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
            "spam_limit": 7,        # Max messages allowed...
            "spam_interval": 5,     # ...within this many seconds.
            "mention_limit": 5,     # Max mentions per message
            "action": "mute",       # mute, kick, ban
            "mute_duration": 600,   # Seconds (10 mins)
            "whitelist_roles": []
        }
        self.config.register_guild(**default_guild)
        
        # In-memory storage for spam tracking
        # {guild_id: {user_id: [timestamp1, timestamp2]}}
        self.message_history = defaultdict(lambda: defaultdict(list))

    async def _punish_user(self, message: discord.Message, reason: str):
        """Executes the configured punishment."""
        guild = message.guild
        member = message.author
        settings = await self.config.guild(guild).all()
        action = settings["action"]

        # Check hierarchy
        if member.top_role >= guild.me.top_role:
            return # Cannot punish someone higher than the bot

        try:
            # 1. Delete the triggering message(s)
            def check(m):
                return m.author == member and (message.created_at - m.created_at).total_seconds() < 10
            
            await message.channel.purge(limit=15, check=check)

            # 2. Execute Action
            if action == "mute":
                duration = datetime.timedelta(seconds=settings["mute_duration"])
                await member.timeout(duration, reason=reason)
                await message.channel.send(f"🛡️ **AntiRaid:** Muted {member.mention} for spamming/raiding.")
            
            elif action == "kick":
                await member.kick(reason=reason)
                await message.channel.send(f"🛡️ **AntiRaid:** Kicked {member.mention} for spamming/raiding.")
            
            elif action == "ban":
                await member.ban(reason=reason, delete_message_days=1)
                await message.channel.send(f"🛡️ **AntiRaid:** Banned {member.mention} for spamming/raiding.")

        except discord.Forbidden:
            print(f"[AntiRaid] Missing permissions to punish {member.id} in {guild.name}.")
        except Exception as e:
            print(f"[AntiRaid] Error: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots, DMs, and webhooks
        if not message.guild or message.author.bot:
            return

        settings = await self.config.guild(message.guild).all()
        if not settings["enabled"]:
            return

        # Check Whitelist
        user_roles = [r.id for r in message.author.roles]
        if any(rid in settings["whitelist_roles"] for rid in user_roles):
            return
        if message.author.guild_permissions.administrator:
            return

        # --- CHECK 1: Mass Mentions ---
        # Count user mentions + role mentions + @everyone/@here
        mention_count = len(message.mentions) + len(message.role_mentions)
        if message.mention_everyone:
            mention_count += 1
        
        if mention_count >= settings["mention_limit"]:
            await self._punish_user(message, f"AntiRaid: Exceeded mention limit ({mention_count})")
            return

        # --- CHECK 2: Message Velocity (Spam) ---
        now = message.created_at.timestamp()
        user_history = self.message_history[message.guild.id][message.author.id]
        
        # Add current message time
        user_history.append(now)
        
        # Remove timestamps older than the interval
        # Keep only messages sent within the last 'spam_interval' seconds
        cutoff = now - settings["spam_interval"]
        self.message_history[message.guild.id][message.author.id] = [
            t for t in user_history if t > cutoff
        ]
        
        # Check if count exceeds limit
        if len(self.message_history[message.guild.id][message.author.id]) >= settings["spam_limit"]:
            # Clear history so we don't try to ban them twice instantly
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

    @antiraid.command(name="view")
    async def ar_view(self, ctx: commands.Context):
        """View current settings."""
        s = await self.config.guild(ctx.guild).all()
        e = discord.Embed(title="AntiRaid Settings", color=discord.Color.red())
        e.add_field(name="Status", value="✅ Enabled" if s['enabled'] else "❌ Disabled")
        e.add_field(name="Action", value=s['action'].upper())
        e.add_field(name="Spam Sensitivity", value=f"{s['spam_limit']} msgs in {s['spam_interval']} sec")
        e.add_field(name="Mention Limit", value=s['mention_limit'])
        await ctx.send(embed=e)