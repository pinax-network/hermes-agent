# Slack Passive Observation Changes

This change makes Hermes observe Slack channel messages silently by default.
When Hermes is invited to a Slack channel, it stores unaddressed channel chatter
as context but does not reply unless it is directly addressed.

## What changed

- Slack channel messages without an explicit Hermes mention are persisted as
  observed context.
- Hermes still replies only when addressed by mention, command, or an active
  thread/session rule.
- Observed messages are stored separately from normal user turns using the
  `observed` flag.
- When Hermes is addressed, recent observed context is injected into the agent
  prompt as context-only information.
- Old observed messages are pruned to keep storage bounded.
- Long observed messages are truncated before being saved.

## Default Slack settings

```yaml
slack:
  require_mention: true
  observe_unaddressed_channel_messages: true
  observed_persist_max_messages: 2000
  observed_message_max_chars: 8000
  observed_context_max_messages: 300
  observed_context_max_chars: 100000
```

## Disable passive observation

```yaml
slack:
  observe_unaddressed_channel_messages: false
```

## Increase retained incident history

Use this for longer incident channels where more passive context should be kept.

```yaml
slack:
  observed_persist_max_messages: 5000
  observed_context_max_messages: 500
  observed_context_max_chars: 150000
```

## Reduce stored data

Use this when privacy or disk usage matters more than long incident context.

```yaml
slack:
  observed_persist_max_messages: 500
  observed_message_max_chars: 4000
  observed_context_max_messages: 100
  observed_context_max_chars: 50000
```

## Expected behavior

1. Invite Hermes to a Slack channel.
2. People discuss normally; Hermes stays silent.
3. Someone mentions Hermes, for example:

```text
@Hermes summarize the incident so far
```

4. Hermes answers using recent observed channel context.
