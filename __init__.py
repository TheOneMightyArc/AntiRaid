from .antiraid import AntiRaid

async def setup(bot):
    await bot.add_cog(AntiRaid(bot))