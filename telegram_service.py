"""
Telegram Service: message templates and sending logic.
All messages in Portuguese (Brazil) with emojis for engagement.
Uses Telegram Bot HTTP API directly (no python-telegram-bot dependency).
V2: Jitter/humanization - random delay, emoji variation, phrase rotation.
"""
import logging
import random
import time
import requests

import config

logger = logging.getLogger(__name__)

_message_sent_callback = None


def register_message_sent_callback(callback):
    """Register callback to be invoked when a message is successfully sent (for keep-alive tracking)."""
    global _message_sent_callback
    _message_sent_callback = callback


def init():
    """Initialize Telegram service (validates config)."""
    if not config.TELEGRAM_ENABLED:
        logger.warning("Telegram not configured (missing BOT_TOKEN or CHANNEL_ID). Messages will be logged only.")
        return False
    logger.info(f"Telegram initialized for channel: {config.TELEGRAM_CHANNEL_ID}")
    return True


def send_message(text, reply_to_message_id=None):
    """Send message to Telegram channel via HTTP API.
    Returns message_id (int) on success, None on failure/disabled.
    V2: Random 0.5-1.5s delay before send (humanization)."""
    if not config.TELEGRAM_ENABLED:
        logger.info(f"[TELEGRAM DISABLED] Would send:\n{text}")
        return None
    time.sleep(random.uniform(0.5, 1.5))
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = int(reply_to_message_id)
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.ok:
            data = resp.json()
            msg_id = data.get("result", {}).get("message_id")
            logger.info("Message sent to Telegram")
            if _message_sent_callback:
                try:
                    _message_sent_callback()
                except Exception as e:
                    logger.debug(f"message_sent_callback error: {e}")
            return msg_id
        logger.error(f"Telegram API error: {resp.status_code} - {resp.text}")
        return None
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return None


def format_currency(value):
    """Format value as Brazilian Real (e.g., 4.00)."""
    return f"{value:.2f}"


def _link_button():
    """Return the affiliate link formatted for messages."""
    return f"<a href='{config.AFFILIATE_LINK}'>🎰 APOSTE AGORA!</a>"


# Emoji arrays for humanization (pick randomly)
WIN_EMOJIS = ["💸", "💰", "🤑", "🏆", "✨"]
ALERT_EMOJIS = ["⚠️", "🔔", "📣"]
ANALYSIS_EMOJIS = ["🔍", "📈", "📡", "🧠"]
WIN_PHRASES = ["Lucro garantido!", "Na conta!", "Mais uma verde!"]


# ============================================================
# TEMPLATE 1: Daily Opener
# ============================================================
def send_daily_opener(yesterday_wins, yesterday_losses):
    """Send daily opener message (08:00 BRT)."""
    total = yesterday_wins + yesterday_losses
    pct = (yesterday_wins / total * 100) if total > 0 else 0
    text = f"""🟢 BOM DIA TIME! ESTAMOS ONLINE

📊 Ontem fechamos: {yesterday_wins} ✅ | {yesterday_losses} 🛑 ({pct:.0f}%)

Sinais começando agora! 

👉 O QUE FAZER:
- Fique de olho no grupo
- Quando chegar "SINAL CONFIRMADO" - siga as instruções
- Ative as notificações pra não perder nada 🔔

Bora lucrar hoje! 💪

{_link_button()}"""
    send_message(text)


# ============================================================
# Keep-Alive Messages (V2 - channel stays active during silence)
# ============================================================
KEEP_ALIVE_VARIANTS = [
    # Variant A
    "📊 Mercado instável no momento...\nAnalisando padrões para entrada segura. Fique atento! 👀",
    # Variant B
    "🌐 Modo de proteção ativo.\nAguardando estabilização do mercado para próxima entrada segura. 🛡️",
    # Variant C
    "🔍 Algoritmo em execução...\nMonitorando os próximos rounds. Sinal em breve! 📡",
]


def send_keep_alive_message(variant_index):
    """Send keep-alive message. variant_index 0=A, 1=B, 2=C. Max 1 per 5-min window."""
    if 0 <= variant_index < len(KEEP_ALIVE_VARIANTS):
        text = KEEP_ALIVE_VARIANTS[variant_index]
        send_message(text)


def send_cooldown_mode_message():
    """Send cooldown mode message when 3 consecutive rounds < 1.20x detected."""
    text = """📉 MODO COOLDOWN 📉

3 rounds seguidos abaixo de 1.20x detectados.

Algoritmo em pausa para proteção da banca.

Retornamos quando o mercado estabilizar. ⏳"""
    send_message(text)


# ============================================================
# TEMPLATE 2: Pattern Monitoring (Optional)
# ============================================================
def send_pattern_monitoring(count, remaining):
    """Send pattern monitoring message (3+ rounds of sequence detected). Random Analysis emoji."""
    emoji = random.choice(ANALYSIS_EMOJIS)
    text = f"""{emoji} Analisando padrões...

Últimas {count} rodadas abaixo de 2x
Aguardando confirmação ({remaining} restantes)

Fique pronto 👀"""
    send_message(text)


# ============================================================
# TEMPLATE 3: Signal
# ============================================================
def send_signal(last_round, target):
    """Send signal confirmation message (V2 style)."""
    # In V2 we focus on target/protection/gale max, not last_round text.
    target_multiplier = target
    protection_multiplier = target  # can be adjusted later if a distinct protection level is introduced
    gale_max = getattr(config, "MAX_GALE", 2)

    text = f"""🚀 SINAL CONFIRMADO 🚀

🎯 Alvo: {target_multiplier}x
🛡️ Proteção: {protection_multiplier}x
🔄 Gale Máx: {gale_max}

{_link_button()}"""
    return send_message(text)


# ============================================================
# TEMPLATE 4: Win Result
# ============================================================
def send_win_result(result, target, today_wins, today_losses, reply_to_message_id=None):
    """Send win result message (gale_depth = 0) - V2 style. Rotates emoji + phrase."""
    emoji = random.choice(WIN_EMOJIS)
    phrase = random.choice(WIN_PHRASES)
    text = f"""✅ GREEEEEN! {emoji}

{phrase}

{_link_button()}"""
    send_message(text, reply_to_message_id=reply_to_message_id)


# ============================================================
# TEMPLATE 5: Gale 1 Trigger
# ============================================================
def send_gale1_trigger(result, target, reply_to_message_id=None):
    """Send gale 1 warning message (V2 style). Random Alert emoji."""
    emoji = random.choice(ALERT_EMOJIS)
    text = f"""{emoji} GALE 1 {emoji}

Dobre a aposta! Entrada de recuperação.

{_link_button()}"""
    send_message(text, reply_to_message_id=reply_to_message_id)


# ============================================================
# TEMPLATE 6: Gale 2 Trigger
# ============================================================
def send_gale2_trigger(result, target, reply_to_message_id=None):
    """Send gale 2 warning message (V2 style). Random Alert emoji."""
    emoji = random.choice(ALERT_EMOJIS)
    text = f"""{emoji} GALE 2 {emoji}

Dobre a aposta! Entrada de recuperação.

{_link_button()}"""
    send_message(text, reply_to_message_id=reply_to_message_id)


# ============================================================
# TEMPLATE 7: Gale Recovery
# ============================================================
def send_gale_recovery(gale_depth, result, target, today_wins, today_losses, reply_to_message_id=None):
    """Send gale recovery message (gale 1 or 2 hit target). Optional reply threading."""
    text = f"""✅ RECUPERAMOS NO GALE {gale_depth}! - {result}x

Meta era {target}x - BATEU ✅

É pra isso que o sistema GALE existe! 
Quem confiou e dobrou tá lucrando agora 🤑

Hoje: {today_wins} ✅ | {today_losses} 🛑

Próximo sinal em breve 👀

{_link_button()}"""
    send_message(text, reply_to_message_id=reply_to_message_id)


# ============================================================
# TEMPLATE 8: Loss (Gale 2 Failed)
# ============================================================
def send_loss_message_telegram(result, today_wins, today_losses, reply_to_message_id=None):
    """Send loss message (gale 2 failed). Optional reply threading."""
    text = f"""🛑 STOP LOSS ATIVADO 🛑

Volatilidade detectada no mercado.

Pausando para proteger sua banca. 🛡️

Aguardando entrada segura...

{_link_button()}"""
    send_message(text, reply_to_message_id=reply_to_message_id)


# ============================================================
# TEMPLATE 9: Session Summary (replaces Hourly Scoreboard)
# ============================================================
def _performance_message_from_win_rate(win_rate):
    """Dynamic commentary based on win rate. Returns (emoji_prefix, message)."""
    if win_rate >= 75:
        return "🔥", "Sessão excelente! Algoritmo em alta performance."
    if win_rate >= 60:
        return "✅💎", "Sessão positiva. Consistência é a chave!"
    if win_rate >= 50:
        return "📊", "Mercado desafiador. Gestão de banca é essencial."
    return "⚠️", "Mercado volátil. Recomendamos cautela nas próximas entradas."


def send_session_summary(session_duration, total_signals, wins, losses, win_rate):
    """Send full session summary with dynamic commentary (V2). Uses Stop Loss for losses."""
    total = wins + losses
    win_rate_val = (wins / total * 100) if total > 0 else 0
    emoji_prefix, perf_msg = _performance_message_from_win_rate(win_rate_val)
    performance_message = f"{emoji_prefix} {perf_msg}"
    text = f"""📊 RESUMO DA SESSÃO 📊

━━━━━━━━━━━━━━━━━━━━

⏰ Tempo: {session_duration}
📈 Sinais enviados: {total_signals}
✅ Greens: {wins}
🛑 Stop Loss: {losses}
📊 Aproveitamento: {win_rate_val:.0f}%

━━━━━━━━━━━━━━━━━━━━

{performance_message}

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
🛑 Stop Loss: {today_losses}
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
🛑 Stop Loss: {losses}
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
🛑 Stop Loss: {losses}
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
        daily_lines.append(f"{day_name}:  {wins}✅ {losses}🛑 ({rate:.0f}%)")

    daily_str = "\n".join(daily_lines)

    text = f"""📊 RESUMO DA SEMANA

━━━━━━━━━━━━━━━━━━━━

{daily_str}

━━━━━━━━━━━━━━━━━━━━

📈 TOTAL DA SEMANA:
✅ {week_wins} vitórias
🛑 {week_losses} stop loss
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
