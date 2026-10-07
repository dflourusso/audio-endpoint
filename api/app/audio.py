import re

OUTPUT_NOT_SUPPORTED = "output_not_supported"
BLUETOOTH_OUTPUT_ID = "bluetooth"

_CARD_RE = re.compile(r"^\s*(\d+)\s+\[([^\]]+)\]\s*:\s*(.+)$")


def classify_card(card_id: str, description: str) -> str:
    text = f"{card_id} {description}".lower()
    if "hdmi" in text:
        return "hdmi"
    if "usb" in text:
        return "usb"
    if any(word in text for word in ("analog", "headphone", "headset", "alc", "codec")):
        return "analog"
    return "other"


def parse_asound_cards(text: str) -> list[dict]:
    outputs = []
    for line in text.splitlines():
        match = _CARD_RE.match(line)
        if not match:
            continue
        index, card_id, description = match.groups()
        kind = classify_card(card_id.strip(), description.strip())
        outputs.append(
            {
                "id": f"alsa:{index}",
                "name": card_id.strip(),
                "description": description.strip(),
                "kind": kind,
                "selectable": False,
                "active": False,
                "reason": OUTPUT_NOT_SUPPORTED,
            }
        )
    return outputs


def list_outputs(cards_text: str | None) -> list[dict]:
    bluetooth = {
        "id": BLUETOOTH_OUTPUT_ID,
        "name": "Bluetooth",
        "description": "Caixas pareadas pelo Sendspin Bluetooth Bridge",
        "kind": "bluetooth",
        "selectable": True,
        "active": True,
        "reason": None,
    }
    detected = parse_asound_cards(cards_text or "")
    return [bluetooth, *detected]


def select_output(output_id: str) -> dict:
    if output_id == BLUETOOTH_OUTPUT_ID:
        return {"id": BLUETOOTH_OUTPUT_ID, "active": True}
    raise OutputNotSupported()


class OutputNotSupported(Exception):
    code = OUTPUT_NOT_SUPPORTED
    message = "Nesta versão a reprodução é só por Bluetooth. USB, P2 e HDMI aparecem na lista, mas ainda não podem ser selecionados."
