"""DTF Test Data Generator -- Streamlit front end.

This file only handles UI, session state, file selection and display. All
transformation reasoning lives in `src/dtf_test_gen`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dtf_test_gen.config import KNOWN_MODELS, AppConfig                    # noqa: E402
from dtf_test_gen.engine import Engine                                     # noqa: E402
from dtf_test_gen.llm.client import OllamaClient                           # noqa: E402
from dtf_test_gen.loaders import LoadError, discover_project, load_ddl, load_dtf, load_skills  # noqa: E402
from dtf_test_gen.loaders.ddl import load_ddl_text                         # noqa: E402
from dtf_test_gen.loaders.dtf import load_dtf_text                         # noqa: E402
from dtf_test_gen.loaders.skills import auto_select, load_skill_payload    # noqa: E402
from dtf_test_gen.models.schema import DDLSet                              # noqa: E402
from dtf_test_gen.sql.writer import render_all, render_insert              # noqa: E402
from dtf_test_gen import ui_helpers as ui                                  # noqa: E402

EXAMPLE_PROJECT = "./examples/simple_customer"

st.set_page_config(page_title="DTF Test Data Generator", page_icon="🧪", layout="wide")

st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem;}
      div[data-testid="stMetricValue"] {font-size: 1.6rem;}
      .dtf-card {border:1px solid rgba(128,128,128,.28); border-radius:8px;
                 padding:.7rem .9rem; margin-bottom:.6rem;}
      .dtf-card h4 {margin:0 0 .35rem 0; font-size:.95rem;}
      .dtf-cov {font-family:ui-monospace,monospace; font-size:.82rem; line-height:1.6;}
      .dtf-yes {color:#1a7f37; font-weight:700;}
      .dtf-no  {color:#c62828; font-weight:700;}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------- state
def state(key: str, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


config: AppConfig = state("config", AppConfig.load())
state("ddl_tables", [])
state("skills", [])
state("dtf", None)
state("analysis", None)
state("generation", None)
state("outcome", None)
state("errors", [])


def reset_results() -> None:
    st.session_state["analysis"] = None
    st.session_state["generation"] = None
    st.session_state["outcome"] = None


# ------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🧪 DTF Test Data Generator")

    st.subheader("Project")
    mode = st.radio(
        "Input mode", ["Local project", "Upload files"],
        horizontal=True, label_visibility="collapsed",
    )

    ddl_paths: list[Path] = []
    dtf_paths: list[Path] = []
    skill_paths: list[Path] = []
    uploaded_ddl = uploaded_dtf = uploaded_skills = None

    if mode == "Local project":
        col_a, col_b = st.columns([3, 1])
        project_dir = col_a.text_input(
            "Project directory", value=state("project_dir", EXAMPLE_PROJECT), key="project_dir",
        )
        if col_b.button("Example", use_container_width=True, help="Load the bundled example project"):
            st.session_state["project_dir"] = EXAMPLE_PROJECT
            reset_results()
            st.rerun()

        found = discover_project(project_dir)
        if found.errors:
            for message in found.errors:
                st.warning(f"⚠️ {message}")
        if found.empty and not found.errors:
            st.info("No DDL, DTF or skill files found in that directory.")
        ddl_paths, dtf_paths, skill_paths = found.ddl, found.dtf, found.skills
    else:
        uploaded_ddl = st.file_uploader(
            "DDL (JSON / YAML)", type=["json", "yaml", "yml"], accept_multiple_files=True,
        )
        uploaded_dtf = st.file_uploader(
            "DTF configuration", type=["json", "yaml", "yml", "sql"], accept_multiple_files=False,
        )
        uploaded_skills = st.file_uploader(
            "Skills (Markdown)", type=["md", "markdown"], accept_multiple_files=True,
        )

    # ---- DDL ---------------------------------------------------------
    st.subheader("Source schemas")
    tables = []
    load_errors: list[LoadError] = []
    if mode == "Local project" and ddl_paths:
        ddl_all, load_errors = load_ddl(list(ddl_paths))
        tables = ddl_all.tables
    elif uploaded_ddl:
        for upload in uploaded_ddl:
            try:
                tables.extend(load_ddl_text(upload.getvalue().decode("utf-8"), upload.name))
            except LoadError as exc:
                load_errors.append(exc)

    for exc in load_errors:
        st.error(f"❌ **{exc.message}**\n\nFile: `{exc.file}`\n\nReason: {exc.reason}")

    # ---- DTF ---------------------------------------------------------
    st.subheader("DTF configuration")
    dtf = None
    if mode == "Local project" and dtf_paths:
        names = [p.name for p in dtf_paths]
        choice = st.radio("Available", names, label_visibility="collapsed", key="dtf_choice")
        try:
            dtf = load_dtf(dtf_paths[names.index(choice)])
        except LoadError as exc:
            st.error(f"❌ **{exc.message}**\n\nFile: `{exc.file}`\n\nReason: {exc.reason}")
    elif uploaded_dtf is not None:
        try:
            dtf = load_dtf_text(uploaded_dtf.getvalue().decode("utf-8"), uploaded_dtf.name)
        except LoadError as exc:
            st.error(f"❌ **{exc.message}**\n\nFile: `{exc.file}`\n\nReason: {exc.reason}")
    elif mode == "Local project":
        st.caption("No DTF configuration found.")

    # Tables the DTF references are pre-selected and highlighted.
    referenced = {t.lower() for t in (dtf.source_tables if dtf else [])}
    selected_tables = []
    if tables:
        for table in tables:
            hit = table.name.lower() in referenced
            label = f"{'⭐ ' if hit else ''}{table.source_file or table.name}"
            if st.checkbox(label, value=hit or not referenced, key=f"tbl_{table.fq_name}"):
                selected_tables.append(table)
        if referenced:
            st.caption("⭐ referenced by the selected DTF")
    else:
        st.caption("No schemas loaded yet.")
    ddl = DDLSet(tables=selected_tables)

    # ---- Skills ------------------------------------------------------
    st.subheader("DTF skills")
    skills = []
    if mode == "Local project" and skill_paths:
        skills = load_skills(list(skill_paths))
    elif uploaded_skills:
        skills = [
            load_skill_payload(u.name, u.getvalue().decode("utf-8", "replace"))
            for u in uploaded_skills
        ]
    if skills and dtf:
        auto_select(skills, dtf)

    if skills:
        c1, c2, c3 = st.columns(3)
        if c1.button("All", use_container_width=True):
            for skill in skills:
                st.session_state[f"skill_{skill.name}"] = True
        if c2.button("None", use_container_width=True):
            for skill in skills:
                st.session_state[f"skill_{skill.name}"] = False
        if c3.button("Auto", use_container_width=True):
            for skill in skills:
                st.session_state[f"skill_{skill.name}"] = skill.selected
        for skill in skills:
            key = f"skill_{skill.name}"
            skill.selected = st.checkbox(
                skill.name, value=st.session_state.get(key, skill.selected), key=key,
                help=skill.reason,
            )
    else:
        st.caption("No skill files found.")

    # ---- Ollama ------------------------------------------------------
    st.subheader("Ollama")
    host = st.text_input("Host", value=config.ollama.host)
    status = OllamaClient(host).status()
    if status.connected:
        st.success(f"● Connected · {len(status.models)} model(s)")
    else:
        st.warning(f"⚠ Ollama is not running.\n\n{status.error}\n\nStart it with `ollama serve`.")

    options = status.models or KNOWN_MODELS
    default_model = config.ollama.model
    if default_model not in options:
        options = [default_model, *options]
    model = st.selectbox("Model", options, index=options.index(default_model))
    temperature = st.slider("Temperature", 0.0, 1.0, config.ollama.temperature, 0.05)

    st.subheader("Generation")
    gen_mode = st.radio(
        "Mode", ["Fast", "Deep"], horizontal=True,
        help="Fast: one Ollama call. Deep: adds a validation pass.",
    )
    use_llm = st.checkbox(
        "Use Ollama", value=status.connected,
        help="Off runs deterministic analysis only -- no model call at all.",
    )
    use_cache = st.checkbox("Cache analysis", value=config.cache.enabled)
    max_rows = st.number_input("Max rows per table", 1, 500, config.generation.max_rows)

    engine = Engine(config)
    analyse_clicked = st.button("Analyze DTF", type="primary", use_container_width=True)
    if st.button("Clear cache", use_container_width=True):
        st.toast(f"Cleared {engine.cache.clear()} cached analyses.")

    st.caption(f"Config: `{config.source or 'defaults'}` · cached: {engine.cache.count()}")


# ------------------------------------------------------------------ actions
if analyse_clicked:
    if dtf is None:
        st.error("❌ Select a DTF configuration first.")
    elif not ddl.tables:
        st.error("❌ Select at least one source schema.")
    else:
        reset_results()
        bar = st.progress(0.0, text="Starting...")
        try:
            outcome = engine.analyse(
                dtf=dtf, ddl=ddl, skills=skills, model=model,
                deep=(gen_mode == "Deep"), use_llm=use_llm, use_cache=use_cache,
                temperature=temperature, host=host,
                progress=lambda pct, text: bar.progress(pct, text=text),
            )
            bar.progress(1.0, text="Analysis complete")
            st.session_state["outcome"] = outcome
            st.session_state["analysis"] = outcome.analysis
            st.session_state["dtf"] = dtf
        finally:
            bar.empty()

analysis = st.session_state.get("analysis")
outcome = st.session_state.get("outcome")

st.header("DTF Test Data Generator")

if analysis is None:
    st.info(
        "Pick a project directory (or upload files) in the sidebar, choose a DTF "
        "configuration, then press **Analyze DTF**.\n\n"
        f"The bundled example lives at `{EXAMPLE_PROJECT}`."
    )
    st.stop()

if outcome:
    if outcome.from_cache:
        st.success("✓ Using cached transformation analysis — no Ollama call made.")
    if outcome.llm_error:
        st.error(f"❌ **Ollama unavailable**\n\n{outcome.llm_error}")
    for message in outcome.messages:
        if "invalid structured output" in message:
            st.warning(f"⚠ {message}")
    if outcome.llm_called and not outcome.llm_error:
        st.success(f"✓ Analysed with `{model}` — 1 call, ≈{outcome.prompt_tokens} prompt tokens.")

for warning in analysis.warnings:
    st.warning(f"⚠ {warning}")

# Generation runs automatically once analysis exists, and again on demand.
if st.session_state.get("generation") is None:
    st.session_state["generation"] = engine.generate(analysis, ddl, max_rows=int(max_rows))
generation = st.session_state["generation"]

tab_overview, tab_transforms, tab_scenarios, tab_data, tab_sql = st.tabs(
    ["Overview", "Transformations", "Test Scenarios", "Generated Data", "SQL"]
)

# ------------------------------------------------------------------ overview
with tab_overview:
    stats = ui.counts(analysis)
    c = st.columns(4)
    c[0].metric("Tables used", stats["tables_used"])
    c[1].metric("Tables ignored", stats["tables_ignored"])
    c[2].metric("Transformations", stats["transformations"])
    c[3].metric("Transformation paths", stats["paths"])

    c = st.columns(4)
    c[0].metric("Transformation columns", stats["transformation_columns"])
    c[1].metric("1:1 columns", stats["one_to_one_columns"])
    c[2].metric("Unused columns", stats["unused_columns"])
    c[3].metric("NOT NULL fillers", stats["not_null_fillers"])

    c = st.columns(3)
    c[0].metric("Generated source rows", generation.total_rows)
    c[1].metric("Scenarios", len(generation.scenarios))
    coverage = generation.coverage
    c[2].metric("Coverage", f"{coverage.percent}%", f"{coverage.covered}/{coverage.total} paths")

    if coverage.missing:
        st.error(
            "⚠ **Coverage incomplete**\n\nMissing:\n"
            + "\n".join(f"- {path}" for path in coverage.missing)
        )
    else:
        st.success("✓ Every transformation path is covered.")
    if coverage.repaired:
        st.info("Automatic repair added rows for: " + ", ".join(coverage.repaired))

    naive = generation.naive_row_estimate
    if naive and naive > generation.total_rows:
        st.caption(
            f"A row-per-path-per-table dataset would need about {naive} rows; "
            f"this run uses {generation.total_rows} ({naive - generation.total_rows} fewer)."
        )

    st.subheader("Source column analysis")
    show_unused = st.checkbox("Show unused columns", value=False)
    frame = ui.columns_dataframe(analysis, include_unused=show_unused)
    if frame.empty:
        st.caption("No columns to show.")
    else:
        st.dataframe(frame, use_container_width=True, hide_index=True)

    if analysis.tables_ignored:
        st.caption("Ignored tables: " + ", ".join(analysis.tables_ignored))
    if analysis.notes:
        with st.expander("Analysis notes"):
            for note in analysis.notes:
                st.write("•", note)
    with st.expander("Run details"):
        st.write(f"**Source:** {analysis.source}  ·  **Model:** {analysis.model or '-'}")
        st.write("**Skills used:** " + (", ".join(analysis.skills_used) or "none"))
        if outcome:
            st.write(f"**Ollama called:** {'yes' if outcome.llm_called else 'no'}")

# ------------------------------------------------------------- transformations
with tab_transforms:
    if not analysis.transformations:
        st.info("No transformation branches were detected in this DTF.")
    covered_ids = {r.path_id for r in generation.coverage.rows if r.covered}
    for transformation in analysis.transformations:
        with st.expander(
            f"**{transformation.id}** · {transformation.kind.replace('_', ' ')} · {transformation.description}",
            expanded=True,
        ):
            st.code(transformation.expression, language="sql")
            lines = []
            for path in transformation.paths:
                hit = path.id in covered_ids
                mark = f"<span class='{'dtf-yes' if hit else 'dtf-no'}'>{'✓' if hit else '✗'}</span>"
                lines.append(f"{mark} <b>{path.label}</b> — {path.description}")
            st.markdown("<div class='dtf-cov'>" + "<br>".join(lines) + "</div>", unsafe_allow_html=True)

    st.subheader("Coverage")
    cframe = ui.coverage_dataframe(generation)
    if not cframe.empty:
        st.dataframe(cframe, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- scenarios
with tab_scenarios:
    cards = ui.scenario_cards(generation, analysis)
    c = st.columns(3)
    c[0].metric("Scenarios", len(cards))
    c[1].metric("Transformation paths", generation.coverage.total)
    c[2].metric("Covered", f"{generation.coverage.covered} ({generation.coverage.percent}%)")

    columns = st.columns(2)
    for i, card in enumerate(cards):
        with columns[i % 2]:
            covers = "<br>".join(
                f"<span class='{'dtf-yes' if item['covered'] else 'dtf-no'}'>"
                f"{'✓' if item['covered'] else '✗'}</span> "
                f"<b>{item['id']}</b> — {item['description']}"
                for item in card["covers"]
            )
            st.markdown(
                f"<div class='dtf-card'><h4>{card['id']}</h4>"
                f"<div>{card['description']}</div>"
                f"<div class='dtf-cov' style='margin-top:.4rem'>{covers}</div></div>",
                unsafe_allow_html=True,
            )

# ------------------------------------------------------------- generated data
with tab_data:
    if not generation.tables:
        st.info("No rows were generated.")
    for table in generation.tables:
        st.subheader(f"{table.table}  ·  {table.row_count} row(s)")
        st.caption(f"`{table.fq_name}`")
        frame = ui.rows_dataframe(generation, table.table)
        if not frame.empty:
            st.dataframe(frame, use_container_width=True, hide_index=True)
        if table.omitted_columns:
            with st.expander(f"{len(table.omitted_columns)} column(s) deliberately omitted"):
                st.write(", ".join(f"`{c}`" for c in table.omitted_columns))
    for warning in generation.warnings:
        st.warning(f"⚠ {warning}")

# ---------------------------------------------------------------------- sql
with tab_sql:
    dtf_name = st.session_state["dtf"].name if st.session_state.get("dtf") else "dtf"
    all_sql = render_all(
        generation, ddl,
        header=f"DTF: {dtf_name}\nRows: {generation.total_rows}  Coverage: {generation.coverage.percent}%",
    )

    c1, c2, c3 = st.columns([1, 1, 2])
    c1.download_button(
        "⬇ Download SQL", all_sql, file_name="inserts.sql",
        mime="text/plain", use_container_width=True,
    )
    if c2.button("💾 Save run", use_container_width=True):
        run_dir = engine.write_output(analysis, generation, ddl, dtf_name)
        st.success(f"Wrote `{run_dir}/inserts.sql`, `coverage.json`, `analysis.json`.")

    st.caption(
        "Use the copy button in the top-right of any code block below. "
        "Only transformation-relevant and NOT NULL columns are included."
    )

    st.subheader("All tables")
    st.code(all_sql, language="sql")

    if len(generation.tables) > 1:
        st.subheader("Per table")
        for table in generation.tables:
            with st.expander(f"{table.table} ({table.row_count} rows)"):
                st.code(render_insert(table, ddl.get(table.table)), language="sql")
