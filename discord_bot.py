"""Squad Radar as a Discord bot, nothing else: no website, no sample people.

    python3 discord_bot.py

Needs DISCORD_TOKEN in .env (see the README for creating the bot), and optionally a model key
(LLM_API_KEY / XAI_API_KEY) for summaries, translation and free-form answers."""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run  # noqa: E402
import web  # noqa: E402


def main():
    run.load_env()
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        print("No DISCORD_TOKEN in .env. Add the line  DISCORD_TOKEN=your_bot_token  and run this again.")
        return 1
    try:
        import discord  # noqa: F401
    except ImportError:
        print("The Discord library isn't installed. Run:  source .venv/bin/activate  then  pip install -r requirements.txt")
        return 1
    from discord_bridge import DiscordBridge
    app = web.App(os.environ.get("SQUAD_DISCORD_DATA") or os.path.join(HERE, "data-discord"), samples=False)
    if app.model.enabled:
        threading.Thread(target=app.model.pick_model, daemon=True).start()
    threading.Thread(target=web.ticker, args=(app,), daemon=True).start()  # reminders, bumps, Wrapped
    st = app.llm_status()
    print("Squad Radar for Discord. Model: %s. Connecting..." % (
        "%s %s" % (st.get("provider"), st.get("model") or "") if st.get("enabled") else "off (add LLM_API_KEY to .env)"))
    bridge = DiscordBridge(app, token, os.environ.get("DISCORD_CHANNELS", ""), website=False)
    try:
        bridge._run()  # runs until Ctrl+C
    except KeyboardInterrupt:
        pass
    print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
