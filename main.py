import asyncio
import os
from nexara_bot.bot1 import bot

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

async def main():
    token1 = os.getenv("TOKEN_BOT1")

    if not token1:
        print("-> Token manquant pour le bot.")
        return

    try:
        await bot.start(token1)
    except Exception as e:
        print(f"-> Le bot n'a pas pu démarrer : {e}")

if __name__ == "__main__":
    asyncio.run(main())