import asyncio
import os

from bot.bot import bot

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


async def main():
    token = os.getenv("TOKEN_BOT")
    if not token:
        print("-> Token manquant pour le bot.")
        return
    try:
        await bot.start(token)
    except Exception as e:
        print(f"-> Le bot n'a pas pu démarrer : {e}")


if __name__ == "__main__":
    asyncio.run(main())