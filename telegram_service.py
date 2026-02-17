"""
Telegram Service: message templates and sending logic.
All messages in Portuguese (Brazil) with emojis for engagement.
Uses Telegram Bot HTTP API directly (no python-telegram-bot dependency).
"""
import logging
import requests

import config

logger = logging.getLogger(__name__)


def init():
    """Initialize Telegram service (validates config)."""
    if not config.TELEGRAM_ENABLED:
        logger.warning("Telegram not configured (missing BOT_TOKEN or CHANNEL_ID). Messages will be logged only.")
        return False
    logger.info(f"Telegram initialized for channel: {config.TELEGRAM_CHANNEL_ID}")
    return True


def send_message(text):
    """Send message to Telegram channel via HTTP API."""
    if not config.TELEGRAM_ENABLED:
        logger.info(f"[TELEGRAM DISABLED] Would send:\n{text}")
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.ok:
            logger.info("Message sent to Telegram")
            return True
        logger.error(f"Telegram API error: {resp.status_code} - {resp.text}")
        return False
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def format_currency(value):
    """Format value as Brazilian Real (e.g., 4.00)."""
    return f"{value:.2f}"


def _link_button():
    """Return the affiliate link formatted for messages."""
    return f"<a href='{config.AFFILIATE_LINK}'>💰 JOGAR AGORA</a>"


# ============================================================
# TEMPLATE 1: Daily Opener
# ============================================================
def send_daily_opener(yesterday_wins, yesterday_losses):
    """Send daily opener message (08:00 BRT)."""
    total = yesterday_wins + yesterday_losses
    pct = (yesterday_wins / total * 100) if total > 0 else 0
    text = f"""🟢 BOM DIA TIME! ESTAMOS ONLINE

📊 Ontem fechamos: {yesterday_wins} ✅ | {yesterday_losses} ❌ ({pct:.0f}%)

Sinais começando agora! 

👉 O QUE FAZER:
- Fique de olho no grupo
- Quando chegar "SINAL CONFIRMADO" - siga as instruções
- Ative as notificações pra não perder nada 🔔

Bora lucrar hoje! 💪

{_link_button()}"""
    send_message(text)


# ============================================================
# TEMPLATE 2: Pattern Monitoring (Optional)
# ============================================================
def send_pattern_monitoring(count, remaining):
    """Send pattern monitoring message (3+ rounds of sequence detected)."""
    text = f"""🔍 Analisando padrões...

Últimas {count} rodadas abaixo de 2x
Aguardando confirmação ({remaining} restantes)

Fique pronto 👀"""
    send_message(text)


# ============================================================
# TEMPLATE 3: Signal
# ============================================================
def send_signal(last_round, target):
    """Send signal confirmation message."""
    text = f"""🚀 SINAL CONFIRMADO

👉 O QUE FAZER AGORA:
1. Abra o Aviator
2. Coloque Auto Cashout em {target}x
3. Aposte R$2-5
4. Clique APOSTAR

⏱️ Última rodada foi: {last_round}x
(confira na sua tela para garantir que está no jogo certo)

{_link_button()}"""
    send_message(text)


# ============================================================
# TEMPLATE 4: Win Result
# ============================================================
def send_win_result(result, target, today_wins, today_losses):
    """Send win result message (gale_depth = 0)."""
    profit_example = format_currency((target - 1) * 5)
    text = f"""✅ GANHAMOS! - {result}x

Meta era {target}x - BATEU ✅

Quem apostou R$5 lucrou R${profit_example}

Hoje: {today_wins} ✅ | {today_losses} ❌

Próximo sinal em breve 👀

{_link_button()}"""
    send_message(text)


# ============================================================
# TEMPLATE 5: Gale 1 Trigger
# ============================================================
def send_gale1_trigger(result, target):
    """Send gale 1 trigger message."""
    text = f"""⚠️ NÃO BATEU - Caiu em {result}x

🔁 GALE 1 - HORA DE RECUPERAR

👉 O QUE FAZER AGORA:
1. DOBRE sua aposta (apostou R$2? agora R$4)
2. Mesmo Auto Cashout: {target}x
3. Clique APOSTAR

Não desista - é assim que recuperamos! 💪

{_link_button()}"""
    send_message(text)


# ============================================================
# TEMPLATE 6: Gale 2 Trigger
# ============================================================
def send_gale2_trigger(result, target):
    """Send gale 2 trigger message."""
    text = f"""🔥 GALE 1 NÃO BATEU - Caiu em {result}x

🔁 GALE 2 - ÚLTIMA TENTATIVA

👉 O QUE FAZER AGORA:
1. DOBRE de novo (apostou R$4? agora R$8)
2. Mesmo Auto Cashout: {target}x
3. Clique APOSTAR

Última chance dessa sequência - bora! 🚀

{_link_button()}"""
    send_message(text)


# ============================================================
# TEMPLATE 7: Gale Recovery
# ============================================================
def send_gale_recovery(gale_depth, result, target, today_wins, today_losses):
    """Send gale recovery message (gale 1 or 2 hit target)."""
    text = f"""✅ RECUPERAMOS NO GALE {gale_depth}! - {result}x

Meta era {target}x - BATEU ✅

É pra isso que o sistema GALE existe! 
Quem confiou e dobrou tá lucrando agora 🤑

Hoje: {today_wins} ✅ | {today_losses} ❌

Próximo sinal em breve 👀

{_link_button()}"""
    send_message(text)


# ============================================================
# TEMPLATE 8: Loss (Gale 2 Failed)
# ============================================================
def send_loss_message_telegram(result, today_wins, today_losses):
    """Send loss message (gale 2 failed)."""
    text = f"""🔴 STOP LOSS ATIVADO 🔴

Volatilidade detectada no mercado.

Pausando para proteger sua banca. 🛡️

Aguardando entrada segura...

{_link_button()}"""
    send_message(text)


# ============================================================
# TEMPLATE 9: Hourly Scoreboard
# ============================================================
def send_hourly_scoreboard(result_emojis, period_wins, period_losses):
    """Send hourly scoreboard (every 2 hours)."""
    total = period_wins + period_losses
    pct = (period_wins / total * 100) if total > 0 else 0
    text = f"""📊 COMO ESTAMOS NAS ÚLTIMAS 2 HORAS:

{result_emojis}

{period_wins} vitórias | {period_losses} derrotas ({pct:.0f}%)

👉 Ainda não tá jogando? 
Olha o que você tá perdendo! ☝️

{_link_button()}"""
    send_message(text)


# ============================================================
# TEMPLATE 10: Daily Close
# ============================================================
def send_daily_close(today_wins, today_losses):
    """Send daily close message (23:00 BRT)."""
    total = today_wins + today_losses
    pct = (today_wins / total * 100) if total > 0 else 0
    text = f"""🌙 ENCERRANDO O DIA

📊 Resultado final de hoje:
✅ Vitórias: {today_wins}
❌ Derrotas: {today_losses}
📈 Taxa de acerto: {pct:.0f}%

Valeu por jogar com a gente, time! 🙏

Voltamos amanhã às 8h com mais sinais.
Ativa a notificação pra não perder! 🔔

Descansa e até amanhã 💪

{_link_button()}"""
    send_message(text)


# ============================================================
# RECAP: Mid-Day
# ============================================================
def send_midday_recap(result_emojis, wins, losses, best_streak):
    """Send mid-day recap (14:00 BRT)."""
    total = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 0
    text = f"""📊 MEIO DO DIA - COMO ESTAMOS

{result_emojis}

✅ Vitórias: {wins}
❌ Derrotas: {losses}
📈 Taxa: {win_rate:.0f}%

🔥 Maior sequência: {best_streak} seguidas

Ainda temos a tarde toda! Bora time 💪

{_link_button()}"""
    send_message(text)


# ============================================================
# RECAP: End of Day
# ============================================================
def send_end_of_day_recap(result_emojis, wins, losses, best_streak, total_signals):
    """Send end of day recap (22:30 BRT, before daily close)."""
    total = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 0

    if win_rate >= 85:
        performance_message = "DIA INCRÍVEL! Quem seguiu os sinais tá sorrindo! 🤑"
    elif win_rate >= 75:
        performance_message = "Dia sólido time! Consistência é o que paga. 💪"
    elif win_rate >= 65:
        performance_message = "Dia ok. Alguns Gales pesados mas recuperamos. 👊"
    else:
        performance_message = "Dia difícil. Faz parte. Amanhã voltamos mais fortes. 🔄"

    text = f"""📊 RESULTADO DO DIA

{result_emojis}

━━━━━━━━━━━━━━━━━━━━

✅ Vitórias: {wins}
❌ Derrotas: {losses}
📈 Taxa de acerto: {win_rate:.0f}%

🔥 Maior sequência: {best_streak} seguidas
💰 Sinais enviados: {total_signals}

━━━━━━━━━━━━━━━━━━━━

{performance_message}

Voltamos amanhã às 8h! 🚀

{_link_button()}"""
    send_message(text)


# ============================================================
# RECAP: Weekly
# ============================================================
def send_weekly_recap(daily_data, week_wins, week_losses, week_total_signals, best_day, best_day_rate):
    """
    Send weekly recap (Sunday 21:00 BRT).
    daily_data: list of dicts [{'day': 'Segunda', 'wins': X, 'losses': Y, 'rate': Z}, ...]
    """
    total = week_wins + week_losses
    week_rate = (week_wins / total * 100) if total > 0 else 0

    daily_lines = []
    for day_data in daily_data:
        day_name = day_data['day']
        wins = day_data['wins']
        losses = day_data['losses']
        rate = day_data['rate']
        daily_lines.append(f"{day_name}:  {wins}✅ {losses}❌ ({rate:.0f}%)")

    daily_str = "\n".join(daily_lines)

    text = f"""📊 RESUMO DA SEMANA

━━━━━━━━━━━━━━━━━━━━

{daily_str}

━━━━━━━━━━━━━━━━━━━━

📈 TOTAL DA SEMANA:
✅ {week_wins} vitórias
❌ {week_losses} derrotas
🎯 {week_rate:.0f}% de acerto

🔥 Melhor dia: {best_day} ({best_day_rate:.0f}%)
📊 Sinais enviados: {week_total_signals}

━━━━━━━━━━━━━━━━━━━━

Semana que vem tem mais! Bora time 🚀

{_link_button()}"""
    send_message(text)


# ============================================================
# STREAK CELEBRATION
# ============================================================
def send_streak_celebration(streak, streak_emojis=""):
    """Send streak celebration (5, 10, 15, 20+ wins in a row)."""
    if streak == 5:
        text = f"""🔥 5 SEGUIDAS!

✅✅✅✅✅

Quem tá junto tá lucrando! Bora continuar 💪

{_link_button()}"""
    elif streak == 10:
        text = f"""🔥🔥 10 SEGUIDAS! 🔥🔥

✅✅✅✅✅✅✅✅✅✅

O TIME TÁ ON FIRE! 🚀

Quem não tá acompanhando tá perdendo dinheiro!

{_link_button()}"""
    else:  # 15+
        if not streak_emojis:
            streak_emojis = "✅" * streak
        text = f"""🚨🚨🚨 {streak} SEGUIDAS! 🚨🚨🚨

{streak_emojis}

HISTÓRICO! Dia pra contar pros netos! 🤑

Print isso aqui e manda pros amigos!

{_link_button()}"""
    send_message(text)
