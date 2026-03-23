# Duckln Release Gate

A release cannot ship unless every item below is true.

## Spec Gate
- [ ] requirements updated
- [ ] plan updated
- [ ] tasks updated
- [ ] analysis updated
- [ ] no orphan requirement/plan/task

## Product Gate
- [ ] onboarding works from clean machine state
- [ ] all three modes behave as specified
- [ ] pixel banner / fallback renders correctly
- [ ] provider selection works for OpenRouter, OpenAI, Anthropic

## Safety Gate
- [ ] blocked commands are enforced
- [ ] HOOTLWO whitelist is enforced
- [ ] no unsafe auto-run path exists
- [ ] destructive actions require explicit approval

## Privacy Gate
- [ ] secrets redacted before provider calls
- [ ] payload review is available where required
- [ ] logs contain no raw secrets

## Quality Gate
- [ ] unit tests pass
- [ ] integration tests pass
- [ ] manual scenario tests completed
- [ ] release notes prepared
- [ ] rollback notes prepared
