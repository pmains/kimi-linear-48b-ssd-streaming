#!/usr/bin/env python3
"""Step 9D production patch (minimal, corruption-proof).

Makes the two reply-directive instruction lines in
buildAssistantOutputDirectivesSection conditional on an actual messaging
delivery surface. A boolean `hasDeliverySurface` is computed at embedded
prompt-prep time (prepareEmbeddedAttemptSystemPrompt) from the attempt's real
delivery context, threaded through the embeddedSystemPrompt bag and the
buildEmbeddedSystemPrompt explicit param list into buildAgentSystemPrompt, then
passed at the section call site. The section emits the two lines unless
hasDeliverySurface === false (headless/no-delivery runs); when the signal is
absent on any other path, current behavior is preserved (lines emitted).
"""
import io

SP = "/opt/homebrew/lib/node_modules/openclaw/dist/system-prompt-params-7goAPY5o.js"
BO = "/opt/homebrew/lib/node_modules/openclaw/dist/builtin-openclaw-nX1NfpmQ.js"

def repl_lines(path, fn):
    with io.open(path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")
    lines = fn(lines)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

# ------------------------------------------------------------- system-prompt-params
def patch_sp(lines):
    # Edit 1: gate the two reply-directive lines in the general branch.
    t1 = '"- Native reply starts with `[[reply_to_current]]`; explicit id only: `[[reply_to:<id>]]`.",'
    t2 = '"- Directives stripped before render; channel config controls delivery.",'
    i1 = i2 = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == t1:
            i1 = i
        if s == t2:
            i2 = i
    assert i1 is not None and i2 is not None, "reply lines not found"
    assert i2 == i1 + 1, "reply lines not adjacent"
    indent = lines[i1][: len(lines[i1]) - len(lines[i1].lstrip())]
    rep = indent + '...(params.hasDeliverySurface === false ? [] : ["- Native reply starts with `[[reply_to_current]]`; explicit id only: `[[reply_to:<id>]]`.", "- Directives stripped before render; channel config controls delivery."]),'
    lines[i1] = rep
    del lines[i2]
    # Edit 2: pass hasDeliverySurface at the section call site.
    found = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith("...buildAssistantOutputDirectivesSection({"):
            for j in range(i + 1, min(i + 9, len(lines))):
                if lines[j].strip() == "runtimeChannel":
                    cindent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
                    lines.insert(j + 1, cindent + "hasDeliverySurface: params.hasDeliverySurface")
                    found = True
                    break
            break
    assert found, "section call site runtimeChannel not found"
    return lines

# ------------------------------------------------------------- builtin-openclaw
def patch_bo(lines):
    # Edit 3: compute hasDeliverySurface after runtimeChannel const.
    done3 = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith("const runtimeChannel = normalizeMessageChannel("):
            cindent = ln[: len(ln) - len(ln.lstrip())]
            newln = cindent + 'const hasDeliverySurface = runtimeChannel != null && runtimeChannel !== "webchat" || attempt.currentMessageId != null || attempt.currentChannelId != null || attempt.currentMessagingTarget != null;'
            lines.insert(i + 1, newln)
            done3 = True
            break
    assert done3, "runtimeChannel const not found"
    # Edit 4: add to the embeddedSystemPrompt bag.
    done4 = False
    for i, ln in enumerate(lines):
        if ln.strip() == "silentReplyPromptMode: attempt.silentReplyPromptMode,":
            cindent = ln[: len(ln) - len(ln.lstrip())]
            lines.insert(i + 1, cindent + "hasDeliverySurface,")
            done4 = True
            break
    assert done4, "bag silentReplyPromptMode not found"
    # Edit 5: forward in buildEmbeddedSystemPrompt explicit list.
    done5 = False
    for i, ln in enumerate(lines):
        if ln.strip() == "activeProjectKeys: params.activeProjectKeys,":
            cindent = ln[: len(ln) - len(ln.lstrip())]
            lines.insert(i + 1, cindent + "hasDeliverySurface: params.hasDeliverySurface,")
            done5 = True
            break
    assert done5, "explicit list activeProjectKeys not found"
    return lines

repl_lines(SP, patch_sp)
repl_lines(BO, patch_bo)
print("patched both files")
