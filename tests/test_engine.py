"""Test-first specification of the pure Flop7 rules engine (spec section 2)."""

from __future__ import annotations

import copy

import pytest

from app import engine


class FixedRng:
    """Deterministic rng: keeps shuffle order (optionally reverses it)."""

    def __init__(self, *, reverse: bool = False) -> None:
        self.reverse = reverse

    def shuffle(self, seq: list) -> None:
        if self.reverse:
            seq.reverse()


RNG = FixedRng()
BAD_PLAYERS = "a game needs 2 to 6 players including the optional bot"


def make_state(names=("A", "B"), *, bot: bool = False, deck=()):
    """Build a game and override the deck. `deck` is given in draw order."""
    state = engine.new_game(list(names), bot=bot, rng=FixedRng())
    if deck:
        state["deck"] = list(reversed(deck))
        state["discard"] = []
    return state


def assert_error(fn, message: str) -> None:
    with pytest.raises(engine.IllegalMove) as exc:
        fn()
    assert str(exc.value) == message


def end_round(state):
    """Force a round end without playing it out (for turn/round tests)."""
    state = copy.deepcopy(state)
    for player in state["players"]:
        if player["status"] == "active":
            player["round_score"] = engine.round_score(player)
            player["status"] = "stayed"
    state["round_over"] = True
    return state


# --- 2.1 Deck ----------------------------------------------------------------


def test_deck_composition_explicit_enumeration():
    deck = engine.build_deck(FixedRng())
    numbers = [card for card in deck if isinstance(card, int)]
    expected = {0: 1, 1: 1, **{value: value for value in range(2, 13)}}
    for value, count in expected.items():
        assert numbers.count(value) == count, value
    assert len(numbers) == 79
    for mod in ("+2", "+4", "+6", "+8", "+10", "x2"):
        assert deck.count(mod) == 1, mod
    for action in ("freeze", "flip3", "second"):
        assert deck.count(action) == 3, action
    assert len(deck) == 94


def test_deck_reshuffles_discard_when_empty():
    state = make_state()
    state["deck"] = []
    state["discard"] = [9, 8, 7]
    new = engine.hit(state, 0, RNG)
    assert new["players"][0]["numbers"] == [7]
    assert any(event["type"] == "reshuffle" for event in new["events"])
    assert new["discard"] == []
    assert len(new["deck"]) == 2


def test_no_cards_left():
    state = make_state()
    state["deck"] = []
    state["discard"] = []
    assert_error(lambda: engine.hit(state, 0, RNG), "no cards left")


# --- 2.2 Turn flow -----------------------------------------------------------


def test_duplicate_number_busts_and_passes_turn():
    state = make_state(deck=[5, 5])
    first = engine.hit(state, 0, RNG)
    assert first["players"][0]["numbers"] == [5]
    second = engine.hit(first, 0, RNG)
    assert second["players"][0]["status"] == "bust"
    assert second["players"][0]["round_score"] == 0
    assert 5 in second["discard"]
    assert second["turn"] == 1
    assert any(event["type"] == "bust" for event in second["events"])


def test_second_consumed_exactly_once_then_discarded():
    state = make_state(deck=[5, 5, 5])
    state["players"][0]["second"] = True
    held = engine.hit(state, 0, RNG)
    assert held["players"][0]["numbers"] == [5]
    protected = engine.hit(held, 0, RNG)
    assert protected["players"][0]["second"] is False
    assert protected["players"][0]["status"] == "active"
    assert 5 in protected["discard"]
    assert sum(e["type"] == "second" and e.get("used") for e in protected["events"]) == 1
    # Official FAQ case 3: the turn passes, but the player stays in the round.
    assert protected["turn"] == 1
    back = engine.stay(protected, 1)
    busted = engine.hit(back, 0, RNG)
    assert busted["players"][0]["status"] == "bust"


def test_second_granted_on_draw():
    state = make_state(deck=["second"])
    new = engine.hit(state, 0, RNG)
    assert new["players"][0]["second"] is True
    assert sum(e["type"] == "second" for e in new["events"]) == 1


def test_modifier_order_numbers_doubled_then_bonus():
    state = make_state()
    player = state["players"][0]
    player["numbers"] = [5, 6]
    player["mods"] = ["x2", "+2"]
    assert engine.round_score(player) == 24
    player["mods"] = ["+2", "x2"]
    assert engine.round_score(player) == 24
    assert engine.public_view(state)["players"][0]["score"] == 24


def test_flop7_bonus_ends_round_for_everyone():
    state = make_state(deck=[7])
    state["players"][0]["numbers"] = [0, 1, 2, 3, 4, 5]
    new = engine.hit(state, 0, RNG)
    assert new["players"][0]["round_flop7"] is True
    assert new["players"][0]["round_score"] == 37  # (15+7)*1 + 15
    assert new["players"][0]["total"] == 37
    assert new["players"][1]["status"] == "stayed"
    assert new["round_over"] is True
    assert new["last_round"][0]["flop7"] is True
    assert any(event["type"] == "flop7" for event in new["events"])


def test_stay_banks_and_advances_the_turn():
    state = make_state(deck=[4])
    first = engine.hit(state, 0, RNG)
    second = engine.stay(first, 0)
    assert second["players"][0]["status"] == "stayed"
    assert second["players"][0]["round_score"] == 4
    assert second["turn"] == 1
    assert any(event["type"] == "stays" for event in second["events"])


def test_round_summary_survives_hand_reset_and_start_seat_rotates():
    state = make_state(names=("A", "B", "C"), deck=[4])
    first = engine.hit(state, 0, RNG)
    first = engine.stay(first, 0)
    first = engine.stay(first, 1)
    first = engine.stay(first, 2)
    assert first["round_over"] is True
    assert len(first["last_round"]) == 3
    summary = copy.deepcopy(first["last_round"])
    second = engine.next_round(first)
    assert second["round"] == 2
    assert second["turn"] == 1
    assert all(p["numbers"] == [] and p["mods"] == [] and p["second"] is False for p in second["players"])
    assert second["last_round"] == summary
    third = engine.next_round(end_round(second))
    assert third["round"] == 3
    assert third["turn"] == 2


def test_win_at_200_sole_leader():
    state = make_state(deck=[10])
    state["players"][0]["total"] = 195
    first = engine.hit(state, 0, RNG)
    first = engine.stay(first, 0)
    done = engine.stay(first, 1)
    assert done["winner"] == 0
    assert done["players"][0]["total"] == 205
    assert any(event["type"] == "win" for event in done["events"])


def test_tie_at_200_plays_another_round():
    state = make_state(deck=[5, 5])
    state["players"][0]["total"] = 195
    state["players"][1]["total"] = 195
    first = engine.hit(state, 0, RNG)
    first = engine.stay(first, 0)
    first = engine.hit(first, 1, RNG)
    done = engine.stay(first, 1)
    assert done["winner"] is None
    assert done["round_over"] is True
    assert any(event["type"] == "tie" for event in done["events"])
    assert engine.next_round(done)["round"] == 2


# --- 2.3 Action cards --------------------------------------------------------


def test_freeze_self_when_only_active_player():
    state = make_state(deck=["freeze"])
    state["players"][1]["status"] = "stayed"
    state["players"][1]["round_score"] = 4
    new = engine.hit(state, 0, RNG)
    assert new["players"][0]["status"] == "stayed"
    assert new["round_over"] is True
    assert any(e["type"] == "freezes" and e["target"] == 0 for e in new["events"])


def test_freeze_opponent_via_target():
    state = make_state(names=("A", "B", "C"), deck=["freeze"])
    state["players"][0]["numbers"] = [8]
    pending = engine.hit(state, 0, RNG)
    assert pending["pending"] and pending["pending"][0]["kind"] == "freeze"
    resolved = engine.target(pending, 0, 2, RNG)
    assert resolved["players"][2]["status"] == "stayed"
    assert resolved["players"][2]["round_score"] == 0
    assert resolved["pending"] == []
    assert any(e["type"] == "freezes" and e["target"] == 2 for e in resolved["events"])


def test_flip3_on_opponent_busts_midway():
    state = make_state(deck=["flip3", 5, 5])
    pending = engine.hit(state, 0, RNG)
    new = engine.target(pending, 0, 1, RNG)
    assert new["players"][1]["status"] == "bust"
    assert new["players"][1]["numbers"] == [5]
    assert any(event["type"] == "flip3" for event in new["events"])


def test_flip3_action_cards_queued_and_played_by_target():
    state = make_state(deck=["flip3", "second", 3, 9])
    pending = engine.hit(state, 0, RNG)
    new = engine.target(pending, 0, 1, RNG)
    assert new["players"][1]["numbers"] == [3, 9]
    assert new["players"][1]["second"] is True
    assert new["pending"] == []


def test_flip3_queued_action_lost_on_bust():
    state = make_state(deck=["flip3", "second", 3, 3, 11])
    pending = engine.hit(state, 0, RNG)
    new = engine.target(pending, 0, 1, RNG)
    assert new["players"][1]["status"] == "bust"
    assert new["players"][1]["second"] is False


def test_flip3_queued_action_lost_on_round_end():
    state = make_state(deck=["flip3", "second", 7])
    state["players"][1]["numbers"] = [0, 1, 2, 3, 4, 5]
    pending = engine.hit(state, 0, RNG)
    new = engine.target(pending, 0, 1, RNG)
    assert new["players"][1]["round_flop7"] is True
    assert new["round_over"] is True
    assert new["players"][1]["second"] is False
    assert new["pending"] == []


def test_flip3_self_when_only_active_player():
    state = make_state(deck=["flip3", 2, 4, 6])
    state["players"][1]["status"] = "stayed"
    new = engine.hit(state, 0, RNG)
    assert new["players"][0]["numbers"] == [2, 4, 6]
    assert any(e["type"] == "flip3" and e.get("target") == 0 for e in new["events"])


def test_hit_and_stay_are_blocked_while_a_decision_is_pending():
    state = make_state(names=("A", "B", "C"), deck=["freeze"])
    pending = engine.hit(state, 0, RNG)
    assert_error(lambda: engine.hit(pending, 0, RNG), "choose a target first")
    assert_error(lambda: engine.stay(pending, 0), "choose a target first")


def test_second_given_away_with_one_target_auto_resolves():
    state = make_state(deck=["second"])
    state["players"][0]["second"] = True
    new = engine.hit(state, 0, RNG)
    assert new["players"][0]["second"] is True
    assert new["players"][1]["second"] is True
    assert new["pending"] == []
    assert sum(e["type"] == "gives" for e in new["events"]) == 1


def test_second_given_away_with_no_target_is_discarded():
    state = make_state(deck=["second"])
    state["players"][0]["second"] = True
    state["players"][1]["second"] = True
    new = engine.hit(state, 0, RNG)
    assert new["pending"] == []
    assert new["players"][0]["second"] is True
    assert new["players"][1]["second"] is True
    assert not any(e["type"] == "gives" for e in new["events"])


def test_second_given_away_with_two_targets_needs_choice():
    state = make_state(names=("A", "B", "C"), deck=["second"])
    state["players"][0]["second"] = True
    pending = engine.hit(state, 0, RNG)
    assert pending["pending"] and pending["pending"][0]["kind"] == "give"
    resolved = engine.target(pending, 0, 2, RNG)
    assert resolved["players"][2]["second"] is True
    assert resolved["players"][1]["second"] is False
    assert resolved["pending"] == []


def test_pending_of_inactive_owner_is_discarded():
    state = make_state(names=("A", "B", "C"))
    state["pending"] = [{"kind": "freeze", "by": 0, "target": None, "source": "hit"}]
    state["players"][0]["status"] = "stayed"
    assert engine.resolve_pending(state, RNG)["pending"] == []


def test_automatic_move_guard():
    state = make_state()
    state["pending"] = [{"kind": "freeze", "by": 0, "target": None, "source": "hit"}]
    assert_error(lambda: engine.resolve_pending(state, RNG, max_steps=0), "too many automatic moves")


# --- Errors (verbatim strings from 2.2) --------------------------------------


def test_error_strings_are_verbatim():
    expected = {
        "GAME_OVER": "game is over",
        "START_NEXT_ROUND": "start the next round first",
        "ROUND_RUNNING": "round is still running",
        "NOT_YOUR_TURN": "not your turn",
        "CHOOSE_TARGET": "choose a target first",
        "NOTHING_TO_TARGET": "nothing to target",
        "NOT_YOUR_DECISION": "not your decision",
        "INVALID_TARGET": "invalid target",
        "NO_CARDS_LEFT": "no cards left",
        "TOO_MANY_AUTOMATIC": "too many automatic moves",
        "BAD_PLAYER_COUNT": BAD_PLAYERS,
    }
    for name, text in expected.items():
        assert getattr(engine, name) == text


def test_wrong_turn_and_round_errors():
    state = make_state(deck=[5])
    assert_error(lambda: engine.hit(state, 1, RNG), "not your turn")
    assert_error(lambda: engine.next_round(state), "round is still running")
    assert_error(lambda: engine.target(state, 0, 1, RNG), "nothing to target")
    over = copy.deepcopy(state)
    over["round_over"] = True
    assert_error(lambda: engine.hit(over, 0, RNG), "start the next round first")
    won = copy.deepcopy(state)
    won["winner"] = 0
    assert_error(lambda: engine.hit(won, 0, RNG), "game is over")
    assert_error(lambda: engine.next_round(won), "game is over")


def test_target_errors():
    state = make_state(names=("A", "B", "C"), deck=["freeze"])
    pending = engine.hit(state, 0, RNG)
    assert_error(lambda: engine.target(pending, 1, 2, RNG), "not your decision")
    assert_error(lambda: engine.target(pending, 0, 99, RNG), "invalid target")


def test_new_game_player_count_validation():
    assert_error(lambda: engine.new_game(["A"], rng=RNG), BAD_PLAYERS)
    assert_error(lambda: engine.new_game([f"P{i}" for i in range(7)], rng=RNG), BAD_PLAYERS)
    solo = engine.new_game(["A"], bot=True, rng=RNG)
    assert len(solo["players"]) == 2
    assert solo["players"][1]["name"] == "Bot"
    assert solo["players"][1]["bot"] is True


# --- 2.4 Bot -----------------------------------------------------------------


def bot_state(numbers, *, second: bool = False):
    state = make_state(names=("A",), bot=True)
    state["turn"] = 1
    state["players"][1]["numbers"] = list(numbers)
    state["players"][1]["second"] = second
    return state


def test_bot_banks_at_18_points_or_four_numbers_unless_protected():
    assert engine.bot_action(bot_state([12, 6]))["action"] == "stay"
    assert engine.bot_action(bot_state([1, 2, 3, 4]))["action"] == "stay"
    assert engine.bot_action(bot_state([1, 2, 3]))["action"] == "hit"
    assert engine.bot_action(bot_state([12, 6], second=True))["action"] == "hit"
    assert engine.bot_action(bot_state([1, 2, 3, 4], second=True))["action"] == "hit"


def test_bot_targets_strongest_opponent():
    state = make_state(names=("A", "B"), bot=True)
    state["players"][0]["numbers"] = [2, 3]
    state["players"][1]["numbers"] = [12]
    state["pending"] = [{"kind": "freeze", "by": 2, "target": None, "source": "hit"}]
    assert engine.bot_action(state) == {"action": "target", "target": 1}


def test_bot_waits_at_round_break_and_when_game_over():
    state = make_state(names=("A", "Bot"), bot=True)
    state["round_over"] = True
    assert engine.bot_action(state)["action"] == "wait"
    state["round_over"] = False
    state["winner"] = 1
    assert engine.bot_action(state)["action"] == "wait"


def test_bot_move_applies_the_decision():
    state = bot_state([1, 2, 3])
    state["deck"] = [12]
    state["discard"] = []
    new = engine.bot_move(state, RNG)
    assert new["players"][1]["numbers"] == [1, 2, 3, new["players"][1]["numbers"][-1]]
    banked = engine.bot_move(bot_state([12, 6]), RNG)
    assert banked["players"][1]["status"] == "stayed"


# --- 2.5 / 4.3 Public view & events ------------------------------------------


def test_public_view_hides_deck_and_derives_fields():
    state = make_state(deck=[5])
    view = engine.public_view(state)
    assert "deck" not in view
    assert view["deck_count"] == len(state["deck"])
    assert view["players"][0]["score"] == 0
    assert view["scores"] == [0, 0]
    assert view["targets"] == []
    view["players"][0]["name"] = "changed"
    assert state["players"][0]["name"] == "A"


def test_events_have_unique_monotonic_ids_and_are_capped_at_twelve():
    state = make_state(deck=["+2"] * 15)
    new = state
    for _ in range(15):
        new = engine.hit(new, 0, RNG)
    ids = [event["id"] for event in new["events"]]
    assert len(ids) == 12
    assert len(set(ids)) == 12
    assert ids == sorted(ids)
    assert ids[-1] == 15


# --- Purity ------------------------------------------------------------------


def test_functions_do_not_mutate_their_input():
    state = make_state(deck=[5, "freeze"])
    before = copy.deepcopy(state)
    engine.hit(state, 0, RNG)
    assert state == before


# --- Official-rule alignment (opening deal, Second Chance, self-targeting) -----


def test_opening_deal_gives_every_active_player_one_card():
    state = make_state(names=("A", "B"), deck=[3, 7])
    dealt = engine.start_round(state, RNG)
    assert [p["numbers"] for p in dealt["players"]] == [[3], [7]]
    assert dealt["deck"] == [] and "deal_index" not in dealt


def test_opening_deal_pauses_for_a_choice_and_resumes_on_target():
    state = make_state(names=("A", "B", "C"), deck=["freeze", 4, 5, 6])
    paused = engine.start_round(state, RNG)
    assert paused["pending"][0]["kind"] == "freeze" and paused["deal_index"] == 1
    resumed = engine.target(paused, 0, 1, RNG)
    assert resumed["players"][1]["status"] == "stayed"  # frozen before their card
    assert resumed["players"][2]["numbers"] == [4]  # the remaining player still gets one
    assert "deal_index" not in resumed and len(resumed["deck"]) == 2


def test_self_targeting_is_allowed_with_other_active_players():
    freeze = engine.hit(make_state(deck=["freeze"]), 0, RNG)
    assert 0 in engine.public_view(freeze)["targets"]
    frozen = engine.target(freeze, 0, 0, RNG)
    assert frozen["players"][0]["status"] == "stayed"

    flip = engine.hit(make_state(deck=["flip3", 2, 4, 8]), 0, RNG)
    flipped = engine.target(flip, 0, 0, RNG)
    assert flipped["players"][0]["numbers"] == [2, 4, 8]
    assert any(e["type"] == "flip3" and e.get("target") == 0 for e in flipped["events"])


def test_second_chance_keeps_flip3_going():
    state = make_state(deck=["flip3", 5, 5, 9])
    state["players"][1]["second"] = True
    pending = engine.hit(state, 0, RNG)
    new = engine.target(pending, 0, 1, RNG)
    assert new["players"][1]["numbers"] == [5, 9]  # flip continues after the save
    assert new["players"][1]["second"] is False
    assert new["turn"] == 0  # Flip Three never passes the turn
