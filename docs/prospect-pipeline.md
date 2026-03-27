# Etch Prospect Pipeline

**Purpose:** Actionable outreach list of 18 prospect companies for Etch content provenance, organized by category with specific integration points, priority, and contact strategy.

**Author:** Alex Macaluso | **Last updated:** 2026-03-27

**Note:** Web research tools were unavailable during compilation. Company descriptions are based on known information as of early 2026. Verify current product details and contact info before outreach.

---

## How to Use This Document

This is a founder outreach doc, not a marketing deck. For each prospect:

1. Read the "Why Etch" column to understand the pitch angle
2. Check the integration point to know which API/SDK to demo
3. Use the approach column to find the right person and channel
4. Cross-reference with [eu-ai-act-prospects.md](eu-ai-act-prospects.md) for EU compliance angles
5. Cross-reference with [licensing-model.md](licensing-model.md) for pricing conversations

---

## Category 1: AI Agent Platforms (High Priority)

**Why this category matters:** AI agents generate content autonomously -- outputs from multi-step agent workflows need provenance to establish what was produced, when, and by which agent. As agent platforms move into production enterprise deployments, customers demand audit trails. Etch is the simplest way to add tamper-evident provenance to every agent output.

**Key talking points:**
- "Your agents produce documents, code, analyses, and decisions autonomously. Your enterprise customers need proof of what was generated and when."
- "One API call per agent output. Sub-millisecond. No content stored. Your agents get a cryptographic receipt for every action."
- "When an agent chain produces a final deliverable, Etch proves the entire provenance chain -- which agent, what step, what timestamp."

| # | Company | What They Do | Why Etch | Integration Point | Priority | Approach |
|---|---------|-------------|----------|-------------------|----------|----------|
| 1 | **LangChain / LangSmith** | Framework and observability platform for LLM applications and agent workflows. LangSmith provides tracing, evaluation, and monitoring. | LangSmith already traces agent runs -- Etch adds tamper-evident, independently verifiable provenance to those traces. Enterprise customers using LangSmith for compliance get cryptographic proof that agent outputs have not been altered. Natural extension of their observability story. | Python SDK as a LangChain callback handler; register proof on each agent step completion | **High** | Engineering/product leadership. LangChain has an active open-source community -- start with a PR adding an Etch callback handler to langchain-community, then reach out to product team. Harrison Chase (CEO) is active on Twitter/X. |
| 2 | **CrewAI** | Multi-agent orchestration framework where teams of AI agents collaborate on tasks with defined roles. | CrewAI agents hand off work between each other. Each handoff is an opportunity for provenance -- prove what Agent A produced before Agent B consumed it. Critical for enterprise customers who need to audit multi-agent workflows. | Python SDK integrated as a CrewAI tool or post-task hook; `POST /v1/proof` on each task completion | **High** | Joao Moura (founder) is accessible on Twitter/X and the CrewAI Discord. Open an issue or PR showing Etch as a CrewAI tool for audit trails. Direct DM with a working integration demo. |
| 3 | **Microsoft AutoGen** | Open-source multi-agent conversation framework from Microsoft Research, enabling agents to collaborate via structured conversations. | AutoGen conversations between agents generate transcripts that need provenance for enterprise audit. Microsoft enterprise customers expect compliance infrastructure. Etch can hash each agent message in the conversation, creating a verifiable chain of agent interactions. | Python SDK as an AutoGen agent middleware; register each message exchange | **Medium** | Microsoft Research team via GitHub issues on the AutoGen repo. Target the enterprise integration track. Harder to reach decision-makers (big company), but a merged PR creates visibility. |
| 4 | **Relevance AI** | No-code/low-code platform for building and deploying AI agents and workflows, targeting business users. | Business users building agents without code have even less ability to build custom audit infrastructure. Etch as a built-in provenance toggle ("enable audit trail") is a compelling enterprise feature for Relevance AI to offer. | REST API (`POST /v1/proof`) called from Relevance AI workflow steps; or Python SDK in their backend | **Medium** | Product/engineering team. Australian company. LinkedIn outreach to CTO/VP Engineering. Position as "enterprise feature your customers are asking for." |

---

## Category 2: Content Authenticity / Media Platforms (High Priority)

**Why this category matters:** The C2PA standard embeds provenance metadata in media files, but it requires PKI infrastructure and only works with file-based content. Etch complements C2PA by providing an independent, tamper-evident timestamp chain that works with any content type. Companies in this space understand provenance -- the conversation is about adding an independent verification layer.

**Key talking points:**
- "C2PA proves who signed the content. Etch proves when the content existed and that nothing in the chain has been tampered with. They are complementary."
- "Your provenance metadata can be stripped from a file. An Etch proof receipt is independently verifiable even if the original file is modified."
- "For news organizations: prove that a photo or article existed at a specific time, independently of your own systems."

**EU AI Act angle:** Article 50 requires machine-readable provenance marking. Etch proof receipts satisfy this requirement and can be embedded alongside C2PA manifests.

| # | Company | What They Do | Why Etch | Integration Point | Priority | Approach |
|---|---------|-------------|----------|-------------------|----------|----------|
| 5 | **Truepic** | Photo and video authenticity platform using C2PA Content Credentials. Provides capture-time provenance for photos/videos. | Truepic anchors provenance at capture time. Etch adds an independent tamper-evident layer -- if Truepic's signing keys were ever compromised, the Etch chain would still prove content existed at the claimed time. Complementary, not competitive. | REST API or SDK to register content hash alongside C2PA manifest creation | **High** | Truepic is a C2PA founding member with a small team. CEO Jeff McGregor and CTO are accessible via LinkedIn. Position as "independent verification layer that strengthens your existing provenance." |
| 6 | **Numbers Protocol** | Blockchain-based content provenance network. Registers photos/videos with on-chain provenance. | Numbers Protocol uses blockchain (slow, gas fees, public). Etch offers sub-millisecond, zero-cost registration as a complementary or alternative provenance layer. For high-volume use cases, Etch scales where blockchain cannot. | REST API as an alternative/complementary registration path for high-volume content | **Medium** | Small team, active in Web3 content provenance community. Approach via their Discord or directly to founders. The pitch is scale and speed, not replacement. |
| 7 | **Associated Press (AP)** | Global news wire service. First major news org to adopt C2PA Content Credentials for photo/video provenance. | AP already uses Content Credentials on published photos. Etch adds tamper-evident timestamping for the editorial chain -- prove when a photo was received, when it was edited, when it was published. Independent of AP's own signing infrastructure. | REST API (`POST /v1/proof`) integrated into AP's editorial workflow and content management system | **High** | AP has a dedicated technology/innovation team. They have publicly discussed content provenance at journalism conferences. Approach via their Director of Product/Technology. Journalism conferences (ONA, ISOJ) are good venues. |
| 8 | **Getty Images** | Largest stock photo/video marketplace. Has partnered with NVIDIA on AI image generation and adopted C2PA for content authenticity. | Every AI-generated image in Getty's library needs provenance. Every licensed photo needs proof of when it entered their system. Etch provides a lightweight provenance layer for millions of assets without the overhead of blockchain or complex PKI. | Batch API for bulk registration of asset library; REST API for new uploads | **Medium** | Enterprise sales cycle. Target their Chief Technology Officer or VP of Product. Getty has publicly committed to content authenticity -- position Etch as infrastructure that supports that commitment. |

---

## Category 3: Legal / Compliance Tech (Medium Priority)

**Why this category matters:** Legal tech companies deal with documents where timestamping and tamper evidence have direct legal value. Chain of custody, e-discovery holds, and regulatory filings all benefit from cryptographic proof of when a document existed and that it has not been altered.

**Key talking points:**
- "Your customers need to prove a document existed at a specific time and has not been altered since. That is exactly what Etch does."
- "Etch proof receipts are independently verifiable -- no need to trust any single server or authority. This matters in adversarial legal proceedings."
- "Privacy-preserving: only the hash is registered. Attorney-client privilege and confidential documents stay confidential."

| # | Company | What They Do | Why Etch | Integration Point | Priority | Approach |
|---|---------|-------------|----------|-------------------|----------|----------|
| 9 | **Ironclad** | AI-powered contract lifecycle management (CLM) platform used by enterprise legal teams. | Every contract version needs a tamper-evident timestamp. When a dispute arises about when a contract was signed or what version was in effect, an Etch proof settles it cryptographically. Ironclad's AI features (contract generation, review) add an EU AI Act angle for AI-generated legal text. | Python SDK or REST API integrated into contract versioning workflow; hash each contract version on save | **Medium** | Legal tech conferences (Legalweek, CLOC). Product/engineering team via LinkedIn. Ironclad's enterprise customers already care about audit trails -- position Etch as the cryptographic backbone. |
| 10 | **Everlaw** | Cloud-based e-discovery and litigation platform. Handles massive document sets for legal proceedings. | E-discovery requires proving chain of custody -- that documents were collected at a specific time and not tampered with. Etch provides cryptographic proof for every document in a collection, verifiable by opposing counsel without trusting Everlaw's systems. | Batch API for bulk document registration during collection; REST API for individual document events | **Medium** | Everlaw targets AmLaw 200 firms and corporate legal departments. Approach VP of Product or Engineering. E-discovery professionals understand chain of custody -- the pitch is straightforward. |
| 11 | **Relativity** | E-discovery platform (formerly kCura). Market leader in litigation document review with AI-powered analytics. | Same chain-of-custody value as Everlaw but at larger scale. Relativity processes billions of documents. Their AI features (Relativity aiR) generate AI-produced document summaries and analyses that need provenance under EU AI Act. | Batch API for collection-level registration; SDK for integration into Relativity's processing pipeline | **Low** | Large company, longer sales cycle. Relativity has a partner ecosystem -- consider building an Etch app for the Relativity Marketplace. Attend Relativity Fest conference. |

---

## Category 4: InsurTech / FinTech (Medium Priority)

**Why this category matters:** Insurance and finance are heavily regulated industries where audit trails are mandatory, not optional. Regulators require proof that records have not been tampered with. AI-generated risk assessments, underwriting documents, and financial reports all need provenance.

**Key talking points:**
- "Your regulators require tamper-evident audit trails. Etch provides cryptographic proof that records have not been altered, without storing any sensitive financial data."
- "AI-generated underwriting decisions, risk assessments, and reports need provenance trails under emerging AI regulation."
- "Sub-millisecond registration means no impact on transaction latency. Privacy-preserving means no sensitive data leaves your infrastructure."

**EU AI Act angle:** AI systems used in insurance and credit scoring are classified as high-risk under Article 6. These require extensive documentation and audit trails.

| # | Company | What They Do | Why Etch | Integration Point | Priority | Approach |
|---|---------|-------------|----------|-------------------|----------|----------|
| 12 | **Lemonade** | AI-first insurance company using AI agents (AI Jim, AI Maya) for claims processing and underwriting. | Lemonade's entire claims process is AI-driven. Every AI claim decision needs a tamper-evident record for regulatory compliance and dispute resolution. EU operations mean direct AI Act exposure. | REST API integrated into claims processing pipeline; register hash of each AI decision with metadata | **Medium** | Lemonade is publicly traded and tech-forward. Their engineering blog discusses their AI stack. CTO/VP Engineering via LinkedIn. Position as regulatory infrastructure for their AI claims pipeline. |
| 13 | **Shift Technology** | AI-powered fraud detection and claims automation for insurance carriers. Paris-headquartered. | French company, direct EU jurisdiction. Their AI generates fraud assessments that insurers act on -- those assessments need provenance for regulatory compliance and legal defensibility. | Python SDK integrated into fraud assessment output pipeline; hash each assessment report | **Medium** | Paris HQ means strong EU AI Act awareness. CTO or VP Product via LinkedIn. French tech events (VivaTech). Compliance angle is strongest here -- "your AI fraud decisions need a tamper-evident audit trail." |

---

## Category 5: AI Training Data Providers (High Priority)

**Why this category matters:** EU AI Act Article 50 and related provisions require transparency about AI training data. Companies that curate, license, or provide training data need to prove what data existed in a training set at a specific point in time. Etch timestamps training data snapshots, creating verifiable proof of dataset provenance.

**Key talking points:**
- "Regulators will ask: what data was in your training set on this date? Etch gives you a cryptographic proof that a specific dataset snapshot existed at a specific time."
- "When a data licensing dispute arises, Etch proof receipts prove exactly which data you had, when you had it, and that the record has not been altered."
- "Privacy-preserving: only the hash of the dataset manifest is registered. The actual data never touches Etch."

**EU AI Act angle:** Article 53 requires GPAI providers to document training data. Article 50 requires provenance for AI outputs. The intersection creates demand for proving data lineage from training data through to model output.

| # | Company | What They Do | Why Etch | Integration Point | Priority | Approach |
|---|---------|-------------|----------|-------------------|----------|----------|
| 14 | **Scale AI** | AI training data platform. Provides labeled datasets, RLHF services, and data curation for major AI labs. | Scale provides the training data that AI models learn from. Under EU AI Act, model providers must document their training data. Scale can offer Etch-verified dataset snapshots as a premium feature -- "your training data is provenance-verified." | Batch API for dataset snapshot registration; SDK integrated into Scale's data pipeline to hash dataset manifests | **High** | Scale AI is well-funded and compliance-aware (government contracts require documentation). CEO Alexandr Wang is public-facing. Target VP of Product or enterprise partnerships team. Position as value-add for Scale's enterprise/government customers. |
| 15 | **Defined.ai** | AI training data marketplace with focus on ethically sourced, licensed data for model training. | Defined.ai's entire value proposition is trustworthy training data. Etch-verified provenance on every dataset strengthens that value proposition. Buyers can independently verify that the dataset they purchased is the same one that was certified. | REST API to register dataset hashes at point of sale; batch API for catalog-level registration | **High** | Smaller company, more accessible. CEO Daniela Braga is active in AI ethics/policy circles. LinkedIn outreach. Position as "cryptographic proof that backs up your ethical sourcing claims." |
| 16 | **Labelbox** | Data-centric AI platform for labeling, curating, and managing training data. Enterprise-focused. | Labelbox customers build training datasets iteratively. Each version of a labeled dataset should have provenance -- prove what labels existed at what time, that labels have not been retroactively altered. Critical for regulatory audits of AI training processes. | Python SDK integrated into Labelbox's export pipeline; register hash of each dataset export/version | **High** | Enterprise AI infrastructure company. VP Product or CTO via LinkedIn. They sponsor MLOps and data-centric AI conferences. Position as "tamper-evident versioning for your training data pipeline." |

---

## Category 6: Publishing / Creative Platforms (Medium Priority)

**Why this category matters:** Creators and publishers need to prove when content was created to establish priority (who published first), defend against plagiarism claims, and comply with AI content disclosure requirements. Etch provides a lightweight timestamp proof without requiring blockchain complexity or legal notarization.

**Key talking points:**
- "Your creators want to prove they published first. Etch gives every piece of content a tamper-evident timestamp, verifiable by anyone."
- "AI-assisted content on your platform needs provenance under EU AI Act Article 50. Etch handles that with a single API call."
- "No content stored, no privacy risk. Just a cryptographic proof that content existed at a specific time."

| # | Company | What They Do | Why Etch | Integration Point | Priority | Approach |
|---|---------|-------------|----------|-------------------|----------|----------|
| 17 | **Substack** | Newsletter and publishing platform where independent writers build paid audiences. | Writers on Substack need to prove when they published an article -- for IP disputes, plagiarism claims, and establishing thought leadership priority. Etch as a platform feature ("your article is timestamped on a tamper-evident chain") differentiates Substack in the creator economy. | REST API integrated into Substack's publish pipeline; hash article content on publish | **Medium** | Product team via LinkedIn. Substack is relatively small and founder-led (Chris Best, Hamish McKenzie). Position as a creator-facing feature: "every Substack post gets a verifiable proof of publication." |
| 18 | **Medium** | Open publishing platform with AI-powered content recommendations and a Partner Program for writers. | Same creator provenance angle as Substack, plus Medium's AI recommendation features mean they are deploying AI that curates content -- provenance for AI-curated content feeds matters under Article 50. | REST API on publish; batch API for backfill of existing content library | **Low** | Medium has been through leadership changes. Target VP of Engineering or Product. Longer shot but large platform with millions of articles that could benefit from provenance. |

---

## First Five: Sequencing Recommendation

These are the five companies to approach first, in order. Selection criteria: (1) strength of Etch fit, (2) accessibility of decision-makers, (3) speed to a pilot deal, (4) reference value for subsequent prospects.

### 1. CrewAI

**Why first:** Small team, accessible founder (Joao Moura), fast-moving open-source project, and the multi-agent provenance use case is the most compelling demo. A working CrewAI + Etch integration becomes a reference implementation for all agent platform prospects. Build the integration first, then reach out.

**Action:** Build a CrewAI tool/callback that registers Etch proofs on task completion. Open a PR or publish as a community package. DM Joao with the demo.

### 2. LangChain / LangSmith

**Why second:** Largest developer community in the AI agent space. A LangChain callback handler for Etch gets visibility with thousands of developers. LangSmith's observability positioning aligns perfectly with provenance. Harrison Chase is accessible and the team actively merges community contributions.

**Action:** Build a `langchain-etch` callback handler. Publish to PyPI. Open a PR to langchain-community. Write a blog post showing agent audit trails with Etch + LangSmith.

### 3. Truepic

**Why third:** Truepic is the leading company in content authenticity and a C2PA founding member. They understand provenance deeply -- the conversation starts at a high level. A Truepic partnership validates Etch in the content authenticity ecosystem and opens doors to AP, Getty, and every other C2PA adopter.

**Action:** LinkedIn outreach to CEO Jeff McGregor or CTO. Lead with: "Etch provides an independent tamper-evident layer that complements Content Credentials. If your signing keys are ever compromised, the Etch chain still proves content existed at the claimed time."

### 4. Scale AI

**Why fourth:** Scale AI's government and enterprise customers have the strictest documentation requirements. EU AI Act compliance for training data provenance is a natural premium feature for Scale. High contract value, and Scale's endorsement carries weight with AI labs.

**Action:** LinkedIn outreach to VP Product or partnerships team. Lead with the EU AI Act training data documentation angle. Offer a pilot integration with one dataset product line.

### 5. Associated Press

**Why fifth:** AP is the most credible news organization in the world and already uses C2PA Content Credentials. An AP partnership validates Etch for the entire news media vertical. AP has a small, innovation-focused technology team that evaluates new tools.

**Action:** Attend journalism technology events (ONA, ISOJ) or reach out to AP's Director of Technology. Lead with: "You already prove photo authenticity with Content Credentials. Etch adds tamper-evident timestamping for your entire editorial chain -- from intake to publication."

---

## EU AI Act Angle by Category

| Category | EU AI Act Relevance | Compliance Deadline | Urgency |
|----------|-------------------|-------------------|---------|
| AI Agent Platforms | Agent outputs are AI-generated content under Article 50; enterprise deployments need provenance | August 2, 2026 | High -- 4 months away |
| Content Authenticity | Directly addressed by Article 50 transparency obligations; C2PA is named as a relevant standard | August 2, 2026 | Very High |
| Legal / Compliance Tech | AI-generated legal documents trigger Article 50; high-risk AI classification for legal decision support | August 2, 2027 (high-risk) | Medium |
| InsurTech / FinTech | High-risk AI classification (Art. 6) for credit scoring and insurance; requires extensive audit trails | August 2, 2027 (high-risk) | Medium |
| AI Training Data | Article 53 training data documentation for GPAI; Article 50 output provenance traces back to training data | August 2, 2025 (GPAI rules) | Immediate -- already in effect |
| Publishing / Creative | AI-assisted content published on matters of public interest triggers Article 50(4) disclosure | August 2, 2026 | High |

---

## Pricing Conversation Guide

Reference [licensing-model.md](licensing-model.md) for full details. Key points for prospect conversations:

- **Evaluation / POC:** Free. No Nous integration required. Personal use license covers prototyping.
- **Production / Commercial:** Rising Sun License requires Nous network integration. Small, published, predictable revenue share. No per-call pricing, no tiered limits.
- **Enterprise:** Custom terms available. Contact alex@risingsun.name. May include modified Nous configurations, SLAs, private deployment.
- **Key objection handler:** "It is not a phone-home mechanism. Nous verifies value created, not behavior observed. The attestation record is transparent and auditable."

---

## Pipeline Summary

| # | Company | Category | Priority | First Contact |
|---|---------|----------|----------|---------------|
| 1 | LangChain / LangSmith | AI Agent Platforms | High | Open-source PR + product team |
| 2 | CrewAI | AI Agent Platforms | High | Founder DM + integration demo |
| 3 | AutoGen (Microsoft) | AI Agent Platforms | Medium | GitHub issue + research team |
| 4 | Relevance AI | AI Agent Platforms | Medium | LinkedIn to CTO |
| 5 | Truepic | Content Authenticity | High | LinkedIn to CEO/CTO |
| 6 | Numbers Protocol | Content Authenticity | Medium | Discord / founder outreach |
| 7 | Associated Press | Content Authenticity | High | Technology team / conferences |
| 8 | Getty Images | Content Authenticity | Medium | VP Product / enterprise sales |
| 9 | Ironclad | Legal / Compliance Tech | Medium | Legal tech conferences / LinkedIn |
| 10 | Everlaw | Legal / Compliance Tech | Medium | VP Product / LinkedIn |
| 11 | Relativity | Legal / Compliance Tech | Low | Marketplace app / Relativity Fest |
| 12 | Lemonade | InsurTech / FinTech | Medium | CTO / LinkedIn |
| 13 | Shift Technology | InsurTech / FinTech | Medium | CTO / VivaTech |
| 14 | Scale AI | AI Training Data | High | VP Product / partnerships |
| 15 | Defined.ai | AI Training Data | High | CEO / LinkedIn |
| 16 | Labelbox | AI Training Data | High | VP Product / ML conferences |
| 17 | Substack | Publishing / Creative | Medium | Product team / LinkedIn |
| 18 | Medium | Publishing / Creative | Low | VP Engineering / LinkedIn |

**High priority count:** 8 | **Medium priority count:** 8 | **Low priority count:** 2

---

## Next Steps

1. **Build integrations for CrewAI and LangChain first** -- these become the demo and reference for all agent platform conversations
2. **Draft a one-page "Etch for Content Provenance" PDF** suitable for cold email attachment
3. **Create a 3-minute Loom video** showing register -> verify -> offline proof workflow
4. **Compile LinkedIn contact list** for the First Five prospects
5. **Block time for outreach:** aim for 3 cold outreaches per week starting with the First Five sequence above
6. **Track pipeline in a simple spreadsheet:** Company | Contact | Date Reached Out | Response | Next Step | Status
