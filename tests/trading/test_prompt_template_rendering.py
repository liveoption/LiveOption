from trading.prompts.intraday import (
    INTRADAY_USER_TEMPLATE,
    build_intraday_user_prompt,
)
from trading.prompts.loader import PromptLoader, get_loader


def test_json_examples_do_not_block_runtime_placeholders():
    template = get_loader().load_system("executor")
    rendered = PromptLoader.render(template, MAX_COST="5,000", MAX_CONTRACTS="10")
    assert "Maximum budget for this trade: $5,000" in rendered
    assert '"action": "sell"' in rendered


def test_custom_intraday_template_receives_market_data():
    rendered = build_intraday_user_prompt(
        current_time="09:45", minutes_to_close=375, portfolio={"cash": 1000, "equity": 1000},
        price_windows=[], chain_sample=[], template="At {current_time}: {chain_sample_table}",
    )
    assert rendered.startswith("At 09:45:")
    assert "{chain_sample_table}" not in rendered
    assert INTRADAY_USER_TEMPLATE != rendered
