# Etch Sales Pipeline: EU AI Act Compliance Prospects

**Purpose:** Identify and segment prospect companies for Etch's content provenance solution, focused on EU AI Act compliance obligations.

**Last updated:** 2026-03-17

---

## EU AI Act Compliance Requirements (Etch-Relevant)

### Article 50 — Transparency Obligations for AI-Generated Content

Article 50 of the EU AI Act (Regulation 2024/1689) imposes specific obligations on providers and deployers of AI systems that generate or manipulate content. These are the requirements most directly addressed by Etch:

1. **Providers of AI systems that generate synthetic audio, image, video, or text** must ensure outputs are marked in a machine-readable format that is detectable as artificially generated or manipulated. This marking must be:
   - Robust (surviving reasonable modifications)
   - Interoperable
   - Effective and reliable

2. **Deployers of AI systems that generate deepfakes** (AI-generated/manipulated content depicting real people or events appearing authentic) must disclose that the content has been artificially generated or manipulated.

3. **Deployers using AI systems to generate or manipulate text published to inform the public on matters of public interest** must disclose the AI-generated nature of the content, unless the content has undergone human editorial review and a natural person holds editorial responsibility.

4. **Providers of general-purpose AI (GPAI) models** (e.g., foundation model providers) must implement state-of-the-art watermarking or equivalent techniques for generated content.

### Key Compliance Deadlines

| Milestone | Date |
|---|---|
| AI Act entered into force | August 1, 2024 |
| Prohibited practices apply | February 2, 2025 |
| GPAI & governance rules apply | August 2, 2025 |
| **Article 50 transparency obligations apply** | **August 2, 2026** |
| Full enforcement (all high-risk) | August 2, 2027 |

**The Article 50 deadline (August 2, 2026) is ~4.5 months away. This creates immediate urgency for prospects.**

### What Companies Need (and What Etch Solves)

| Requirement | What Article 50 Mandates | How Etch Addresses It |
|---|---|---|
| Content provenance | Prove when and by whom AI content was generated | Tamper-evident timestamping on Merkle chain; each registration creates a cryptographic proof receipt |
| Machine-readable marking | AI outputs must carry detectable provenance metadata | Etch proof receipts (leaf_hash, mmr_root, timestamp) can be embedded as machine-readable provenance |
| Tamper resistance | Markings must be robust against modification | Merkle chain integrity: modifying any historical entry invalidates all subsequent entries |
| Verifiability | Third parties must be able to verify provenance claims | Offline-verifiable inclusion proofs (no server dependency for verification) |
| Privacy preservation | Provenance without exposing proprietary content or user data | Only SHA-256 hashes stored; raw content never touches Etch servers |
| Audit trail | Demonstrable compliance record for regulators | Immutable, append-only chain with full history; DB-backed record keeping |

---

## Prospect Segments

### Segment 1: Foundation Model / GPAI Providers

**Why they need Etch:** Article 50(2) requires GPAI providers to implement watermarking or equivalent provenance for all generated content. These companies generate billions of outputs daily and need a scalable, privacy-preserving provenance layer.

**Compliance pressure:** HIGH. GPAI rules apply from August 2025; full Article 50 from August 2026.

| Company | HQ | What They Do | Etch Fit |
|---|---|---|---|
| **OpenAI** | US (EU operations) | GPT models, DALL-E, Sora | Hash-based provenance for text/image/video outputs without storing user prompts |
| **Anthropic** | US (EU operations) | Claude models | Provenance timestamping for model outputs across API consumers |
| **Google DeepMind** | UK/US | Gemini models, Imagen, Veo | Complements SynthID watermarking with tamper-evident chain of custody |
| **Meta AI** | US (EU operations) | Llama models, Emu image gen | Open-weight models need downstream provenance; Etch provides it at deploy time |
| **Mistral AI** | France | Mistral/Mixtral models | EU-headquartered, first in line for enforcement; needs provenance solution |
| **Aleph Alpha** | Germany | Luminous models | German company, direct EU jurisdiction, high compliance urgency |
| **Stability AI** | UK | Stable Diffusion, Stable Audio | Open models widely used for image generation; provenance gap today |
| **Cohere** | Canada (EU operations) | Enterprise LLMs | Enterprise focus means compliance-conscious customers demanding provenance |

**Sales angle:** "Your model generates the content, but Article 50 says you must prove its provenance. Etch gives you a privacy-preserving provenance layer that scales to your output volume, without storing any user content."

---

### Segment 2: Synthetic Media & AI-Generated Content Platforms

**Why they need Etch:** These companies produce realistic AI-generated images, video, and audio. Article 50 specifically targets deepfake-capable systems. They must label and prove provenance of every generated asset.

**Compliance pressure:** VERY HIGH. Synthetic media is the primary target of Article 50 disclosure obligations.

| Company | HQ | What They Do | Etch Fit |
|---|---|---|---|
| **ElevenLabs** | US/Poland | AI voice synthesis, dubbing | Every generated voice clip needs provenance proof; Etch timestamps without storing audio |
| **Synthesia** | UK | AI-generated video avatars | Video outputs depicting real-looking humans = deepfake rules apply directly |
| **HeyGen** | US | AI video avatars, translation | Same deepfake exposure as Synthesia; needs proof of origin per clip |
| **Runway** | US | Gen-3 video generation | Cutting-edge video gen; needs machine-readable provenance on every output |
| **Pika** | US | AI video generation | Fast-growing video gen startup; compliance obligation on every generated video |
| **D-ID** | Israel | AI-generated talking head videos | Digital human videos trigger deepfake disclosure requirements |
| **Murf AI** | US/India | AI voiceover platform | Audio generation at scale requires provenance trail |
| **Respeecher** | Ukraine | Voice cloning for entertainment | High-profile voice cloning; direct deepfake regulation applicability |
| **Luma AI** | US | Dream Machine video gen | 3D/video generation needs provenance infrastructure |
| **Midjourney** | US | AI image generation | Massive image generation volume; no current provenance infrastructure |

**Sales angle:** "Regulators will ask: can you prove when each piece of synthetic media was generated and by whom? Etch creates a tamper-proof receipt for every output, verifiable by any third party, without storing the content itself."

---

### Segment 3: AI-Powered Content & Publishing Platforms

**Why they need Etch:** Article 50(4) requires disclosure when AI generates or manipulates text published on matters of public interest. Platforms publishing AI-assisted content need provenance records demonstrating what was AI-generated vs. human-edited.

**Compliance pressure:** HIGH for any platform publishing AI content at scale.

| Company | HQ | What They Do | Etch Fit |
|---|---|---|---|
| **Jasper** | US | AI marketing/content platform | Enterprise content at scale; customers need compliance proof for generated copy |
| **Writer** | US | Enterprise AI writing platform | Enterprise customers will demand provenance records as part of compliance |
| **Copy.ai** | US | AI copywriting | Marketing content generated by AI needs provenance for EU-facing campaigns |
| **Notion AI** | US | AI-enhanced workspace | AI-generated text in public-facing docs triggers disclosure obligations |
| **Grammarly** | US/Ukraine | AI writing assistant | AI-rewritten/generated content at massive scale across enterprise |
| **Adobe (Firefly)** | US | AI image/video in Creative Cloud | Already has Content Credentials; Etch offers independent tamper-evident layer |
| **Canva (Magic Studio)** | Australia | AI design tools | AI-generated designs for marketing; EU enterprise customers need compliance |
| **Shutterstock** | US | AI image generation marketplace | Stock media platform with AI gen; needs provenance per generated asset |
| **Getty Images** | US | AI image generation (partnership w/ NVIDIA) | Already compliance-focused; Etch adds independent verification layer |

**Sales angle:** "Your platform helps users create content. Under Article 50, that AI-generated content needs provenance. Embed Etch to give every piece of generated content a verifiable proof of origin that satisfies regulators."

---

### Segment 4: Social Media & UGC Platforms (Deployers)

**Why they need Etch:** As deployers of AI features and hosts of AI-generated content, these platforms must detect and label AI-generated content. They need infrastructure to track provenance of content flowing through their systems.

**Compliance pressure:** HIGH. Massive volume of AI-generated content; regulatory spotlight.

| Company | HQ | What They Do | Etch Fit |
|---|---|---|---|
| **Meta Platforms** | US | Facebook, Instagram, Threads, WhatsApp | Labels AI content already; needs tamper-evident provenance backend |
| **TikTok (ByteDance)** | China/Singapore | Short video platform with AI effects | AI filters/effects generate synthetic content at enormous scale |
| **Snap Inc.** | US | Snapchat with AI lenses/My AI | AI lenses create synthetic content; My AI generates text |
| **X (Twitter)** | US | Grok AI integration, content platform | AI-generated content on platform needs provenance tracking |
| **Spotify** | Sweden | AI-generated playlists, DJ, potential synthetic audio | EU-HQ; AI DJ and synthetic voice features trigger Article 50 |
| **YouTube (Google)** | US | Video platform with AI tools | AI-generated content labeling already rolling out; needs backend proof |

**Sales angle:** "You already label AI content. But can you prove that label is authentic and hasn't been tampered with? Etch provides the cryptographic backbone behind your AI content labeling."

---

### Segment 5: Enterprise AI / Compliance-First Buyers

**Why they need Etch:** Large enterprises deploying AI internally (document generation, customer communications, automated reporting) need provenance records for regulatory compliance and internal audit.

**Compliance pressure:** MEDIUM-HIGH. These companies are compliance-driven by nature and will adopt early.

| Company | HQ | What They Do | Etch Fit |
|---|---|---|---|
| **SAP** | Germany | Enterprise software with AI copilots | German HQ, direct EU enforcement; AI-generated business docs need provenance |
| **Siemens** | Germany | Industrial AI, document generation | Regulated industry + German jurisdiction = high compliance urgency |
| **Deutsche Telekom** | Germany | Telco with AI customer service | AI-generated customer communications in regulated telco environment |
| **Philips** | Netherlands | Healthcare AI | High-risk AI (healthcare) with extreme documentation requirements |
| **Bosch** | Germany | Industrial AI, autonomous systems | Automotive + industrial AI under high-risk classification |
| **Thomson Reuters** | Canada/UK | AI-powered legal and news content | AI-generated legal/news content = public interest disclosure required |
| **Wolters Kluwer** | Netherlands | AI in legal, tax, healthcare content | Professional content where AI provenance is critical for liability |
| **RELX (Elsevier)** | UK/Netherlands | Scientific publishing with AI tools | AI-assisted scientific content requires provenance for integrity |

**Sales angle:** "Your auditors and regulators will ask for proof that AI-generated content in your business processes is tracked and verifiable. Etch gives you an immutable audit trail without changing your existing workflows."

---

## Prioritization Matrix

### Tier 1 — Immediate Outreach (Highest urgency, strongest fit)

These companies face direct Article 50 obligations, are in or serving the EU, and have no established provenance solution:

| Company | Segment | Why Tier 1 |
|---|---|---|
| **Mistral AI** | GPAI | EU-HQ, direct enforcement, no known provenance layer |
| **Aleph Alpha** | GPAI | German company, EU-first, enterprise-focused |
| **ElevenLabs** | Synthetic Media | Polish roots, voice cloning = high regulatory scrutiny |
| **Synthesia** | Synthetic Media | UK-based, deepfake video = Article 50 poster child |
| **SAP** | Enterprise | German HQ, massive AI feature rollout, compliance DNA |
| **Spotify** | Platform | Swedish HQ, AI features expanding, direct EU jurisdiction |
| **HeyGen** | Synthetic Media | Rapid growth, deepfake-capable, no provenance infrastructure |

### Tier 2 — Near-Term Pipeline (Strong fit, EU exposure, larger sales cycle)

| Company | Segment | Why Tier 2 |
|---|---|---|
| **Stability AI** | GPAI | Open models widely deployed; provenance gap |
| **Runway** | Synthetic Media | Leading video gen; compliance-aware customer base |
| **Midjourney** | Synthetic Media | Enormous volume; no current provenance stack |
| **Adobe** | Content Platform | Already has Content Credentials; Etch = independent verification |
| **Thomson Reuters** | Enterprise | AI in news/legal; public interest disclosure rules |
| **Wolters Kluwer** | Enterprise | NL-based, professional content, compliance-oriented |
| **Siemens** | Enterprise | German industrial giant, heavy AI adoption |

### Tier 3 — Strategic / Large Enterprise (Longer cycle, high contract value)

| Company | Segment | Why Tier 3 |
|---|---|---|
| **OpenAI** | GPAI | Massive scale; likely building in-house but needs independent verification |
| **Google DeepMind** | GPAI | Has SynthID; Etch = complementary independent layer |
| **Meta Platforms** | Platform | Already labeling; needs tamper-evident backend |
| **Anthropic** | GPAI | Compliance-forward culture; potential design partner |

---

## Etch Technical Differentiators for EU AI Act Compliance

### Why Etch vs. alternatives (C2PA/Content Credentials, SynthID, blockchain)

| Criterion | Etch | C2PA / Content Credentials | Watermarking (SynthID etc.) | Public Blockchain |
|---|---|---|---|---|
| **Privacy** | Only SHA-256 hashes stored; content never leaves customer infrastructure | Metadata may reveal provenance chain, editing history | Embedded in content; privacy-neutral | Transaction data public on-chain |
| **Tamper evidence** | Merkle chain: altering any entry invalidates all subsequent | Signature-based; individual certs can be revoked | Watermarks can be stripped or degraded | Strong (consensus-based) |
| **Offline verification** | Yes, inclusion proofs verify without server | Needs PKI/certificate chain | Needs detector model | Needs node access |
| **Latency** | Sub-millisecond append (in-memory chain + async DB) | Signing overhead | Encoding overhead | Block confirmation delay (seconds to minutes) |
| **Cost** | Hash computation only; no gas fees, no certificate authority | CA infrastructure costs | Model inference cost per asset | Gas fees per transaction |
| **Scalability** | Millions of entries; O(1) append, O(1) proof generation | Good (signature-based) | Good (per-asset) | Limited by block throughput |
| **Regulatory fit** | Machine-readable proof receipts with timestamps and chain roots | Industry standard but complex ecosystem | Meets watermarking requirement | Overkill for provenance; regulatory uncertainty |

### Etch's Strongest Selling Points for Article 50

1. **Privacy by design.** Content never leaves the customer's environment. Only a SHA-256 hash is registered. This matters enormously for GPAI providers processing user prompts (GDPR intersection).

2. **Offline-verifiable proofs.** Regulators or third parties can verify a proof receipt without calling back to Etch servers. This satisfies the Article 50 requirement for content to carry its own provenance.

3. **Tamper-evident chain.** The Merkle chain structure means any attempt to alter, backdate, or delete a provenance record is cryptographically detectable. This is exactly what "robust" marking means in regulatory terms.

4. **Lightweight integration.** Single API call to register (`POST /v1/proof`), single API call to verify (`POST /v1/proof/{id}/verify`). Can be embedded in any content generation pipeline with minimal engineering effort.

5. **Complementary to watermarking.** Etch does not replace watermarking; it provides an independent, tamper-evident record that a piece of content was generated at a specific time. Use alongside SynthID, C2PA, or other marking schemes.

---

## Outreach Talking Points

### Opening (cold email / intro call)

> "Article 50 of the EU AI Act requires your AI-generated outputs to carry machine-readable provenance that's robust and verifiable. The compliance deadline is August 2026. Etch is a content provenance API that creates tamper-evident proof receipts for every piece of generated content, without ever seeing or storing the content itself. One API call, sub-millisecond, privacy-preserving."

### For GPAI / Foundation Model Providers

> "Your models generate content for thousands of downstream deployers. Under Article 50, you need provenance at the model output layer. Etch registers a SHA-256 hash of each output on a Merkle chain, giving every generated asset a verifiable timestamp and proof of origin. Your customers can independently verify these proofs offline."

### For Synthetic Media Companies

> "Deepfake regulations are the most explicit part of Article 50. Every AI-generated video, voice clip, or avatar you produce needs a provenance trail. Etch gives you that trail without storing any of the generated content, and the proof receipts travel with the content wherever it goes."

### For Enterprise Buyers

> "Your compliance team is already asking about EU AI Act readiness. Etch drops into your existing AI pipelines with a single API integration and gives you an immutable audit log of every piece of AI-generated content in your organization. When the regulator asks for proof, you have it."

---

## Next Steps

1. **Build prospect contact list** for Tier 1 companies (target: compliance/legal/engineering leads)
2. **Create segment-specific one-pagers** adapting the talking points above
3. **Develop a compliance checklist** ("EU AI Act Article 50 Readiness") as a lead magnet
4. **Set up demo environment** showing register -> verify -> offline proof workflow
5. **Target EU-based conferences:** AI Act compliance events, Web Summit, VivaTech, DMEXCO
6. **Partnership angle:** Approach C2PA members (Adobe, Microsoft, BBC) about Etch as complementary independent verification layer
