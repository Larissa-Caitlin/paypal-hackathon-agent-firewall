# Design note

The idea behind the look is **seeing through the agent's eyes**. A person and a shopping agent look at the same product listing and get different information. The interface makes that difference visible once, clearly, and then stays out of the way.

## The one signature element

The start page opens with TechMart Deals' real listing (loaded from `/catalog` and `/shops/techmartdeals/views`) shown as an ordinary light product card, next to a switch labelled **View as the agent**. Turning it on reveals the hidden `display:none` note in place, highlighted, with the caption "A person sees an ordinary listing. The agent reads an instruction."

This is the only element with a strong visual treatment: a light card on a dark page, an orange outline when the agent view is on, and a red highlight on the note. Everything else is calm.

## Palette

Dark only, deep ink blue rather than near-black. Colour carries meaning and nothing else.

| Role | Colour |
|---|---|
| Base | `#0E1A24` |
| Surface (panels) | `#15232F` |
| Raised (chips, current nav, selected stage) | `#1C2D3B` |
| Text | `#E6EEF2` |
| Muted text | `#8FA3B1` |
| Trusted / PayPal / primary action | `#4A9EDB` |
| Allow | `#4CC38A` |
| Hold | `#E8B04B` |
| Block | `#E5574F` for fills and borders; `#EE7069` for small text |
| Untrusted | `#E8894B` |

All text pairs meet WCAG AA (4.5:1). The block red as small text measured 4.4:1 on panels, so small red text uses a slightly lighter shade of the same red; fills, borders and badges keep `#E5574F`. AI-check tags use the hold colour, because an AI check can only escalate to hold.

## Type

- **IBM Plex Sans** (400, 500, 600, 700) for everything people read, headings included.
- **IBM Plex Mono** (400, 500) only for machine data: IDs (product, order, merchant), the raw page text the agent read, and amounts and currency as reported by PayPal.
- Self-hosted from `app/static/fonts/` (latin subset, woff2, OFL licence files alongside). Copied from the `@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono` npm packages; no CDN, so the CSP stays `default-src 'self'`.
- Scale: page titles 2–2.35rem, section headings 1.1–1.45rem, body 16px with 1.6 line height. Prose is capped at 70 characters per line.

## Hierarchy

Not every block is a card. Verdicts, the "Claimed vs verified" comparison, the agent's proposal, the purchase and attack forms, and the evidence charts get panels. Supporting detail (the agent's trace, what happened in PayPal, rule checks, AI checks, design decisions, limitations) sits on the page with only spacing and a rule above it.

## Motion

One moment only: on the result page the five progress stages fill in sequence once on load (about one second in total). Nothing else animates apart from user-triggered expand and collapse. With `prefers-reduced-motion: reduce` the stages appear in their final state.

## What was removed, and why

- **All-caps labels and badges** ("UNVERIFIED CLAIM", "BLOCK", "PASS"). They read as a template and shout. Everything is sentence case now: "Unverified claim", "Blocked", "Pass".
- **Arrows appended to links and buttons** ("Attack Lab →", "← Start a new purchase"). The navigation and the words already say where a link goes.
- **Middle-dot summary lines** ("Rules: 2 of 6 failed · AI checks: 1 of 7 flagged"). Replaced with plain sentences: "2 of 6 rules failed. 1 of 7 AI checks flagged."
- **Identical rounded cards for every block**, which made the verdict as loud as the rule table.
- **Decorative colour**: a purple AI accent and a green brand mark that carried no meaning. Green now only ever means allow.
- **Spinners and hover transitions**, so that the result-page progress is the only motion.

## Copy

Plain, active, from the user's point of view. Buttons say exactly what happens: "Run the shopping agent", "Approve in PayPal", "Try this attack", "Approve and pay", "Reject and release the money". Error messages say what happened and what to do next, for example "That access key didn't work. Check it and try again."
