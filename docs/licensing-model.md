# Etch Licensing and Royalty Model

This document explains how Etch is licensed, what Nous network integration means, and what it costs to build commercially on Etch. This is a business document -- the actual legal text is [LICENSE.md](../LICENSE.md) (Rising Sun License v1.0).

---

## License Tiers

### Personal / Non-commercial -- Free

Use Etch however you want. Run it, study it, modify it, share it. Personal projects, education, research, tinkering -- all free, no registration, no strings attached.

You can self-host entirely on your own infrastructure with zero external dependencies.

### Builder / Commercial -- Revenue Share via Nous

If you build something that generates revenue using Etch, you connect it to the Nous network. Nous handles verification, attestation, and accounting automatically. You integrate once and it works.

- The revenue share rate is small, published, and predictable.
- You always keep the majority.
- The exact rate is governed by the Nous protocol and published at the canonical terms endpoint.

### Enterprise -- Custom Terms

Custom integration, dedicated support, and private deployment are available directly from Rising Sun. Enterprise terms are negotiated individually and may include:

- Modified Nous configurations
- SLAs and uptime guarantees
- Priority access to new capabilities
- Private deployment options

Contact: alex@risingsun.name

---

## Nous Network Integration

### What it is

Nous is a verification and attestation service. It is not a phone-home mechanism. It is not surveillance.

### How it works

When your deployment does work -- processes data, serves users, executes transactions -- Nous verifies the work, attests to its completion, and routes value through the Ergon ledger. You integrate once; accounting is automatic from that point forward.

### What it measures

Value created, not behavior observed. The attestation record is transparent and auditable. Nous tracks that work happened and what it was worth, not how your users behave or what your system is doing internally.

### Trust scores

Consistent, honest operation earns better terms automatically. Trust scores build over time. There is no penalty system -- just a gradient where reliable operators get rewarded.

---

## Agent Platform Partners

This section is for companies building products on Etch: AI agent platforms, content verification services, document provenance tools, media authentication platforms, and similar.

### The model

- **Integrate once.** Connect to Nous during your initial Etch deployment. After that, accounting is automatic.
- **Published, predictable rate.** The revenue share percentage is public and does not change without notice. No surprise invoices.
- **Builder keeps the majority.** The rate is small by design. Etch succeeds when builders succeed.
- **No hidden fees.** No per-call pricing, no tiered API limits, no overage charges. One published rate.
- **No lock-in.** You can fork Etch, extend it, combine it with other software. The only requirement is that commercial deployments connect to Nous for value accounting.

### Why this works for platforms

If you are building an agent platform or content service, you need provenance infrastructure that is cheap, fast, and trustworthy. Etch gives you sub-millisecond content registration, offline verification, and a tamper-evident chain -- without gas fees, without blockchain complexity, and without storing your users' content.

The Nous integration means you do not need to build your own metering, billing, or attestation layer for the provenance component. It is handled for you.

---

## What You Can Do

- Use, copy, modify, and distribute Etch
- Build commercial products and services on it
- Fork it, extend it, combine it with other software
- Run it on any infrastructure you control
- Self-host for personal use with no external dependencies
- Pre-hash content locally so it never leaves your machines
- Contribute improvements back (appreciated, not required)

## What You Cannot Do

- Remove or bypass Nous integration in commercial deployments
- Represent modified versions as official Rising Sun releases
- Use Rising Sun trademarks without permission

---

## FAQ

**Is this open source?**
Yes. The source is public, you can read every line, fork it, modify it, and distribute it. The one condition is that commercial deployments connect to the Nous network for value accounting. This is sometimes called "source-available with a commercial integration requirement."

**What if I'm just prototyping?**
Free, no strings. Personal use, research, evaluation, proof-of-concept work -- all of it is unrestricted. Nous integration is only required when revenue is flowing.

**How much does the revenue share cost?**
The rate is published at the canonical Nous terms endpoint. It is a small, fixed percentage. You always keep the majority.

**Can I self-host?**
Yes. Personal use is fully self-hosted with zero external dependencies. Commercial deployments self-host the Etch infrastructure but connect to Nous for attestation and accounting.

**What about enterprise?**
Contact alex@risingsun.name. Enterprise terms include custom Nous configurations, SLAs, private deployment options, and priority support.

**Do you store my content?**
No. Etch is privacy-preserving by design. Only SHA-256 hashes are stored. You can pre-hash content locally so it never leaves your infrastructure.

**What happens if Nous is down?**
Etch's core chain operates independently. Proofs are registered and verifiable locally. Nous attestation syncs when connectivity is available.

---

*Rising Sun License v1.0 -- Copyright (c) 2026 Alex Macaluso -- https://risingsun.name*
