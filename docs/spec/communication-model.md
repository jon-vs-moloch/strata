# Strata Communication Model

Strata should treat communication as a first-class system capability, not an ad hoc side effect of whichever subsystem happens to want to write a chat message.

This document defines the shared contract.

## Why This Exists

Strata can now speak through multiple kinds of surfaces:

- direct replies inside an existing chat
- autonomous/system-originated updates
- feedback notices
- task-progress notices
- later, cross-lane or cross-agent communication

If those paths all write directly to storage, the system loses:

- routing discipline
- provenance
- consistent visual semantics
- the ability to choose whether something should be said at all
- the ability to choose where it should go

The communication layer exists to make those choices explicit.

## Provenance Rule

Every communication event should be able to answer:

- what was emitted
- who or what emitted it
- why it was emitted
- under what authority it was emitted
- which upstream user input, spec clause, task, audit, or policy decision it derived from

Communication provenance should include both causal ancestry and authority ancestry, not just source labels.

## Core Rule

All non-user-authored messages should go through the communication decision and delivery layer.

User-authored messages are still stored directly as user actions. System-authored messages, notifications, replies, and recommendations should not bypass the communication layer.

## Object Model

The umbrella category is `communication`.

A `response` is one communicative act within that broader category.

Current communicative acts include:

- `response`
- `notification`
- `recommendation`
- future acts such as `question`, `handoff`, or `reflection`

The important design point is:

- not every communication is a response
- replies, autonomous notices, and recommendations should still share one routing substrate

## Communication Decision

The current implementation centers on a communication decision object with fields such as:

- `should_send`
- `role`
- `content`
- `lane`
- `channel`
- `session_id`
- `audience`
- `source_kind`
- `source_actor`
- `opened_reason`
- `tags`
- `disclosability`
- `topic_summary`
- `session_title`
- `communicative_act`
- `response_kind`
- `urgency`
- `allow_user_opened_reuse`

This object answers three questions:

1. should Strata communicate at all
2. if so, where should the message go
3. what kind of communicative act is it

## Routing Model

Routing happens in three layers:

1. build the communication decision
2. route the decision to a session/channel
3. deliver the routed message and persist metadata

Current routing principles:

- `response` defaults to the current session
- an explicit `new_session` response is allowed when the caller requests it
- autonomous/system-originated communication may either reuse an appropriate session or open a new one
- user-opened sessions are reusable when they are the best topical fit
- callers may explicitly forbid reuse of user-opened sessions with `allow_user_opened_reuse=False`

The system should prefer good topical fit over rigid origin rules.

## Session Metadata As Routing Substrate

Session metadata is not just display polish. It is part of routing and attention management.

Important metadata/state fields include:

- title fields: `custom_title`, `generated_title`, `recommended_title`
- provenance: `opened_by`, `opened_reason`, `source_kind`
- authority/provenance chain: `authority_kind`, `authority_ref`, `derived_from`, `governing_spec_refs`
- topical hints: `tags`, `topic_summary`
- lifecycle: `created_at`, `last_audited_at`
- attention state: `last_read_at`, `last_read_message_id`, `unread_count`
- communication trace: `last_communicative_act`, `last_response_kind`, `last_communication_urgency`, `last_communication_source_kind`, `last_communication_actor`, `last_communication_tags`

The communication router should use these fields to decide whether an update belongs in:

- the current session
- an existing related system thread
- a fresh session

## Message Lifecycle Metadata

Session metadata is not enough by itself.

Strata also needs message-level lifecycle metadata so it can tell the difference between:

- a message being authored
- a message being delivered into a surface
- a message being seen by the system
- a message being read by the intended recipient

This matters because Strata will not always reply. A user message can still be successfully ingested and understood even when the correct behavior is silence.

Current message-level metadata should support fields such as:

- `sent_at`
- `delivered_at`
- `delivery_channel`
- `audience`
- `source_kind`
- `source_actor`
- `communicative_act`
- `response_kind`
- `urgency`
- `seen_by_system_at`
- `seen_by_system_actor`
- `read_at`
- `read_by`
- `tags`
- `authority_kind`
- `authority_ref`
- `derived_from`
- `governing_spec_refs`
- `event_action`
- `event_target`

Design intent:

- user-authored messages should be markable as `seen_by_system` even if no reply is emitted
- system-authored messages should be markable as `read` by the user or another recipient later
- downstream policy should be able to reason about whether a message was merely sent, actually observed, or ignored
- downstream audit should be able to reason about whether a message or log mutation was authorized, attributable, and later corrected

## Append-Only Meaning

Communication and history should be append-only in meaning even when storage is compacted or edited.

This means:

- opening a thread or log is an event
- closing it is an event
- editing a message or log entry is an event
- redacting or compacting history is an event
- replaying or restoring prior history is an event

The system should prefer emitting a new provenance-bearing event over silently mutating prior history.

## Emitter Guidelines

When a subsystem emits communication, it should provide enough metadata for routing and later reflection.

At minimum, system emitters should set:

- `source_kind`
- `source_actor`
- `communicative_act`
- `tags`
- `topic_summary` when there is a stable topic
- `urgency` when the attention level is known

Examples:

- chat reply: `communicative_act=response`, `source_kind=chat_reply`
- task progress: `communicative_act=notification`, `source_kind=task_research_complete`
- feedback notice: `communicative_act=notification`, `source_kind=feedback_event`, urgency derived from prioritization
- autonomous alignment thread: `communicative_act=notification`, `source_kind=autonomous_alignment`, with tags and a stable topic/session title

## UI Implications

The UI should develop a stable visual language around communication provenance.

Different kinds of communication should be distinguishable at a glance, especially:

- user-authored
- direct response
- autonomous/system-originated
- operator/admin-facing
- recommendation/reflection output

This should not rely on color alone, but provenance styling should be consistent across:

- chat messages
- session badges
- task cards
- suggestion affordances
- notifications

## Long-Term Direction

The communication layer should eventually become the shared substrate for:

- user chat replies
- autonomous notices
- recommendations
- cross-lane communication
- attention routing
- future shell-specific notification delivery

That keeps communication policy explicit, inspectable, and evolvable in the same way Strata already treats task routing and model routing.
