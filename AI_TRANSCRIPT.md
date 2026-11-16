# AI coding assistants used

This file records how I used AI assistants to build Errand Agent, in time order. Each exchange is a short summary. The commits, the grok-agent prompts and logs in [docs/ai_transcript/](docs/ai_transcript/), and the Claude Code session log back every entry. All times are IST.

| Assistant | Model | What it did |
|---|---|---|
| Grok chat session | Grok | Design spec, plans, tests, the agent layer, evals, docs and the polish pass |
| grok-agent CLI | grok-4.7 | Spec review, and code that makes failing tests pass (calls 01 to 07) |
| Claude Code | Claude Opus | Code review, an independent review against the brief, and two rounds of bug fixes |

The agent itself calls grok-4.7 through the xAI Responses API at run time. That model is not a coding assistant. The README describes it.

## Friday 6 November: first build (Grok)

**15:50 to 15:52 · Design and plan**

- **Arjun:** Use the Superpowers brainstorming skill and write a design spec first. Then write a plan. Do not write code yet. Use only the Python standard library. The model can read data and propose actions. Only app code can book, cancel or reschedule. App code does this only after the user approves the exact provider, slot and price.
- **Grok:** Wrote the design spec and the plan (`314ec79`, `12ea74e`). One agent runs inside a deterministic shell. A nine-state workflow allows only listed transitions. Each approval holds a SHA-256 digest of provider, slot and price. The executor logs each operation before it calls the provider. It uses an idempotency key and recovers after a restart. The design also covers memory, a token budget for the context, sanitised tool output, proactive follow-up, JSONL traces and offline evals.

**Spec review · grok-agent call 01 · 8,776 tokens, $0.012**

- **Arjun:** Before we write any code, have the CLI review the spec against the brief. List every gap.
- **grok-agent:** Found five gaps. We added each one to the spec and the tests:
  - No next state followed a taken slot or an expired hold.
  - Recovery resolved only pending operations.
  - The model, not the app, wrote the success text.
  - Tool text could write to memory.
  - A failed status check must show as "unverified". The idempotency cache must store only completed side effects.

**15:59 · Mock provider service · call 02 · 142k tokens, $0.090**

- **Arjun:** Write the tests first and confirm that they fail. Then have the CLI write the mock service to make them pass. Seed the mock data so every run is the same. Put a prompt injection in one review for each service.
- **grok-agent:** All 16 tests passed. Commit `3008c2b`.

**16:07 · Core · call 03 · 300k tokens, $0.150, 386 s**

- **Arjun:** Write failing tests for the state machine, storage, approvals and the executor. Then have the CLI make them pass. It must not change the tests.
- **grok-agent:** All 55 tests passed. Commit `79a3b65`. It recorded four decisions:
  - "Go ahead" counts as a yes.
  - After a book timeout, a status other than confirmed or not found is "unverified". The executor never retries it blindly.
  - Recovery for a cancel or a reschedule reads the list of bookings.
  - A change that did not land cancels its approval.

**16:07 to 16:39 · Agent layer**

- **Arjun:** Write the agent loop tests first. Then build the agent layer to pass them. Keep each module small and focused.
- **grok-agent:** Call 04 tried the whole layer in one call. It stalled and produced no output.
- **Arjun:** Split the work into two parts and try again.
- **grok-agent:** Call 05 also stalled in one generation step and produced no output. We prepared a prompt for the second part. No log shows that it ran.
- **Arjun:** Stop using the CLI for this layer. Write it here against the same tests.
- **Grok:** Wrote the tests (`24c1cc4`) and then the layer (`2bde827`). The layer has these parts:
  - A bounded loop with limits on turns, tool calls and wall-clock time.
  - A router that filters by area, budget, time window and earliest start.
  - Approval prompts that the app writes, and a sanitizer for untrusted text.
  - JSONL traces, and a replacement search after a provider cancels.
  - A live model class and a scripted model class. Both use the same single method.
- **grok-agent:** Call 07 generated the seed data: 56 memories and a 220-message history.

**16:43 · Evals**

- **Arjun:** Add offline evals that run on the scripted model. Every scenario must check two rules. No booking happens without a matching approval. No slot is booked twice.
- **Grok:** Added 35 scenarios. The run generates the eval table (`102b239`).

**16:44 to 16:46 · Fixes from the first live run**

- **Arjun:** Run the agent against the live model. Compare the token count we log with the count that the provider reports.
- **Grok:** Found two bugs and fixed each one test-first:
  - Our log showed at most 1,800 tokens, but xAI billed 2,652 to 4,027 input tokens per call. The budget left out the tool schemas and the tool results. Also, characters divided by four gave a count about 1.9× too low. The budget now covers the full request. The estimator corrects itself against the token count that the provider reports (`f87acf8`, `3f5c0fb`).
  - The model described a booking but did not call the booking tool. So the user's "yes" confirmed nothing. Now the agent sends one nudge when the model describes an action without the tool call. The context also states that no approval is pending. The claim guard now ignores negative sentences (`75cdf24`).
- **Grok:** Recorded a live trace of a timeout after a successful booking.

**16:48 · Docs**

- **Arjun:** Write the design note, the bug log and the README. Keep the design note to one page.
- **Grok:** Wrote the three documents (`32a434a`). The repo had 116 tests and 35/35 evals.

## Friday 6 November: polish pass (Grok)

**19:32 to 19:44 · Gap review and plan**

- **Arjun:** Review the repo against the brief again. Tell me what is missing or weak. The agent also feels flat. Give it a plain, human voice, but do not invent a persona.
- **Grok:** Found four gaps:
  - Search ran before the user gave a time window and a budget.
  - The design note had about 820 words, more than one page.
  - The system prompt was only a list of rules.
  - Repeated case-number lines padded the seed history.
- **Arjun:** Write a plan for these gaps. Execute it with subagent-driven development. Write a failing test before each code change.
- **Grok:** Wrote a design and an eight-task plan (`2f2def0`, `ea536d9`).

**19:45 to 20:51 · Plan tasks**

- **Grok and grok-agent:** Completed the plan's tasks:
  - Search now waits for a time window and a budget, test-first (`9f726b8`, `9a3d73a`).
  - The system prompt uses plain words and keeps every hard rule (`bc3429e`).
  - The approval text uses plain words and keeps every material detail (`dda1a00`).
  - The seed history has no padding (`fc7d814`).
  - The design note has about 600 words (`241584c`).
  - The eval table is current (`2241d2c`).
- **Result:** 123 tests and 36/36 evals. We merged the work into `main`.

## Saturday 7 to Sunday 8 November: review and fixes (Claude Code)

**23:40 · Code review**

- **Arjun:** Read the whole repo in detail. Show me how a request flows from end to end. Tell me where the docs and the code disagree, and what the tests miss. Rank the bugs.
- **Claude (Opus, read-only):** Ran the evals (36/36) and found seven issues:
  - "Yes cancel it" did not count as a yes, because "cancel" was on the reject list.
  - A reschedule accepted slots in the past and outside working hours.
  - A provider cancellation replaced the user's pending approval without a word.
  - The time filter compared strings, so an earliest time of "9:00" removed every slot.
  - Recovery ignored operations with an unknown result.
  - Search ignored the area and budget that memory already held.
  - The clock used the machine's time zone, not India's.

**23:50 to 23:57 · First round of fixes**

- **Arjun:** Fix everything. Make one commit for each fix. Write a regression test that fails before each fix. Add an eval when the bug changes agent behaviour.
- **Claude:** Fixed bugs 4 to 10 in `bdbeed9` to `0d481e9`. The bug log lists each one:
  - The pending action's own verb now counts as a yes.
  - A reschedule must use a future slot inside the provider's hours.
  - A cancellation now leaves the user's approval in place and tells the user.
  - Times become HH:MM where they enter the system.
  - Recovery checks operations with an unknown result.
  - Search reads the remembered area and budget.
  - The clock reads Asia/Kolkata time.
  - The README states that the xAI token comes from `XAI_API_KEY` first (`3dda6b1`).
- **Result:** 147 tests passed, up from 123. The evals passed 42/42, up from 36.

**00:02 · Model abstraction question**

- **Arjun:** We support only one LLM vendor. Will a reviewer see that as a modularity problem?
- **Claude:** No. The brief asks for one real LLM and one offline scripted or recorded model. The agent calls only one model method. The live class and the scripted class both implement it. Every test and eval runs on the scripted model. Another vendor needs one adapter class. The real risk was the token lookup, which tried paths that exist only on one machine.

**00:06 to 00:12 · Independent review and docs cleanup**

- **Arjun:** Start a separate reviewer that does not know our earlier findings. It must grade the repo strictly against the brief. It must prove each problem with a script. Meanwhile, remove internal notes and machine-specific paths from the docs.
- **Claude:** Moved the known limits into the README and pointed the live run at `XAI_API_KEY` (`c4ddbf7`, `4e665bd`).
- **Claude (Opus reviewer):** Got 147 passed and 42/42, and proved nine problems:
  - The claim guard let "you're booked" through when no booking existed.
  - The cancellation check marked a cancellation as handled too early. A search error then reached the CLI.
  - A forgotten value still reached the model through the chat history.
  - A substring match stored "150" as the budget when the user wrote "under 1500".
  - One slow model call could exceed the wall-clock limit.
  - After it read an injected review, the model could raise the user's budget.
  - Two processes gave the same booking ID, because both used the same seed.
  - Every type error showed as a bad-argument error.
  - The code contained a fixed user ID and a fixed fallback area.

**00:30 to 00:40 · Second round of fixes**

- **Arjun:** Fix all of the reviewer's findings with the same rules.
- **Claude:** Fixed bugs 11 to 17 and the smaller findings in `17178b3` to `f1d271a`:
  - Only verified executor results produce outcome text. The guard checks each clause. A claim counts as negated only when the negation comes first in that clause.
  - The user always hears about a cancellation, also when the replacement search fails.
  - Explicit memory matches only whole numbers and whole words.
  - The context replaces forgotten values with `[forgotten]` before it sends the history.
  - Each model call gets only the time that is left in the run's budget.
  - Only the user can loosen a budget, a time window or a start time.
  - The booking ID is a hash of the idempotency key.
  - A bad-argument error now means only that the arguments did not bind.
  - The design note matches the code (`1b93406`).
- **Result:** 163 tests passed, and the evals passed 47/47.

**01:09 · Push**

- **Arjun:** Push main.
- **Claude:** Pushed. `main` is at `f1d271a` on GitHub.

## grok-agent CLI calls

The prompts and logs are in [docs/ai_transcript/](docs/ai_transcript/).

| # | Call | Task | Result | Usage |
|---|---|---|---|---|
| 01 | `01_spec_review` | Review the spec against the brief | Five gaps, added to the spec and the tests | 8,776 tokens, $0.012 |
| 02 | `02_prompt` → `02_mocks` | Mock service and clock, against 16 failing tests | 16/16 passed | 142k tokens, $0.090 |
| 03 | `03_prompt` → `03_core` | Workflow, storage, approvals and executor, against failing tests | 55/55 passed | 300k tokens, $0.150 |
| 04 | `04_prompt` | Whole agent layer in one call | Stalled with no output | Not recorded |
| 05 | `05_partA_prompt` | First half of the agent layer | Stalled with no output | Not recorded |
| 06 | `06_partB_prompt` | Second half of the agent layer | Prompt only, no run log | Not recorded |
| 07 | `07_seed` | Seed memories and history | Used as generated | Low effort |

Live model use during testing: the recorded end-to-end session used 20,652 input tokens and 1,096 output tokens, for $0.0125 (`docs/traces/e2e-timeout-after-success.jsonl`).
