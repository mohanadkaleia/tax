"""Interactive CPA expert chat powered by Claude API."""

from decimal import Decimal

from app.db.repository import TaxRepository

# CPA agent identity — condensed from .claude/agents/tax-expert-cpa.md
CPA_IDENTITY = """\
You are a Senior Certified Public Accountant (CPA) specializing in U.S. individual \
taxation with deep expertise in equity compensation (RSUs, ISOs, NSOs, ESPP). You \
have 20+ years of experience handling complex tax situations for technology employees. \
You are licensed in California and thoroughly understand both federal (IRS) and \
California (FTB) tax law.

Core competencies:
- RSUs: Ordinary income at vest, W-2 inclusion, cost-basis correction.
- NSOs: Ordinary income at exercise (spread), basis = strike + recognized income.
- ISOs: No regular income at exercise, AMT preference item (Form 6251), dual-basis \
tracking, AMT credit carryforward (Form 8801).
- ESPP: Section 423 rules, qualifying vs. disqualifying dispositions, ordinary income \
computation, basis adjustment to prevent double taxation.
- Form 8949 reconciliation with adjustment codes (B, e, O).
- Federal tax brackets, NIIT (3.8%), California brackets (1%-13.3%), Mental Health \
Services Tax (1% above $1M), AMT computation.

Rules:
- Always cite IRS publications, form instructions, or IRC sections when making tax \
determinations.
- Be conservative — err on the side of reporting more income, not less.
- Never guess at cost basis. Flag uncertainties clearly.
- If ISOs were exercised, always consider AMT implications.
- Check California conformity — it is not automatic.
- Watch for double taxation of equity income (W-2 income must be reflected in basis).
"""


def _fmt_decimal(value: str | None) -> str:
    """Format a decimal string as currency."""
    if value is None:
        return "$0.00"
    return f"${Decimal(value):,.2f}"


def _fmt_box12(codes: dict | None) -> str:
    """Format W-2 Box 12 codes as a compact string."""
    if not codes:
        return "none"
    return ", ".join(f"{k}={_fmt_decimal(v)}" for k, v in codes.items())


def _fmt_box14(other: dict | None) -> str:
    """Format W-2 Box 14 (Other) as a compact string."""
    if not other:
        return "none"
    return ", ".join(f"{k}={_fmt_decimal(v)}" for k, v in other.items())


def build_system_prompt(repo: TaxRepository, year: int) -> str:
    """Build a system prompt with CPA identity and detailed DB data."""
    sections = [CPA_IDENTITY]

    # --- Gather data context ---
    context_parts: list[str] = []

    # W-2s — full box detail
    w2s = repo.get_w2s(year)
    if w2s:
        lines = [f"W-2 Forms ({len(w2s)}):"]
        for i, w in enumerate(w2s, 1):
            lines.append(f"  #{i}: {w['employer_name']} (State: {w.get('state', 'N/A')})")
            lines.append(
                f"      Box 1 Wages: {_fmt_decimal(w.get('box1_wages'))} | "
                f"Box 2 Fed Withheld: {_fmt_decimal(w.get('box2_federal_withheld'))}"
            )
            lines.append(
                f"      Box 3 SS Wages: {_fmt_decimal(w.get('box3_ss_wages'))} | "
                f"Box 4 SS Withheld: {_fmt_decimal(w.get('box4_ss_withheld'))}"
            )
            lines.append(
                f"      Box 5 Medicare Wages: {_fmt_decimal(w.get('box5_medicare_wages'))} | "
                f"Box 6 Medicare Withheld: {_fmt_decimal(w.get('box6_medicare_withheld'))}"
            )
            lines.append(f"      Box 12 Codes: {_fmt_box12(w.get('box12_codes'))}")
            lines.append(f"      Box 14 Other: {_fmt_box14(w.get('box14_other'))}")
            lines.append(
                f"      Box 16 State Wages: {_fmt_decimal(w.get('box16_state_wages'))} | "
                f"Box 17 State Withheld: {_fmt_decimal(w.get('box17_state_withheld'))}"
            )
        context_parts.append("\n".join(lines))
    else:
        context_parts.append("W-2 Forms: None imported.")

    # Sale results — per-sale detail
    sale_results = repo.get_sale_results(year)
    if sale_results:
        total_gain = sum(Decimal(str(r["gain_loss"])) for r in sale_results)
        total_ordinary = sum(
            Decimal(str(r.get("ordinary_income", "0"))) for r in sale_results
        )
        lines = [
            f"Sale Results ({len(sale_results)}) — "
            f"Total Gain/Loss: ${total_gain:,.2f}, "
            f"Total Ordinary Income: ${total_ordinary:,.2f}:"
        ]
        for i, r in enumerate(sale_results, 1):
            security = r.get("security_name") or r.get("sale_id", "?")
            acq = r.get("acquisition_date", "?")
            sold = r.get("sale_date", "?")
            shares = r.get("shares", "?")
            lines.append(
                f"  #{i}: {security} | Acquired {acq} | Sold {sold} | {shares} shares"
            )
            proceeds = _fmt_decimal(r.get("proceeds"))
            broker_basis = _fmt_decimal(r.get("broker_reported_basis"))
            correct_basis = _fmt_decimal(r.get("correct_basis"))
            lines.append(
                f"      Proceeds: {proceeds} | Broker Basis: {broker_basis} | "
                f"Correct Basis: {correct_basis}"
            )
            adj_code = r.get("adjustment_code", "")
            adj_amt = _fmt_decimal(r.get("adjustment_amount"))
            holding = r.get("holding_period", "?")
            category = r.get("form_8949_category", "?")
            gain = _fmt_decimal(r.get("gain_loss"))
            lines.append(
                f"      Adj: {adj_code} {adj_amt} | {holding} | "
                f"8949 Cat: {category} | Gain/Loss: {gain}"
            )
            ordinary = r.get("ordinary_income", "0")
            amt_adj = r.get("amt_adjustment", "0")
            wash = r.get("wash_sale_disallowed", "0")
            detail_parts = []
            if Decimal(str(ordinary)) != 0:
                detail_parts.append(f"Ordinary Income: {_fmt_decimal(ordinary)}")
            if Decimal(str(amt_adj)) != 0:
                detail_parts.append(f"AMT Adj: {_fmt_decimal(amt_adj)}")
            if Decimal(str(wash)) != 0:
                detail_parts.append(f"Wash Sale Disallowed: {_fmt_decimal(wash)}")
            if r.get("notes"):
                detail_parts.append(f"Notes: {r['notes']}")
            if detail_parts:
                lines.append(f"      {' | '.join(detail_parts)}")
        context_parts.append("\n".join(lines))
    else:
        context_parts.append("Sale Results: Reconciliation not yet run.")

    # Lots — per-lot detail
    lots = repo.get_lots()
    if lots:
        lines = [f"Lots ({len(lots)}):"]
        for i, lt in enumerate(lots, 1):
            eq_type = lt.get("equity_type", "?")
            ticker = lt.get("ticker", "?")
            acq = lt.get("acquisition_date", "?")
            shares = lt.get("shares", "?")
            remaining = lt.get("shares_remaining", "?")
            cost = _fmt_decimal(lt.get("cost_per_share"))
            lines.append(
                f"  #{i}: {eq_type} {ticker} | Acquired {acq} | "
                f"{shares} shares ({remaining} remaining) | Cost/sh: {cost}"
            )
            detail_parts = []
            if lt.get("amt_cost_per_share"):
                detail_parts.append(
                    f"AMT Cost/sh: {_fmt_decimal(lt['amt_cost_per_share'])}"
                )
            if lt.get("notes"):
                detail_parts.append(f"Notes: {lt['notes']}")
            if detail_parts:
                lines.append(f"      {' | '.join(detail_parts)}")
        context_parts.append("\n".join(lines))
    else:
        context_parts.append("Lots: None imported.")

    # Equity events — per-event detail
    events = repo.get_events()
    if events:
        lines = [f"Equity Events ({len(events)}):"]
        for i, ev in enumerate(events, 1):
            ev_type = ev.get("event_type", "?")
            eq_type = ev.get("equity_type", "?")
            ticker = ev.get("ticker", "?")
            ev_date = ev.get("event_date", "?")
            shares = ev.get("shares", "?")
            price = _fmt_decimal(ev.get("price_per_share"))
            lines.append(
                f"  #{i}: {ev_type} {eq_type} {ticker} | Date {ev_date} | "
                f"{shares} shares @ {price}"
            )
            detail_parts = []
            if ev.get("strike_price"):
                detail_parts.append(f"Strike: {_fmt_decimal(ev['strike_price'])}")
            if ev.get("purchase_price"):
                detail_parts.append(f"Purchase Price: {_fmt_decimal(ev['purchase_price'])}")
            if ev.get("grant_date"):
                detail_parts.append(f"Grant Date: {ev['grant_date']}")
            if ev.get("offering_date"):
                detail_parts.append(f"Offering Date: {ev['offering_date']}")
            if ev.get("fmv_on_offering_date"):
                detail_parts.append(
                    f"FMV at Offering: {_fmt_decimal(ev['fmv_on_offering_date'])}"
                )
            if ev.get("ordinary_income"):
                detail_parts.append(
                    f"Ordinary Income: {_fmt_decimal(ev['ordinary_income'])}"
                )
            if detail_parts:
                lines.append(f"      {' | '.join(detail_parts)}")
        context_parts.append("\n".join(lines))
    else:
        context_parts.append("Equity Events: None imported.")

    # Import batches
    batches = repo.get_import_batches(year)
    if batches:
        lines = [f"Import Batches ({len(batches)}):"]
        for b in batches:
            source = b.get("source", "?")
            fpath = b.get("file_path", "?")
            form_type = b.get("form_type", "?")
            count = b.get("record_count", 0)
            lines.append(f"  - {source}: {form_type} from {fpath} ({count} records)")
        context_parts.append("\n".join(lines))

    # Reconciliation runs
    recon_runs = repo.get_reconciliation_runs(year)
    if recon_runs:
        latest = recon_runs[-1]
        context_parts.append(
            f"Reconciliation: {len(recon_runs)} run(s). "
            f"Latest — {latest.get('matched_sales', 0)} matched, "
            f"{latest.get('unmatched_sales', 0)} unmatched, "
            f"status: {latest.get('status', '?')}"
        )
    else:
        context_parts.append("Reconciliation: Not yet run.")

    # 1099-DIV — per-form detail
    divs = repo.get_1099divs(year)
    if divs:
        lines = [f"1099-DIV Forms ({len(divs)}):"]
        for i, d in enumerate(divs, 1):
            payer = d.get("payer_name", "?")
            ordinary = _fmt_decimal(d.get("ordinary_dividends"))
            qualified = _fmt_decimal(d.get("qualified_dividends"))
            cap_gain = _fmt_decimal(d.get("capital_gain_distributions"))
            lines.append(
                f"  #{i}: {payer} | Ordinary: {ordinary} | Qualified: {qualified} | "
                f"Cap Gain Dist: {cap_gain}"
            )
            detail_parts = []
            if d.get("section_199a_dividends") and Decimal(str(d["section_199a_dividends"])) != 0:
                detail_parts.append(f"199A: {_fmt_decimal(d['section_199a_dividends'])}")
            if d.get("foreign_tax_paid") and Decimal(str(d["foreign_tax_paid"])) != 0:
                detail_parts.append(f"Foreign Tax: {_fmt_decimal(d['foreign_tax_paid'])}")
            if d.get("federal_tax_withheld") and Decimal(str(d["federal_tax_withheld"])) != 0:
                detail_parts.append(f"Fed Withheld: {_fmt_decimal(d['federal_tax_withheld'])}")
            if d.get("state_tax_withheld") and Decimal(str(d["state_tax_withheld"])) != 0:
                detail_parts.append(f"State Withheld: {_fmt_decimal(d['state_tax_withheld'])}")
            if detail_parts:
                lines.append(f"      {' | '.join(detail_parts)}")
        context_parts.append("\n".join(lines))

    # 1099-INT — per-form detail
    ints = repo.get_1099ints(year)
    if ints:
        lines = [f"1099-INT Forms ({len(ints)}):"]
        for i, n in enumerate(ints, 1):
            payer = n.get("payer_name", "?")
            interest = _fmt_decimal(n.get("interest_income"))
            lines.append(f"  #{i}: {payer} | Interest: {interest}")
            detail_parts = []
            if n.get("us_savings_bond_interest") and Decimal(str(n["us_savings_bond_interest"])) != 0:
                detail_parts.append(f"US Bond Int: {_fmt_decimal(n['us_savings_bond_interest'])}")
            if n.get("early_withdrawal_penalty") and Decimal(str(n["early_withdrawal_penalty"])) != 0:
                detail_parts.append(f"Early W/D Penalty: {_fmt_decimal(n['early_withdrawal_penalty'])}")
            if n.get("federal_tax_withheld") and Decimal(str(n["federal_tax_withheld"])) != 0:
                detail_parts.append(f"Fed Withheld: {_fmt_decimal(n['federal_tax_withheld'])}")
            if n.get("state_tax_withheld") and Decimal(str(n["state_tax_withheld"])) != 0:
                detail_parts.append(f"State Withheld: {_fmt_decimal(n['state_tax_withheld'])}")
            if detail_parts:
                lines.append(f"      {' | '.join(detail_parts)}")
        context_parts.append("\n".join(lines))

    if context_parts:
        sections.append(
            f"The taxpayer's imported data for tax year {year}:\n"
            + "\n".join(context_parts)
        )
    else:
        sections.append(
            f"No tax data has been imported yet for tax year {year}. "
            "The user may ask general tax questions."
        )

    # --- Computed tax estimate ---
    if sale_results or w2s:
        try:
            from app.engines.estimator import TaxEstimator
            from app.models.enums import FilingStatus

            estimator = TaxEstimator()
            est = estimator.estimate_from_db(repo, year, FilingStatus.SINGLE)

            def _d(v: Decimal) -> str:
                return f"${v:,.2f}"

            estimate_lines = [
                f"Computed Tax Estimate (tax year {year}, filing status: SINGLE):",
                "  NOTE: Filing status defaults to SINGLE. If the taxpayer files "
                "differently, the numbers below will change.",
                "  Income:",
                f"    W-2 Wages: {_d(est.w2_wages)}",
                f"    Interest Income: {_d(est.interest_income)}",
                f"    Dividend Income: {_d(est.dividend_income)}",
                f"    Short-Term Gains: {_d(est.short_term_gains)}",
                f"    Long-Term Gains: {_d(est.long_term_gains)}",
                f"    Total Income: {_d(est.total_income)}",
                f"    AGI: {_d(est.agi)}",
                "  Deductions:",
                f"    Standard Deduction: {_d(est.standard_deduction)}",
                f"    Deduction Used: {_d(est.deduction_used)}",
                f"    Taxable Income: {_d(est.taxable_income)}",
                "  Federal Tax:",
                f"    Regular Tax: {_d(est.federal_regular_tax)}",
                f"    LTCG Tax: {_d(est.federal_ltcg_tax)}",
                f"    NIIT: {_d(est.federal_niit)}",
                f"    AMT: {_d(est.federal_amt)}",
                f"    Total Federal Tax: {_d(est.federal_total_tax)}",
                f"    Federal Withheld: {_d(est.federal_withheld)}",
                f"    Federal Balance Due: {_d(est.federal_balance_due)}",
                "  California Tax:",
                f"    CA Taxable Income: {_d(est.ca_taxable_income)}",
                f"    CA Tax: {_d(est.ca_tax)}",
                f"    CA Mental Health Tax: {_d(est.ca_mental_health_tax)}",
                f"    Total CA Tax: {_d(est.ca_total_tax)}",
                f"    CA Withheld: {_d(est.ca_withheld)}",
                f"    CA Balance Due: {_d(est.ca_balance_due)}",
                "  Total:",
                f"    Total Tax: {_d(est.total_tax)}",
                f"    Total Withheld: {_d(est.total_withheld)}",
                f"    Balance Due: {_d(est.total_balance_due)}",
            ]

            if estimator.warnings:
                estimate_lines.append("  Warnings:")
                for warning in estimator.warnings:
                    estimate_lines.append(f"    - {warning}")

            sections.append("\n".join(estimate_lines))
        except Exception as exc:
            sections.append(
                f"Computed Tax Estimate: Could not compute — {exc}"
            )

    sections.append(
        "Answer the user's tax questions based on the data above. "
        "If data is missing or incomplete, say so. "
        "Cite IRS publications and form instructions when applicable."
    )

    return "\n\n".join(sections)


def run_chat(
    console: "rich.console.Console",  # noqa: F821
    client: "anthropic.Anthropic",  # noqa: F821
    model: str,
    system_prompt: str,
) -> None:
    """Run an interactive chat REPL with the CPA agent."""
    console.print(
        "\n[bold green]TaxBot CPA Chat[/bold green]  "
        "[dim](type 'exit' or 'quit' to end)[/dim]\n"
    )

    messages: list[dict] = []
    exit_commands = {"exit", "quit", "bye"}

    while True:
        try:
            user_input = console.input("[bold cyan]> [/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            break

        stripped = user_input.strip()
        if not stripped:
            continue
        if stripped.lower() in exit_commands:
            break

        messages.append({"role": "user", "content": stripped})

        try:
            full_response = ""
            with client.messages.stream(
                model=model,
                system=system_prompt,
                messages=messages,
                max_tokens=4096,
            ) as stream:
                for text in stream.text_stream:
                    console.print(text, end="", highlight=False)
                    full_response += text
            console.print()  # newline after streamed response
        except Exception as exc:
            console.print(f"\n[bold red]Error:[/bold red] {exc}")
            messages.pop()  # remove the failed user message
            continue

        messages.append({"role": "assistant", "content": full_response})
        console.print()  # blank line between exchanges

    console.print("\n[dim]Goodbye.[/dim]")
