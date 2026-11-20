"""Flop7 rules engine: pure functions over a JSON-serialisable state dict.

No module-level randomness: every draw is driven by the injected ``rng``.
Every mutating function deep-copies its input, so states are values, not objects.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

# Stable error messages from spec 2.2 (verbatim; translated by the frontend).
GAME_OVER = "game is over"
START_NEXT_ROUND = "start the next round first"
ROUND_RUNNING = "round is still running"
NOT_YOUR_TURN = "not your turn"
CHOOSE_TARGET = "choose a target first"
NOTHING_TO_TARGET = "nothing to target"
NOT_YOUR_DECISION = "not your decision"
INVALID_TARGET = "invalid target"
NO_CARDS_LEFT = "no cards left"
TOO_MANY_AUTOMATIC = "too many automatic moves"
BAD_PLAYER_COUNT = "a game needs 2 to 6 players including the optional bot"

MODIFIERS = ("+2", "+4", "+6", "+8", "+10", "x2")
ACTIONS = ("freeze", "flip3", "second")
FLOP7_BONUS = 15
FLOP7_NUMBERS = 7
WIN_TOTAL = 200
EVENT_LIMIT = 12
MAX_AUTOMATIC = 1000

Card = int | str
State = dict[str, Any]


class IllegalMove(ValueError):
    """A rule violation; ``str(error)`` is the stable English message."""


def build_deck(rng: Any) -> list[Card]:
    """Build and shuffle the deck.

    # tech-debt: spec 2.1 enumerates 1x0, 1x1, 2x2 ... 12x12 (=79 numbers) but
    labels that list "78 cards" and the whole deck "87". The explicit card list
    is treated as normative (94 total). If the totals are corrected upstream,
    only the NUMBER_COUNTS rule below needs to change.
    """
    deck: list[Card] = []
    for value in range(13):
        count = max(1, value)
        deck.extend([value] * count)
    deck.extend(MODIFIERS)
    for action in ACTIONS:
        deck.extend([action] * 3)
    rng.shuffle(deck)
    return deck


def new_game(names: Sequence[str], *, bot: bool = False, rng: Any) -> State:
    """Create a fresh state; the optional bot is appended as the last seat."""
    seats = list(names) + (["Bot"] if bot else [])
    if not 2 <= len(seats) <= 6:
        raise IllegalMove(BAD_PLAYER_COUNT)
    players = [
        {
            "name": name,
            "bot": bot and index == len(names),
            "total": 0,
            "numbers": [],
            "mods": [],
            "second": False,
            "status": "active",
            "round_score": 0,
            "round_flop7": False,
        }
        for index, name in enumerate(seats)
    ]
    return {
        "players": players,
        "deck": build_deck(rng),
        "discard": [],
        "round": 1,
        "turn": 0,
        "round_over": False,
        "winner": None,
        "pending": [],
        "last_round": None,
        "events": [],
        "next_event_id": 1,
    }


def _emit(state: State, event_type: str, **data: Any) -> None:
    state["events"].append({"id": state["next_event_id"], "type": event_type, **data})
    state["next_event_id"] += 1
    if len(state["events"]) > EVENT_LIMIT:
        del state["events"][:-EVENT_LIMIT]


def round_score(player: State) -> int:
    """numbers (doubled by x2) first, then +N modifiers, then the flop7 bonus."""
    score = sum(player["numbers"])
    if "x2" in player["mods"]:
        score *= 2
    for modifier in player["mods"]:
        if modifier.startswith("+"):
            score += int(modifier[1:])
    if player["round_flop7"]:
        score += FLOP7_BONUS
    return score


def _current_score(state: State, seat: int) -> int:
    player = state["players"][seat]
    if player["status"] != "active":
        return player["round_score"]
    return round_score(player)


def _draw_card(state: State, rng: Any) -> Card:
    if not state["deck"]:
        if not state["discard"]:
            raise IllegalMove(NO_CARDS_LEFT)
        state["deck"] = state["discard"]
        state["discard"] = []
        rng.shuffle(state["deck"])
        _emit(state, "reshuffle")
    return state["deck"].pop()


def _is_number(card: Card) -> bool:
    return isinstance(card, int)


def _bank(state: State, seat: int) -> None:
    player = state["players"][seat]
    player["round_score"] = round_score(player)
    player["status"] = "stayed"


def _finish_round(state: State) -> None:
    """Add every round score to its total, keep the summary, decide the winner."""
    for seat, player in enumerate(state["players"]):
        if player["status"] == "active":
            _bank(state, seat)
        if player["status"] == "bust":
            player["round_score"] = 0
        player["total"] += player["round_score"]
    state["last_round"] = [
        {
            "seat": seat,
            "points": player["round_score"],
            "status": player["status"],
            "flop7": bool(player["round_flop7"]),
        }
        for seat, player in enumerate(state["players"])
    ]
    state["round_over"] = True
    state["pending"] = []
    _emit(state, "round", round=state["round"], results=copy.deepcopy(state["last_round"]))

    totals = [player["total"] for player in state["players"]]
    best = max(totals)
    if best >= WIN_TOTAL:
        leaders = [seat for seat, total in enumerate(totals) if total == best]
        if len(leaders) == 1:
            state["winner"] = leaders[0]
            _emit(state, "win", seat=leaders[0])
        else:
            _emit(state, "tie", seats=leaders)


def _ensure_round_state(state: State) -> None:
    if not state["round_over"] and not any(p["status"] == "active" for p in state["players"]):
        _finish_round(state)


def _advance_turn(state: State, from_seat: int) -> None:
    count = len(state["players"])
    for step in range(1, count + 1):
        seat = (from_seat + step) % count
        if state["players"][seat]["status"] == "active":
            state["turn"] = seat
            return
    _finish_round(state)


def _apply_number(state: State, seat: int, card: Card) -> bool:
    """Apply a number card; return True when a Second Chance ended the turn.

    Official FAQ case 3: a neutralised duplicate ends your turn (you stay in the
    round). The caller decides whether that ends the current action — a Flip Three
    must still finish its three cards.
    """
    player = state["players"][seat]
    if card in player["numbers"]:
        if player["second"]:
            player["second"] = False
            state["discard"].append(card)
            _emit(state, "second", seat=seat, used=True)
            return True
        player["status"] = "bust"
        player["round_score"] = 0
        state["discard"].append(card)
        _emit(state, "bust", seat=seat, card=card)
        return False
    player["numbers"].append(card)
    if len(player["numbers"]) >= FLOP7_NUMBERS and not player["round_flop7"]:
        player["round_flop7"] = True
        _bank(state, seat)
        _emit(state, "flop7", seat=seat, points=player["round_score"])
        _finish_round(state)
    return False


def _apply_card(state: State, seat: int, card: Card, source: str) -> bool:
    """Apply one drawn card (hit, deal or Flip Three); return 'Second Chance used'."""
    if _is_number(card):
        return _apply_number(state, seat, card)
    if card in MODIFIERS:
        state["players"][seat]["mods"].append(card)
    else:
        _create_pending(state, seat, card, source)
    return False


def _valid_targets(state: State, pending: dict[str, Any]) -> list[int]:
    # Official rules: an Action card may target ANY active player, including the
    # drawer. A spare Second Chance is the exception: you can never hold two.
    targets = [seat for seat, player in enumerate(state["players"]) if player["status"] == "active"]
    if pending["kind"] == "give":
        targets = [seat for seat in targets if not state["players"][seat]["second"]]
    return targets


def _create_pending(state: State, by: int, card: Card, source: str) -> None:
    """Resolve an action card drawn by ``by`` into an immediate effect or pending."""
    state["discard"].append(card)
    if card == "second" and not state["players"][by]["second"]:
        state["players"][by]["second"] = True
        _emit(state, "second", seat=by)
        return
    kind = "give" if card == "second" else card
    state["pending"].append({"kind": kind, "by": by, "target": None, "source": source})


def _apply_pending(state: State, pending: dict[str, Any], rng: Any) -> None:
    kind = pending["kind"]
    by = pending["by"]
    target = pending["target"]
    if kind == "freeze":
        _bank(state, target)
        _emit(state, "freezes", by=by, target=target)
        if target == state["turn"]:
            _advance_turn(state, target)
    elif kind == "flip3":
        _emit(state, "flip3", by=by, target=target)
        deferred: list[Card] = []
        for _ in range(3):
            player = state["players"][target]
            if player["status"] != "active" or state["round_over"]:
                break
            card = _draw_card(state, rng)
            _emit(state, "draws", seat=target, card=card)
            if _is_number(card) or card in MODIFIERS:
                # A Second Chance used during Flip Three does not stop the
                # remaining cards: it is not the target's own "hit or stay".
                _apply_card(state, target, card, "flip3")
            else:
                deferred.append(card)
        if state["players"][target]["status"] == "active" and not state["round_over"]:
            for card in deferred:
                _create_pending(state, target, card, "flip3")
        if state["players"][target]["status"] != "active" and target == state["turn"]:
            _advance_turn(state, target)
    elif kind == "give":
        state["players"][target]["second"] = True
        _emit(state, "gives", by=by, target=target)
    _ensure_round_state(state)


def resolve_pending(state: State, rng: Any, *, max_steps: int = MAX_AUTOMATIC) -> State:
    """Auto-resolve pending decisions in order; stop at the first human choice."""
    new = copy.deepcopy(state)
    for _ in range(max_steps):
        if not new["pending"]:
            return new
        if new["round_over"] or new["winner"] is not None:
            new["pending"] = []
            return new
        pending = new["pending"][0]
        if new["players"][pending["by"]]["status"] != "active":
            new["pending"].pop(0)
            continue
        targets = _valid_targets(new, pending)
        if not targets:
            # No active target at all (or nobody without a Second Chance): discard.
            new["pending"].pop(0)
            continue
        if len(targets) == 1:
            new["pending"].pop(0)
            _apply_pending(new, {**pending, "target": targets[0]}, rng)
            continue
        return new
    if new["pending"]:
        raise IllegalMove(TOO_MANY_AUTOMATIC)
    return new


def _check_playable(state: State) -> None:
    if state["winner"] is not None:
        raise IllegalMove(GAME_OVER)
    if state["round_over"]:
        raise IllegalMove(START_NEXT_ROUND)


def hit(state: State, seat: int, rng: Any) -> State:
    """Draw one card for the current player and apply all resulting rules."""
    _check_playable(state)
    new = copy.deepcopy(state)
    if new["turn"] != seat or new["players"][seat]["status"] != "active":
        raise IllegalMove(NOT_YOUR_TURN)
    if new["pending"]:
        raise IllegalMove(CHOOSE_TARGET)
    card = _draw_card(new, rng)
    _emit(new, "draws", seat=seat, card=card)
    second_used = _apply_card(new, seat, card, "hit")
    if (
        (second_used or new["players"][seat]["status"] != "active")
        and seat == new["turn"]
        and not new["round_over"]
    ):
        _advance_turn(new, seat)
    new = resolve_pending(new, rng)
    _ensure_round_state(new)
    return new


def stay(state: State, seat: int) -> State:
    """Bank the current player's round score and pass the turn."""
    _check_playable(state)
    new = copy.deepcopy(state)
    if new["turn"] != seat or new["players"][seat]["status"] != "active":
        raise IllegalMove(NOT_YOUR_TURN)
    if new["pending"]:
        raise IllegalMove(CHOOSE_TARGET)
    _bank(new, seat)
    _emit(new, "stays", seat=seat)
    _advance_turn(new, seat)
    _ensure_round_state(new)
    return new


def target(state: State, seat: int, target_seat: int, rng: Any) -> State:
    """Resolve the oldest pending decision that ``seat`` must decide."""
    _check_playable(state)
    new = copy.deepcopy(state)
    if not new["pending"]:
        raise IllegalMove(NOTHING_TO_TARGET)
    pending = new["pending"][0]
    if pending["by"] != seat:
        raise IllegalMove(NOT_YOUR_DECISION)
    if target_seat not in _valid_targets(new, pending):
        raise IllegalMove(INVALID_TARGET)
    new["pending"].pop(0)
    _apply_pending(new, {**pending, "target": target_seat}, rng)
    new = resolve_pending(new, rng)
    if "deal_index" in new and not new["pending"]:
        new = start_round(new, rng, resume=True)  # resume the paused opening deal
    return new


def next_round(state: State) -> State:
    """Start the next round; keep the previous round's summary visible."""
    if state["winner"] is not None:
        raise IllegalMove(GAME_OVER)
    if not state["round_over"]:
        raise IllegalMove(ROUND_RUNNING)
    new = copy.deepcopy(state)
    for player in new["players"]:
        new["discard"].extend(player["numbers"])
        new["discard"].extend(player["mods"])
        player["numbers"] = []
        player["mods"] = []
        player["second"] = False
        player["status"] = "active"
        player["round_score"] = 0
        player["round_flop7"] = False
    new["round"] += 1
    new["turn"] = (new["round"] - 1) % len(new["players"])
    new["round_over"] = False
    new["pending"] = []
    _emit(new, "next", round=new["round"])
    return new


def start_round(state: State, rng: Any, *, resume: bool = False) -> State:
    """Deal one starting card to every active player (official opening deal).

    An Action card pauses the deal until it is resolved. If a human must choose a
    target, ``deal_index`` records where to continue when ``target`` is called.
    """
    new = copy.deepcopy(state)
    count = len(new["players"])
    order = [(new["turn"] + step) % count for step in range(count)]
    start = new.get("deal_index", 0) if resume else 0
    for index in range(start, count):
        seat = order[index]
        if new["round_over"] or new["winner"] is not None:
            new.pop("deal_index", None)
            return new
        if new["players"][seat]["status"] != "active":
            continue
        if new["pending"]:
            new["deal_index"] = index
            return new
        card = _draw_card(new, rng)
        _emit(new, "draws", seat=seat, card=card)
        _apply_card(new, seat, card, "deal")
        new = resolve_pending(new, rng)
    if new["pending"]:
        new["deal_index"] = count  # only the pending choice remains
        return new
    new.pop("deal_index", None)
    _ensure_round_state(new)
    return new


def bot_seat(state: State) -> int | None:
    return next((seat for seat, player in enumerate(state["players"]) if player["bot"]), None)


def bot_action(state: State) -> dict[str, Any]:
    """The deterministic bot strategy (no hidden deck access)."""
    seat = bot_seat(state)
    if seat is None or state["winner"] is not None or state["round_over"]:
        return {"action": "wait"}
    if state["pending"]:
        pending = state["pending"][0]
        if pending["by"] != seat:
            return {"action": "wait"}
        targets = _valid_targets(state, pending)
        if seat in targets and len(targets) > 1:
            targets = [candidate for candidate in targets if candidate != seat]
        if not targets:
            return {"action": "wait"}
        strongest = max(targets, key=lambda target: (_current_score(state, target), -target))
        return {"action": "target", "target": strongest}
    if state["turn"] != seat:
        return {"action": "wait"}
    player = state["players"][seat]
    if not player["second"] and (round_score(player) >= 18 or len(player["numbers"]) >= 4):
        return {"action": "stay"}
    return {"action": "hit"}


def bot_move(state: State, rng: Any) -> State:
    """Apply the bot's decision through the same public rules as any seat."""
    seat = bot_seat(state)
    decision = bot_action(state)
    if seat is None:
        return copy.deepcopy(state)
    if decision["action"] == "hit":
        return hit(state, seat, rng)
    if decision["action"] == "stay":
        return stay(state, seat)
    if decision["action"] == "target":
        return target(state, seat, decision["target"], rng)
    return copy.deepcopy(state)


def public_view(state: State) -> State:
    """Full state minus the hidden deck, with derived scores and targets."""
    view = copy.deepcopy(state)
    view.pop("deck", None)
    view.pop("deal_index", None)  # internal deal-resume cursor
    view["deck_count"] = len(state["deck"])
    for seat, player in enumerate(view["players"]):
        player["score"] = _current_score(state, seat)
    view["scores"] = [player["score"] for player in view["players"]]
    view["targets"] = _valid_targets(state, view["pending"][0]) if view["pending"] else []
    return view
