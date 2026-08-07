"""Tests for backend/schemas/notification.py -- WIRING_GAP_LEDGER W2.

NotificationRule.trigger_event used to be an unconstrained str field.
dispatch_notifications() only ever queries/fires rules with
trigger_event == "on_new_record" (backend/pipeline/notifier_dispatch.py) --
there is no producer for any other value, so a rule saved with anything else
became permanently, silently inert. Constrained to Literal["on_new_record"]
at the schema layer (Create/Update, the write paths) so the API rejects an
unsupported value loudly instead of persisting a dead rule.
"""

import pytest
from pydantic import ValidationError

from backend.schemas.notification import NotificationRuleCreate, NotificationRuleUpdate


class TestNotificationRuleCreateTriggerEvent:
    def test_on_new_record_accepted(self):
        rule = NotificationRuleCreate(
            name="r1", trigger_event="on_new_record", notifier_type="webhook"
        )
        assert rule.trigger_event == "on_new_record"

    def test_on_ai_processed_accepted(self):
        """on_ai_processed now has a dispatch producer (pipeline step 5 when
        AI enrichment ran) — accepted at the schema layer."""
        rule = NotificationRuleCreate(
            name="r1", trigger_event="on_ai_processed", notifier_type="webhook"
        )
        assert rule.trigger_event == "on_ai_processed"

    def test_on_task_failed_accepted(self):
        """on_task_failed now has a dispatch producer (pipeline failure paths
        with a synthetic failure payload) — accepted at the schema layer."""
        rule = NotificationRuleCreate(
            name="r1", trigger_event="on_task_failed", notifier_type="webhook"
        )
        assert rule.trigger_event == "on_task_failed"

    def test_arbitrary_string_rejected(self):
        with pytest.raises(ValidationError):
            NotificationRuleCreate(
                name="r1", trigger_event="on_something_else", notifier_type="webhook"
            )

    def test_missing_trigger_event_still_required(self):
        """trigger_event stays required on create -- only the type narrowed
        from str to Literal, not the field's optionality."""
        with pytest.raises(ValidationError):
            NotificationRuleCreate(name="r1", notifier_type="webhook")


class TestNotificationRuleUpdateTriggerEvent:
    def test_on_new_record_accepted(self):
        update = NotificationRuleUpdate(trigger_event="on_new_record")
        assert update.trigger_event == "on_new_record"

    def test_on_task_failed_accepted(self):
        update = NotificationRuleUpdate(trigger_event="on_task_failed")
        assert update.trigger_event == "on_task_failed"

    def test_unsupported_value_rejected(self):
        with pytest.raises(ValidationError):
            NotificationRuleUpdate(trigger_event="on_whatever")

    def test_omitted_trigger_event_stays_none(self):
        """Update is a partial patch -- omitting trigger_event entirely must
        keep working (only a non-None value is constrained to the Literal)."""
        update = NotificationRuleUpdate(name="renamed")
        assert update.trigger_event is None
