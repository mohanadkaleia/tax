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


def build_system_prompt(repo: TaxRepository, year: int) -> str:
    """Build a system prompt with CPA identity and DB context summary."""
    sections = [CPA_IDENTITY]

    # --- Gather data context ---
    context_parts: list[str] = []

    # W-2s
    w2s = repo.get_w2s(year)
    if w2s:
        lines = [f"W-2 Forms ({len(w2s)}):"]
        for w in w2s:
            wages = _fmt_decimal(w.get("box1_wages"))
            fed_wh = _fmt_decimal(w.get("box2_federal_withheld"))
            state_wh = _fmt_decimal(w.get("box17_state_withheld"))
            lines.append(
                f"  - {w['employer_name']}: Wages {wages}, "
                f"Federal withheld {fed_wh}, State withheld {state_wh}"
            )
        context_parts.append("\n".join(lines))
    else:
        context_parts.append("W-2 Forms: None imported.")

    # Sales
    sales = repo.get_sales(year)
    if sales:
        total_proceeds = sum(
            Decimal(str(s["proceeds_per_share"])) * Decimal(str(s["shares"]))
            for s in sales
        )
        context_parts.append(
            f"Sales: {len(sales)} sale(s), total proceeds ${total_proceeds:,.2f}"
        )
    else:
        context_parts.append("Sales: None imported.")

    # Sale results (reconciliation output)
    sale_results = repo.get_sale_results(year)
    if sale_results:
        total_gain = sum(Decimal(str(r["gain_loss"])) for r in sale_results)
        total_ordinary = sum(
            Decimal(str(r.get("ordinary_income", "0"))) for r in sale_results
        )
        context_parts.append(
            f"Reconciled results: {len(sale_results)} result(s), "
            f"total gain/loss ${total_gain:,.2f}, "
            f"ordinary income ${total_ordinary:,.2f}"
        )
    else:
        context_parts.append("Reconciled results: Not yet run.")

    # Lots
    lots = repo.get_lots()
    if lots:
        equity_types = sorted({lt.get("equity_type", "?") for lt in lots})
        context_parts.append(
            f"Lots: {len(lots)} lot(s), equity types: {', '.join(equity_types)}"
        )
    else:
        context_parts.append("Lots: None imported.")

    # Events
    events = repo.get_events()
    if events:
        from collections import Counter

        type_counts = Counter(ev.get("event_type", "?") for ev in events)
        breakdown = ", ".join(f"{t}: {c}" for t, c in sorted(type_counts.items()))
        context_parts.append(f"Events: {len(events)} total ({breakdown})")
    else:
        context_parts.append("Events: None imported.")

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

    # 1099-DIV
    divs = repo.get_1099divs(year)
    if divs:
        total_ord = sum(Decimal(str(d.get("ordinary_dividends", "0"))) for d in divs)
        total_qual = sum(Decimal(str(d.get("qualified_dividends", "0"))) for d in divs)
        context_parts.append(
            f"1099-DIV: {len(divs)} form(s), "
            f"ordinary ${total_ord:,.2f}, qualified ${total_qual:,.2f}"
        )

    # 1099-INT
    ints = repo.get_1099ints(year)
    if ints:
        total_int = sum(Decimal(str(i.get("interest_income", "0"))) for i in ints)
        context_parts.append(f"1099-INT: {len(ints)} form(s), total ${total_int:,.2f}")

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
