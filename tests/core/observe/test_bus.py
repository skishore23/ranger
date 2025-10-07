import pytest
from core.observe.bus import ObservationBus, ReviewCard, Submission


def test_post_review_card():
    bus = ObservationBus()
    card_id = bus.post_review_card(
        title="Test Card",
        description="This is a test card",
        fields=[{"field_name": "test_field", "field_type": "text"}],
        capability_id="capability_1",
        workspace_data={"workspace_key": "workspace_value"}
    )
    card = bus.get_card(card_id)
    assert card is not None
    assert card.title == "Test Card"
    assert card.description == "This is a test card"
    assert card.status == "pending"


def test_submit_response():
    bus = ObservationBus()
    card_id = bus.post_review_card(title="Test Card")
    submission_id = bus.submit_response(card_id, data={"response_key": "response_value"})
    submissions = bus.get_submissions(card_id)
    assert len(submissions) == 1
    assert submissions[0].id == submission_id
    assert submissions[0].data == {"response_key": "response_value"}

    # Check that the card status is updated to submitted
    card = bus.get_card(card_id)
    assert card.status == "submitted"


def test_get_card_not_found():
    bus = ObservationBus()
    with pytest.raises(KeyError):
        bus.submit_response("non_existing_card_id", data={})
