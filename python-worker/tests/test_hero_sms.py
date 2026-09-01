from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


RUNTIME_SRC = Path(__file__).parents[1] / "gopay_runtime" / "app" / "src"
if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))

from opai.core import hero_helpers


def test_hero_get_number_accepts_list_response(monkeypatch) -> None:
    monkeypatch.setenv("OPAI_HERO_SMS_SERVICE", "dr")
    monkeypatch.setenv("OPAI_HERO_SMS_COUNTRY", "6")
    monkeypatch.setenv("OPAI_HERO_SMS_MAX_PRICE", "")
    response = [{"id": "activation-42", "phoneNumber": "6281215735328"}]

    with patch.object(hero_helpers, "_request", return_value=response) as request:
        phone, activation_id = hero_helpers.hero_get_number()

    assert (phone, activation_id) == ("+6281215735328", "activation-42")
    assert request.call_args.args == ("POST", "/activations")
    assert request.call_args.kwargs["json"] == {
        "service": "dr",
        "country": 6,
        "amount": 1,
        "duration": 24,
        "verificationType": "sms",
    }


def test_hero_get_number_accepts_list_in_data_wrapper(monkeypatch) -> None:
    monkeypatch.setenv("OPAI_HERO_SMS_MAX_PRICE", "")
    response = {"data": [{"activationId": 43, "phone": "+6282228019294"}]}

    with patch.object(hero_helpers, "_request", return_value=response):
        phone, activation_id = hero_helpers.hero_get_number()

    assert (phone, activation_id) == ("+6282228019294", "43")
