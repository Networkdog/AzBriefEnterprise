"""English (en) language style guide.

Included in: Report phase when report_language == "en".
"""

# Injected into the subscriber translation prompt (not the report prompt), where
# the full style guide is too long to repeat.
TRANSLATION_NOTES = """- Use the active voice and concise technical prose. No hedging.
- Lead with the capability that changed, not with the announcement itself.
- Keep Azure service, resource and SKU names exactly as Azure spells them."""

ENGLISH_STYLE_GUIDE = """### English (en) — Style Guide
English reports must read like a concise technical advisory written by an Azure Solution Architect.

#### 1. Voice & Tone
- **Impersonal**: Do not use first person ("I found...", "We recommend..."). Write as an authoritative reference: "This update requires...", "Affected resources include...".
- **Confident, not hedging**: State facts directly. Minimize qualifiers.
  - BAD: "It may potentially be the case that some resources could be impacted."
  - GOOD: "This update impacts 3 Storage Accounts using TLS 1.0."
  - OK: "may impact" (when genuinely uncertain) — but never "may potentially impact".
- **Neutral register**: Professional but not stiff. Avoid both casual language ("pretty important") and bureaucratic language ("it is hereby recommended that").

#### 2. Sentence Structure
- **Short sentences**: Target under 25 words. Split complex ideas across sentences.
  - BAD: "This update, which was announced on October 1, 2024, requires all Storage Accounts that are currently configured with TLS 1.0 to be upgraded to TLS 1.2 before the retirement date."
  - GOOD: "This update retires TLS 1.0 support on October 31, 2024. All Storage Accounts on TLS 1.0 must upgrade to TLS 1.2 before that date."
- **Subject-verb proximity**: Keep the subject and its verb close together. Avoid long parenthetical insertions.
  - BAD: "The Storage Accounts, which were provisioned in 2019 using the classic deployment model and have since been migrated to ARM but still retain their original TLS settings, need upgrading."
  - GOOD: "These Storage Accounts still use their original TLS settings from 2019 and need upgrading."
- **Subject-complement agreement (category match)**: In an "X is Y" sentence, X and Y must be the same kind of thing. A common defect is equating an *announcement* with a *capability*, or a *reason* with a *cause clause*.
  - BAD: "This update is a feature that improves isolation." (an update is not a feature)
  - GOOD: "This feature improves isolation." / "This update adds a feature that improves isolation."
  - BAD: "The key point of this release is because it separates the trust boundary." ("the point is because" is malformed)
  - GOOD: "The key point of this release is that it separates the trust boundary."
  - BAD: "The reason to migrate is because performance improves." (reason ... is because = redundant)
  - GOOD: "Migrate because performance improves." / "Migration improves performance."
- **Eliminate "there is/are"**: Use a concrete subject instead.
  - BAD: "There are 3 Storage Accounts that use TLS 1.0."
  - GOOD: "3 Storage Accounts use TLS 1.0."
- **Lead with the capability, not the announcement**: The report describes what changed, not the announcement that reported it. Naming the announcement as the subject ("This announcement is about...") or as a cause ("With this GA, you can now...") repeats "update" on both sides of the verb. Start from the time ("Now...", "From <date>...") or from the thing that changed, and demote the release stage to a prepositional phrase.
  - BAD: "This update is a public preview that adds regex-based masking to Dynamic Data Masking in Azure SQL Database."
  - GOOD: "Regex-based masking for DDM (Dynamic Data Masking) in Azure SQL Database is now in public preview."
  - BAD: "This announcement is about the Nested confidential (cc_v5) VM series being retired on 2026-09-01."
  - GOOD: "The Nested confidential (cc_v5) VM series retires on 2026-09-01."
  - BAD: "With this GA, Azure Firewall explicit proxy is now officially usable." (the source says public preview)
  - GOOD: "Azure Firewall explicit proxy is now available in public preview."
  - Keep the release stage exactly as the source states it. Do not paraphrase it ("officially available", "fully released") or promote a preview to GA.
  - Naming the update as the cause of an *effect on the environment* is fine ("This update blocks TLS 1.0 connections"). The defect is attributing an availability change to the announcement.
- **Name what a new capability replaces (new features, new services, previews)**: tell the reader how an administrator reached the same outcome before it existed — a self-operated component, a manual procedure, a third-party product, or an accepted limitation — and what takes its place now. Blend the contrast into the explanation and vary its wording; the same "Previously you had to X; now you can Y" sentence in every report reads as a template. If the prior method is not documented, name the limitation the capability removes rather than inventing one.
  - GOOD: "Routing traffic previously ran through a self-managed VM forwarding tier. The appliance now handles it inside the VNet."
  - BAD: "Before this feature, this was difficult. Now it is easy." (no concrete prior method)
- **Active voice over passive**: Default to active. Use passive only when the actor is irrelevant or unknown.
  - BAD: "TLS 1.0 connections will be blocked by this update."
  - GOOD: "This update blocks TLS 1.0 connections."
- **Parallel structure**: Items in a list or series must share the same grammatical form.
  - BAD: "Verify TLS version, upgrading the SDK, and client compatibility testing."
  - GOOD: "Verify TLS version, upgrade the SDK, and test client compatibility."

#### 3. Word Choice
- **Strong verbs**: Replace weak verb + noun combinations with a single precise verb.
  - "perform an upgrade" → "upgrade"
  - "carry out verification" → "verify"
  - "conduct an assessment" → "assess"
  - "make a determination" → "determine"
  - "provide support for" → "support"
- **No intensifiers without substance**: Remove "very", "really", "extremely", "quite", "significantly" unless quantified.
  - BAD: "This significantly impacts performance."
  - GOOD: "Latency increases by ~40ms per request."
- **Precise technical verbs**: Use the most specific verb available.
  - "deploy" (not "put out"), "provision" (not "set up"), "configure" (not "set up"),
    "migrate" (not "move over"), "deprecate" (not "phase out"), "enforce" (not "put in place")
- **Oxford comma**: Always use. "TLS 1.0, 1.1, and 1.2" — not "TLS 1.0, 1.1 and 1.2".
- **No weasel words**: "some", "various", "a number of" → use specific counts from data.
  - BAD: "Various resources may be affected."
  - GOOD: "18 of 22 Storage Accounts are affected."

#### 4. Technical Precision
- **Tense**: Present tense for current state ("3 accounts use TLS 1.0"). Future tense for predictions or deadlines ("Support ends October 31, 2024"). Past tense only for historical events.
- **Numbers**: Use digits for all technical quantities. "3 resources", not "three resources". Use commas for thousands: "1,024 VMs".
- **Dates**: ISO 8601 preferred in technical context (2024-10-31). Full format also acceptable (October 31, 2024). Be consistent within a report.
- **Sentence opening variety**: Avoid starting consecutive sentences with the same word or pattern. Vary sentence structure to maintain reader engagement.
  - BAD: "This update affects... This update requires... This update also..."
  - GOOD: "This update affects... Affected resources must... The retirement deadline is..."

#### 5. Concept Boxes (`>` blockquotes)
Concept boxes are inline glossary notes that explain a non-obvious term to a mixed-seniority audience.
- **Placement**: Put the box immediately after the paragraph where the term *first* appears — never grouped at the end of the report, and never before the term is used.
- **Shape**: `> **Term**: one or two sentences.` Start with the bold term and a colon; end with a period.
  - GOOD: "> **mTLS**: Mutual TLS, where both client and server authenticate each other. Stronger than one-way TLS for service-to-service traffic."
- **Add the "why it matters" angle**: A box should say what the term is *and* why it matters here, not just a dictionary definition.
  - GOOD: "> **Ultra Disk**: Azure's highest-performance managed disk. Suited to latency-sensitive databases needing high IOPS."
- **Calibrate depth to the term**: Explain genuinely non-obvious terms (protocols, niche features, pricing units) in full; for ubiquitous infra terms (managed identity, private endpoint, availability zone) keep it to one crisp line — a senior reader should not feel lectured.
- **Do NOT explain** terms every Azure admin knows: resource group, subscription, region, tag, portal, ARM.
- **Link out when a doc exists (only when available)**: If the doc-search tools or the update's links provide an authoritative page for the term (prefer Microsoft Learn), end the box with a compact `([Microsoft Learn](URL))` markdown link so the reader can dive deeper. Add it ONLY when a real URL is on hand — never fabricate one; omit the link otherwise.
  - GOOD: "> **mTLS**: Mutual TLS, where both client and server authenticate each other. Stronger than one-way TLS for service-to-service traffic. ([Microsoft Learn](https://learn.microsoft.com/azure/...))"
- **Length**: 1-2 sentences. If it runs to 3+, move the detail into the body paragraph instead.
"""
