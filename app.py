
import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

from ai_backend  import ask_data_analyst


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="InsightAI",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ============================================================
# SESSION STATE
# ============================================================

if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []

if "last_result" not in st.session_state:
    st.session_state.last_result = None

if "question_input" not in st.session_state:
    st.session_state.question_input = ""


# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown(
    """
    <style>

    .stApp {
        background-color: #0b0f14;
    }

    .main .block-container {
        max-width: 1150px;
        padding-top: 45px;
        padding-bottom: 60px;
    }

    section[data-testid="stSidebar"] {
        background-color: #0f141b;
        border-right: 1px solid #202731;
    }

    section[data-testid="stSidebar"] .block-container {
        padding-top: 30px;
    }

    .main-brand {
        text-align: center;
        font-size: 42px;
        font-weight: 700;
        letter-spacing: -1px;
        margin-bottom: 5px;
    }

    .brand-symbol {
        color: #7188ff;
    }

    .tagline {
        text-align: center;
        color: #8f99a8;
        font-size: 17px;
        margin-bottom: 45px;
    }

    .hero-title {
        text-align: center;
        font-size: 30px;
        font-weight: 650;
        margin-bottom: 8px;
    }

    .hero-description {
        text-align: center;
        color: #8f99a8;
        font-size: 15px;
        line-height: 1.6;
        max-width: 700px;
        margin: 0 auto 30px auto;
    }

    div[data-testid="stTextArea"] textarea {
        background-color: #111720 !important;
        color: #f5f7fa !important;
        border: 1px solid #2b3440 !important;
        border-radius: 12px !important;
        font-size: 16px !important;
        padding: 16px !important;
        min-height: 120px !important;
    }

    div[data-testid="stTextArea"] textarea:focus {
        border-color: #7188ff !important;
        box-shadow: 0 0 0 1px #7188ff !important;
    }

    div.stButton > button {
        border-radius: 10px;
        border: 1px solid #2b3440;
        background-color: #111720;
        color: #dbe2ec;
        font-size: 14px;
        font-weight: 500;
        min-height: 42px;
    }

    div.stButton > button:hover {
        border-color: #7188ff;
        color: #ffffff;
        background-color: #151c27;
    }

    .analyze-button div.stButton > button {
        width: 100%;
        height: 48px;
        border: none;
        background-color: #667eea;
        color: white;
        font-size: 16px;
        font-weight: 600;
    }

    .analyze-button div.stButton > button:hover {
        background-color: #7b8ef5;
        color: white;
    }

    div[data-testid="stMetric"] {
        background-color: #111720;
        border: 1px solid #202a36;
        border-radius: 12px;
        padding: 18px;
    }

    div[data-testid="stMetricLabel"] {
        color: #8f99a8;
    }

    .section-space {
        margin-top: 35px;
    }

    .footer {
        text-align: center;
        color: #5f6977;
        font-size: 13px;
        margin-top: 55px;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown("## ◈ InsightAI")

    st.caption("AI-powered natural language analytics")

    st.divider()

    st.markdown("### Dataset")

    st.write("**Customer Shopping Dataset**")

    st.caption("Connected through MySQL")

    st.divider()

    st.markdown("### Dataset Overview")

    st.write("📊 **3,900** records")
    st.write("🧩 **19** columns")
    st.write("🗄️ **MySQL** database")
    st.write("🤖 **Ollama** LLM")

    st.divider()

    st.markdown("### What you can ask")

    st.caption(
        "Ask questions about purchases, customers, "
        "categories, subscriptions, ratings, locations "
        "and more."
    )


# ============================================================
# MAIN BRAND
# ============================================================

st.markdown(
    '<div class="main-brand">'
    '<span class="brand-symbol">◈</span> INSIGHTAI'
    '</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="tagline">'
    'Your data. Your questions. Instant insights.'
    '</div>',
    unsafe_allow_html=True
)


# ============================================================
# HERO
# ============================================================

st.markdown(
    '<div class="hero-title">Ask your data anything.</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="hero-description">'
    'Turn natural-language questions into data-driven answers '
    'using AI, SQL and your connected database.'
    '</div>',
    unsafe_allow_html=True
)


# ============================================================
# CLICKABLE EXAMPLE QUESTIONS
# ============================================================

st.markdown("### Try asking")

examples = [
    "Which category has the highest total purchase amount?",
    "What is the average purchase amount for subscribed customers?",
    "Which categories have the highest average review rating?",
    "Compare subscribed and non-subscribed customers."
]


def select_example(example):
    st.session_state.question_input = example


col1, col2 = st.columns(2)

for index, example in enumerate(examples):

    target_col = col1 if index % 2 == 0 else col2

    with target_col:
        st.button(
            example,
            key=f"example_{index}",
            use_container_width=True,
            on_click=select_example,
            args=(example,)
        )


# ============================================================
# QUESTION INPUT
# ============================================================

question = st.text_area(
    "Ask your question",
    placeholder=(
        "Example: What is the average purchase amount "
        "for subscribed customers?"
    ),
    height=120,
    label_visibility="collapsed",
    key="question_input"
)


# ============================================================
# ANALYZE BUTTON
# ============================================================

st.markdown('<div class="analyze-button">', unsafe_allow_html=True)

analyze_clicked = st.button(
    "Analyze  →",
    use_container_width=True
)

st.markdown("</div>", unsafe_allow_html=True)


if analyze_clicked:

    if not question.strip():

        st.warning("Please enter a question first.")

    else:

        with st.spinner("Analyzing your data..."):

            result = ask_data_analyst(
                question,
                st.session_state.conversation_history
            )

        st.session_state.last_result = result


# ============================================================
# DISPLAY RESULT
# ============================================================

result = st.session_state.last_result

if result is not None:

    st.divider()

    if not result.get("success"):

        st.error(
            result.get(
                "error",
                "Something went wrong."
            )
        )

    elif result.get("supported") is False:

        st.warning(
            result.get(
                "answer",
                "This question is not supported."
            )
        )

    else:

        # ----------------------------------------------------
        # AI INSIGHT
        # ----------------------------------------------------

        st.markdown("### Insight")

        st.write(result["answer"])


        # ----------------------------------------------------
        # DATA TABLE
        # ----------------------------------------------------

        formatted_result = result.get("result", [])

        if formatted_result:

            st.markdown("### Data")

            dataframe = pd.DataFrame(formatted_result)

            column_config = {}

            for column in dataframe.columns:
                if pd.api.types.is_float_dtype(dataframe[column]):
                    column_config[column] = st.column_config.NumberColumn(
                        column.replace("_", " ").title(),
                        format="%.2f"
                    )
                elif pd.api.types.is_integer_dtype(dataframe[column]):
                    column_config[column] = st.column_config.NumberColumn(
                        column.replace("_", " ").title(),
                        format="%d"
                    )

            st.dataframe(
                dataframe,
                use_container_width=True,
                hide_index=True,
                column_config=column_config
            )


            # ------------------------------------------------
            # VISUALIZATION
            # ------------------------------------------------

            chart = result.get("chart")

            if chart is not None:

                st.markdown("### Visualization")

                chart_dataframe = pd.DataFrame(
                    {
                        chart["category_column"]:
                            chart["categories"],
                        chart["value_column"]:
                            chart["values"]
                    }
                )

                # Horizontal chart: easier to read category labels.
                chart_dataframe = chart_dataframe.sort_values(
                    chart["value_column"],
                    ascending=True
                )

                fig, ax = plt.subplots(figsize=(10, 4.8))

                ax.barh(
                    chart_dataframe[chart["category_column"]],
                    chart_dataframe[chart["value_column"]]
                )

                ax.set_xlabel(
                    chart["value_column"].replace("_", " ").title()
                )
                ax.set_ylabel("")
                ax.set_title(
                    chart["value_column"].replace("_", " ").title(),
                    fontsize=14,
                    pad=12
                )
                ax.grid(axis="x", alpha=0.25)

                for index, value in enumerate(
                    chart_dataframe[chart["value_column"]]
                ):
                    ax.text(
                        value,
                        index,
                        f" {value:,.2f}",
                        va="center",
                        fontsize=10
                    )

                fig.tight_layout()
                st.pyplot(fig, use_container_width=True)
                plt.close(fig)


        # ----------------------------------------------------
        # GENERATED SQL
        # ----------------------------------------------------

        with st.expander("Show generated SQL"):

            st.code(
                result.get("sql", ""),
                language="sql"
            )


# ============================================================
# CONNECTED DATASET
# ============================================================

st.markdown("### Connected dataset")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        label="Records",
        value="3,900"
    )

with col2:
    st.metric(
        label="Columns",
        value="19"
    )

with col3:
    st.metric(
        label="Data source",
        value="MySQL"
    )


# ============================================================
# FOOTER
# ============================================================

st.markdown(
    '<div class="footer">'
    'InsightAI · Natural Language Data Analytics'
    '</div>',
    unsafe_allow_html=True
)
