import os
import requests

def send_telegram_alert(title: str, price: str, url: str, source: str = "PistonHeads", mileage: str = "N/A", year: str = "N/A"):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("Telegram credentials missing in environment variables.")
        return

    message = (
        f"🚗 **New Listing Found!** ({source})\n\n"
        f"**Title:** {title}\n"
        f"**Price:** {price}\n"
        f"**Year:** {year}\n"
        f"**Mileage:** {mileage}\n"
        f"**Link:** [View Listing]({url})"
    )

    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }

    try:
        response = requests.post(telegram_url, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")