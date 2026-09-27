# -*- coding: utf-8 -*-
"""Multilingual phrase table for local offline speech.

Every string that the device speaks through the local TTS engine lives here,
keyed by a short phrase ID and a language code.  At startup the companion
renders the phrases for the configured ``DEVICE_LANG`` and plays them as PCM —
the same path the English hazard phrase already uses.

Adding a language: copy the ``"en"`` block, translate every value, and add the
new code to ``LANGUAGES``.  Keep ``{…}`` format placeholders identical across
languages.
"""

LANGUAGES = ("en", "ko", "zh", "ja", "es")
DEFAULT = "en"

# TTS voice mapping: which voice name to use for each language on each engine.
# espeak-ng uses language codes; macOS ``say`` uses named voices.
ESPEAK_VOICES = {
    "en": "en",
    "ko": "ko",
    "zh": "zh",
    "ja": "ja",
    "es": "es",
}

# macOS voices — these are built-in on recent macOS versions.
# If a voice is missing, ``say`` falls back to the default system voice.
SAY_VOICES = {
    "en": None,       # Use the default English voice.
    "ko": "Yuna",
    "zh": "Ting-Ting",
    "ja": "Kyoko",
    "es": "Paulina",
}

# ---------------------------------------------------------------------------
# Phrase table
# ---------------------------------------------------------------------------

PHRASES = {
    # -- Hazard warnings (companion/voice/hazards.py) -----------------------
    "obstacle_ahead": {
        "en": "Obstacle ahead.",
        "ko": "전방에 장애물.",
        "zh": "前方有障碍物。",
        "ja": "前方に障害物。",
        "es": "Obstáculo adelante.",
    },
    "head_obstacle_ahead": {
        "en": "Head-height obstacle ahead.",
        "ko": "전방 머리 높이에 장애물.",
        "zh": "前方头部高度有障碍物。",
        "ja": "前方、頭の高さに障害物。",
        "es": "Obstáculo a la altura de la cabeza adelante.",
    },
    "head_obstacle_ahead_left": {
        "en": "Head-height obstacle ahead, left.",
        "ko": "전방 왼쪽 머리 높이에 장애물.",
        "zh": "前方左侧头部高度有障碍物。",
        "ja": "前方左、頭の高さに障害物。",
        "es": "Obstáculo a la altura de la cabeza adelante, a la izquierda.",
    },
    "head_obstacle_ahead_right": {
        "en": "Head-height obstacle ahead, right.",
        "ko": "전방 오른쪽 머리 높이에 장애물.",
        "zh": "前方右侧头部高度有障碍物。",
        "ja": "前方右、頭の高さに障害物。",
        "es": "Obstáculo a la altura de la cabeza adelante, a la derecha.",
    },
    "obstacle_ahead_left": {
        "en": "Obstacle ahead, left.",
        "ko": "전방 왼쪽에 장애물.",
        "zh": "前方左侧有障碍物。",
        "ja": "前方左に障害物。",
        "es": "Obstáculo adelante, a la izquierda.",
    },
    "obstacle_ahead_right": {
        "en": "Obstacle ahead, right.",
        "ko": "전방 오른쪽에 장애물.",
        "zh": "前方右侧有障碍物。",
        "ja": "前方右に障害物。",
        "es": "Obstáculo adelante, a la derecha.",
    },
    "stop_obstacle_ahead": {
        "en": "Stop. Obstacle ahead.",
        "ko": "멈추세요. 전방에 장애물.",
        "zh": "停下。前方有障碍物。",
        "ja": "止まって。前方に障害物。",
        "es": "Alto. Obstáculo adelante.",
    },
    "stop_head_obstacle_ahead": {
        "en": "Stop. Head-height obstacle ahead.",
        "ko": "멈추세요. 전방 머리 높이에 장애물.",
        "zh": "停下。前方头部高度有障碍物。",
        "ja": "止まって。前方、頭の高さに障害物。",
        "es": "Alto. Obstáculo a la altura de la cabeza adelante.",
    },
    "stop_head_obstacle_ahead_left": {
        "en": "Stop. Head-height obstacle ahead, left.",
        "ko": "멈추세요. 전방 왼쪽 머리 높이에 장애물.",
        "zh": "停下。前方左侧头部高度有障碍物。",
        "ja": "止まって。前方左、頭の高さに障害物。",
        "es": "Alto. Obstáculo a la altura de la cabeza adelante, a la izquierda.",
    },
    "stop_head_obstacle_ahead_right": {
        "en": "Stop. Head-height obstacle ahead, right.",
        "ko": "멈추세요. 전방 오른쪽 머리 높이에 장애물.",
        "zh": "停下。前方右侧头部高度有障碍物。",
        "ja": "止まって。前方右、頭の高さに障害物。",
        "es": "Alto. Obstáculo a la altura de la cabeza adelante, a la derecha.",
    },
    "stop_obstacle_ahead_left": {
        "en": "Stop. Obstacle ahead, left.",
        "ko": "멈추세요. 전방 왼쪽에 장애물.",
        "zh": "停下。前方左侧有障碍物。",
        "ja": "止まって。前方左に障害物。",
        "es": "Alto. Obstáculo adelante, a la izquierda.",
    },
    "stop_obstacle_ahead_right": {
        "en": "Stop. Obstacle ahead, right.",
        "ko": "멈추세요. 전방 오른쪽에 장애물.",
        "zh": "停下。前方右侧有障碍物。",
        "ja": "止まって。前方右に障害物。",
        "es": "Alto. Obstáculo adelante, a la derecha.",
    },
    "hazard_unavailable": {
        "en": "Hazard sensing unavailable. Use your cane.",
        "ko": "위험 감지를 사용할 수 없습니다. 지팡이를 사용하세요.",
        "zh": "危险感应不可用。请使用盲杖。",
        "ja": "危険感知が使用できません。杖を使ってください。",
        "es": "Detección de peligros no disponible. Use su bastón.",
    },
    "hazard_ready": {
        "en": "Hazard warnings ready. Floor hazards are not monitored.",
        "ko": "위험 경고 준비 완료. 바닥 위험은 감지하지 않습니다.",
        "zh": "危险警告已就绪。不监测地面危险。",
        "ja": "危険警告の準備ができました。床の危険は監視しません。",
        "es": "Alertas de peligro listas. Los peligros en el suelo no se monitorean.",
    },
    "hazard_restored": {
        "en": "Hazard sensing restored.",
        "ko": "위험 감지가 복원되었습니다.",
        "zh": "危险感应已恢复。",
        "ja": "危険感知が復旧しました。",
        "es": "Detección de peligros restaurada.",
    },

    # -- Companion phrases (companion/voice/__main__.py) --------------------
    "ready": {
        "en": "Ready.",
        "ko": "준비되었습니다.",
        "zh": "准备就绪。",
        "ja": "準備完了。",
        "es": "Listo.",
    },
    "no_audio": {
        "en": "I did not hear anything.",
        "ko": "아무 소리도 듣지 못했습니다.",
        "zh": "我没有听到任何声音。",
        "ja": "何も聞こえませんでした。",
        "es": "No escuché nada.",
    },
    "nothing_to_repeat": {
        "en": "There is nothing to repeat yet.",
        "ko": "아직 반복할 내용이 없습니다.",
        "zh": "还没有可以重复的内容。",
        "ja": "まだ繰り返す内容がありません。",
        "es": "Aún no hay nada que repetir.",
    },
    "guidance_unavailable": {
        "en": "Backpack guidance is unavailable here.",
        "ko": "배낭 안내를 사용할 수 없습니다.",
        "zh": "背包导航在此不可用。",
        "ja": "バックパックガイドは利用できません。",
        "es": "La guía de mochila no está disponible aquí.",
    },
    "guidance_stopped": {
        "en": "Guidance stopped.",
        "ko": "안내가 중지되었습니다.",
        "zh": "导航已停止。",
        "ja": "案内を停止しました。",
        "es": "Guía detenida.",
    },
    "guardian_not_configured": {
        "en": "Guardian mode isn't set up on this device.",
        "ko": "이 기기에 가디언 모드가 설정되어 있지 않습니다.",
        "zh": "此设备未设置守护模式。",
        "ja": "このデバイスではガーディアンモードが設定されていません。",
        "es": "El modo guardián no está configurado en este dispositivo.",
    },
    "something_wrong": {
        "en": "Something went wrong.",
        "ko": "문제가 발생했습니다.",
        "zh": "出了点问题。",
        "ja": "問題が発生しました。",
        "es": "Algo salió mal.",
    },

    # -- Status phrases (companion/voice/__main__.py status_text()) ---------
    "network_up": {
        "en": "Network reachable.",
        "ko": "네트워크 연결됨.",
        "zh": "网络可达。",
        "ja": "ネットワーク接続あり。",
        "es": "Red disponible.",
    },
    "network_down": {
        "en": "No network.",
        "ko": "네트워크 없음.",
        "zh": "无网络。",
        "ja": "ネットワークなし。",
        "es": "Sin red.",
    },
    "camera_status": {
        "en": "Camera: {status}.",
        "ko": "카메라: {status}.",
        "zh": "相机：{status}。",
        "ja": "カメラ：{status}。",
        "es": "Cámara: {status}.",
    },
    "gemini_configured": {
        "en": "Gemini key configured.",
        "ko": "Gemini 키 설정됨.",
        "zh": "Gemini密钥已配置。",
        "ja": "Geminiキー設定済み。",
        "es": "Clave de Gemini configurada.",
    },
    "gemini_missing": {
        "en": "No Gemini key.",
        "ko": "Gemini 키 없음.",
        "zh": "无Gemini密钥。",
        "ja": "Geminiキーなし。",
        "es": "Sin clave de Gemini.",
    },
    "battery_level": {
        "en": "Battery {percent} percent.",
        "ko": "배터리 {percent} 퍼센트.",
        "zh": "电池{percent}%。",
        "ja": "バッテリー{percent}パーセント。",
        "es": "Batería {percent} por ciento.",
    },
    "battery_unknown": {
        "en": "Battery level unknown.",
        "ko": "배터리 잔량을 알 수 없습니다.",
        "zh": "电池电量未知。",
        "ja": "バッテリー残量不明。",
        "es": "Nivel de batería desconocido.",
    },

    # -- Guardian exit/control phrases (companion/guardian/session.py) -------
    "guardian_cancelled": {
        "en": "Cancelled.",
        "ko": "취소되었습니다.",
        "zh": "已取消。",
        "ja": "キャンセルしました。",
        "es": "Cancelado.",
    },
    "guardian_ended": {
        "en": "Guardian ended.",
        "ko": "가디언 모드가 종료되었습니다.",
        "zh": "守护模式已结束。",
        "ja": "ガーディアンモードを終了しました。",
        "es": "Modo guardián finalizado.",
    },
    "guardian_lost_connection": {
        "en": "I lost the connection to guardian mode.",
        "ko": "가디언 모드 연결이 끊어졌습니다.",
        "zh": "与守护模式的连接已断开。",
        "ja": "ガーディアンモードとの接続が切れました。",
        "es": "Se perdió la conexión con el modo guardián.",
    },
    "guardian_unavailable": {
        "en": "Guardian isn't available right now.",
        "ko": "가디언을 지금 사용할 수 없습니다.",
        "zh": "守护模式目前不可用。",
        "ja": "ガーディアンは現在利用できません。",
        "es": "El modo guardián no está disponible en este momento.",
    },
    "guardian_needs_network": {
        "en": "Guardian needs the network.",
        "ko": "가디언에는 네트워크가 필요합니다.",
        "zh": "守护模式需要网络。",
        "ja": "ガーディアンにはネットワークが必要です。",
        "es": "El modo guardián necesita la red.",
    },
    "guardian_opening": {
        "en": "Opening guardian mode. Tap to cancel.",
        "ko": "가디언 모드를 여는 중입니다. 탭하면 취소됩니다.",
        "zh": "正在打开守护模式。点击取消。",
        "ja": "ガーディアンモードを起動中。タップでキャンセル。",
        "es": "Abriendo modo guardián. Toque para cancelar.",
    },

    # -- SMS preview frame (companion/guardian/sms.py) ----------------------
    "sms_preview": {
        "en": "I'll text {contact}: {text} Hold the button and say yes to send, or no to cancel.",
        "ko": "{contact}에게 문자를 보냅니다: {text} 버튼을 누르고 보내려면 네, 취소하려면 아니오라고 말하세요.",
        "zh": "将发送短信给{contact}：{text} 按住按钮，说是发送，说不取消。",
        "ja": "{contact}にメッセージを送ります：{text} ボタンを押して、送信なら「はい」、キャンセルなら「いいえ」と言ってください。",
        "es": "Enviaré un mensaje a {contact}: {text} Mantenga el botón y diga sí para enviar, o no para cancelar.",
    },
}


def get(phrase_id, lang=None):
    """Return the string for *phrase_id* in *lang*, falling back to English."""
    if lang is None:
        lang = DEFAULT
    table = PHRASES.get(phrase_id)
    if table is None:
        raise KeyError(f"Unknown phrase id: {phrase_id!r}")
    return table.get(lang, table["en"])


def get_hazard_key(severity, band, direction):
    """Map hazard event attributes to a phrase ID in this table.

    Returns the phrase ID string that can be passed to :func:`get`.
    """
    prefix = "stop_" if severity == "urgent" else ""
    if band == "head":
        base = "head_obstacle_ahead"
    else:
        base = "obstacle_ahead"
    if direction == "center":
        suffix = ""
    else:
        suffix = f"_{direction}"
    return f"{prefix}{base}{suffix}"
