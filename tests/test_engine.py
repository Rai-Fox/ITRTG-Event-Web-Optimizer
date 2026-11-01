"""Event prices, resource accounting and purchase timing."""

import pytest

from itrtg_web.engines.cpp_local_search import cost_entries
from itrtg_web.models import BusyTime


def test_halloween_defaults_and_upgrade_prices_follow_the_event_rules(engine, game_input):
    expected_prices = {
        "Grave Stone": ([200], [500]),
        "Spider Web": ([800], [250]),
        "Cup": ([200], [500]),
        "Moon": ([150, 150], [375, 375]),
        "Candle": ([200], [500]),
        "Broom": ([150, 150], [375, 375]),
        "Free Exp": ([500], [1250]),
        "Skull": ([150, 150], [375, 375]),
        "Pet Stones": ([500], [1250]),
        "Research Points": ([250, 250], [625, 625]),
        "Witch Hat": ([300, 300, 300], [750, 750, 750]),
        "Growth": ([250, 250], [625, 625]),
    }

    labels = engine.metadata().resource_labels
    prices = {
        name: (
            [amount for _, amount in cost_entries(index, game_input.current_levels[index])],
            [amount for _, amount in cost_entries(index + 12, 0)],
        )
        for index, name in enumerate(labels)
    }

    assert labels == list(expected_prices)
    assert game_input.current_levels == [0, 1] + [0] * 23
    assert game_input.resource_counts == [0] * 12
    assert prices == expected_prices
    # Level and Speed prices both keep growing cubically after the first upgrade.
    assert cost_entries(0, 2) == [(1, 5400)]
    assert cost_entries(12, 2) == [(1, 13500)]


def test_purchases_wait_for_resources_then_use_the_new_speed(engine, game_input):
    game_input.duration_seconds = 900

    result = engine.simulate(game_input, [0, 12, 24])
    forecast = engine.timeline(result, 800)

    purchase_times = [purchase.elapsed_seconds for purchase in result.purchases]
    assert purchase_times == pytest.approx([200, 700])
    assert result.final_levels[0] == result.final_levels[12] == 1
    assert result.final_resources[0] == pytest.approx(250)
    assert result.final_resources[1] == pytest.approx(200)
    assert forecast.resources[0] == pytest.approx(625 / 3)
    assert forecast.resources[1] == pytest.approx(100)
    assert forecast.production_rates[0] == pytest.approx(5 / 12)
    assert game_input.current_levels[0] == game_input.current_levels[12] == 0


@pytest.mark.parametrize(
    "upgrade, duration",
    [
        pytest.param(0, 200, id="affordable-only-at-event-end"),
        pytest.param(3, 3600, id="required-resource-has-no-producer"),
    ],
)
def test_unavailable_purchases_do_not_spend_resources(
    engine, game_input, upgrade, duration
):
    game_input.duration_seconds = duration

    result = engine.simulate(game_input, [upgrade, 24])

    assert result.purchases == []
    assert result.final_levels == game_input.current_levels
    assert result.final_resources[1] == pytest.approx(duration)
    assert result.snapshots[-1].elapsed_seconds == duration


def test_forecast_includes_every_instant_purchase(engine, game_input):
    game_input.resource_counts[1] = 1000

    result = engine.simulate(game_input, [0, 2, 24])
    start = engine.timeline(result, 0)
    one_second_later = engine.timeline(result, 1)

    assert [purchase.elapsed_seconds for purchase in result.purchases] == [0, 0]
    assert start.levels[0] == start.levels[2] == 1
    assert start.resources[1] == 600
    assert one_second_later.resources[:3] == pytest.approx([1 / 3, 601, 1 / 3])
    assert game_input.resource_counts[1] == 1000


def test_busy_periods_delay_purchases_but_keep_producing(engine, game_input):
    game_input.duration_seconds = 3 * 3600
    game_input.busy_times = [
        BusyTime(start_hour=1, end_hour=2),
        BusyTime(start_hour=0.5, end_hour=1.5),
        BusyTime(start_hour=0, end_hour=0.5),
    ]

    result = engine.simulate(game_input, [0, 24])

    assert result.purchases[0].elapsed_seconds == 2 * 3600
    assert result.final_resources[0] == pytest.approx(1200)
    assert result.final_resources[1] == pytest.approx(10600)


def test_special_rewards_use_game_visible_units(engine, game_input):
    game_input.duration_seconds = 10
    game_input.dungeon_levels = 5
    game_input.adventure_level = 150
    game_input.unlocked_pets = 50
    game_input.current_levels = [0] * 25
    for resource in (6, 9, 11):
        game_input.current_levels[resource] = 1
    game_input.resource_counts[6] = 10100
    game_input.resource_counts[9] = 200
    game_input.resource_counts[11] = 50

    result = engine.simulate(game_input, [24])

    assert result.snapshots[0].resources == pytest.approx(game_input.resource_counts)
    assert result.snapshots[0].production_rates[6] == pytest.approx(101 / 2500)
    assert result.snapshots[0].production_rates[9] == pytest.approx(2 / 1200)
    assert result.snapshots[0].production_rates[11] == pytest.approx(0.5 / 1800)
    assert result.final_resources[6] == pytest.approx(10100 + 10 * 101 / 2500)
    assert result.final_resources[9] == pytest.approx(200 + 10 * 2 / 1200)
    assert result.final_resources[11] == pytest.approx(50 + 10 * 0.5 / 1800)


def test_score_applies_reward_weights_and_the_hat_soft_cap(engine, game_input):
    game_input.current_levels = [0] * 25
    game_input.dungeon_levels = 5
    game_input.adventure_level = 150
    game_input.unlocked_pets = 50
    game_input.resource_counts[6] = 10100
    game_input.resource_counts[8] = 40
    game_input.resource_counts[9] = 200
    game_input.resource_counts[10] = 11000
    game_input.resource_counts[11] = 50
    game_input.weights = {
        "event_currency": 1,
        "free_exp": 2,
        "pet_stones": 3,
        "research_points": 4,
        "growth": 5,
    }

    result = engine.simulate(game_input, [24])

    # Weights use internal reward units; hats above 10,000 count at 1/200.
    expected_score = (10000 + 5) + 100 * 2 + 40 * 3 + 100 * 4 + 100 * 5
    assert result.score == pytest.approx(expected_score)
