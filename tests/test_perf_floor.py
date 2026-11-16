"""Each account's drawdown floor (SPEC §6.7): the five hand cases of docs/design/step4_final.md."""
from nqbot.performance.floor import Rule, floor_path

RAPID = Rule(25000, 1000, "eod_trailing")
LUCID = Rule(25000, 1000, "eod_trailing", 25100, "floor_or_first_payout")
PRO = Rule(50000, 2000, "eod_trailing", 50100, "first_payout")


def test_static_breaches_at_the_floor():
    fp = floor_path(Rule(25000, 1000, "static"), [24500, 24000.01, 23999])
    assert fp.floor == [24000] * 3 and fp.breach_at == 2


def test_trailing():
    fp = floor_path(RAPID, [26000, 26500, 25600, 25400])
    assert fp.floor[1] == 25500 and fp.breach_at == 3 and fp.locked_at is None


def test_lucid_locks_by_the_floor():
    fp = floor_path(LUCID, [25800, 26300, 26800, 25100])
    assert fp.floor[1] == 25100 and fp.locked_at == 1 and fp.floor[2] == 25100   # stays locked as the high rises
    assert fp.breach_at == 3                                                    # at or below the floor


def test_lucid_payout_first():
    fp = floor_path(LUCID, [25500, 25600, 25700], payout_at=1)
    assert fp.floor == [24500, 25100, 25100] and fp.locked_at == 1


def test_pro_first_payout():
    fp = floor_path(PRO, [53000, 53500, 53800], payout_at=2)
    assert fp.floor[0] == 51000 and fp.floor[1] == 51500 and fp.floor[2] == 50100   # the lock, though trailing is higher


def test_rule_from_an_account_and_its_words():
    acc = {"start_balance_usd": 25000, "drawdown_usd": 1000, "drawdown_type": "eod_trailing", "drawdown_lock_usd": 25100,
           "drawdown_lock_trigger": "floor"}
    assert Rule.of(acc) == Rule(25000, 1000, "eod_trailing", 25100, "floor")
    assert Rule.of(acc).text() == "$1,000 EOD-trailing, locked at $25,100 once it gets there"
    assert RAPID.text() == "$1,000 EOD-trailing, no lock"
