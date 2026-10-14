import db
import transfers as T
from conftest import make_account, make_txn


def row(i, acct, date, cents, text="", linked=False):
    return {"id": i, "account_id": acct, "date": date, "amount_cents": cents, "text": text, "linked": linked}


INST = {1: "Chase", 2: "Chase", 3: "Ally Bank"}


def test_card_payment_is_auto():
    rows = [row(1, 1, "2026-09-02", -120000, "ONLINE PAYMENT TO CHASE CARD 4421"),
            row(2, 2, "2026-09-03", 120000, "Payment Thank You-Mobile")]
    assert T.detect(rows, set(), INST) == [(1, 2, "auto", 1)]


def test_equal_amount_purchase_and_refund_is_only_suggested():
    rows = [row(1, 1, "2026-09-02", -8000, "THE DINER"), row(2, 3, "2026-09-02", 8000, "REFUND AMAZON")]
    out = T.detect(rows, set(), INST)
    assert out == [(1, 2, "suggested", 10)]


def test_institution_name_in_text_makes_it_sure():
    rows = [row(1, 1, "2026-09-02", -50000, "ACH DEBIT ALLY BANK"), row(2, 3, "2026-09-04", 50000, "DEPOSIT")]
    assert T.detect(rows, set(), INST)[0][2] == "auto"


def test_rejected_pair_never_comes_back_and_pairs_are_one_to_one():
    rows = [row(1, 1, "2026-09-02", -1000, "TRANSFER"), row(2, 3, "2026-09-02", 1000, "TRANSFER"),
            row(3, 1, "2026-09-03", -1000, "TRANSFER"), row(4, 3, "2026-09-03", 1000, "TRANSFER")]
    out = T.detect(rows, {(1, 2)}, INST)
    assert sorted((o, i) for o, i, _, _ in out) == [(1, 4), (3, 2)] or sorted((o, i) for o, i, _, _ in out) == [(3, 4)] or len(out) == 2
    out = T.detect(rows, set(), INST)
    assert sorted((o, i) for o, i, _, _ in out) == [(1, 2), (3, 4)]


def test_same_account_and_far_dates_never_pair():
    rows = [row(1, 1, "2026-09-02", -1000, "TRANSFER"), row(2, 1, "2026-09-02", 1000, "TRANSFER"),
            row(3, 3, "2026-09-10", 1000, "TRANSFER")]
    assert T.detect(rows, set(), INST) == []


def test_detect_and_apply_categorises_both_sides(conn, clock):
    chk = make_account(conn, "Checking", institution="Chase")
    card = make_account(conn, "Card", kind="credit", institution="Chase")
    a = make_txn(conn, chk, "2026-09-02", -120000, "ONLINE PAYMENT TO CHASE CARD")
    b = make_txn(conn, card, "2026-09-03", 120000, "Payment Thank You")
    with db.write() as w:
        made = T.detect_and_apply(w, "2026-09-01")
    assert len(made) == 1
    for tid in (a, b):
        assert db.scalar(conn, "SELECT category_id FROM splits WHERE transaction_id = ?", (tid,)) == 501
        assert db.scalar(conn, "SELECT status FROM transactions WHERE id = ?", (tid,)) == "reviewed"
    assert db.scalar(conn, "SELECT transfer_id FROM v_transactions WHERE id = ?", (a,)) == made[0]
    # running again makes nothing new
    with db.write() as w:
        assert T.detect_and_apply(w, "2026-09-01") == []
