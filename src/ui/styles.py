# DESIGN RULE: this module holds ONLY the CSS constant -- no Streamlit
# calls, no logic. app.py injects it once via st.markdown(f"<style>
# {CUSTOM_CSS}</style>", unsafe_allow_html=True).
"""Custom CSS for the compliance-checker UI: an institutional, document-like
look -- deliberately not Streamlit's default rounded-card theme. See the
module docstring in app.py for the palette and rationale.

DARK MODE: this app injects raw HTML/CSS via st.markdown(unsafe_allow_html=
True), which lands in the main document, not inside a sandboxed custom-
component root. Streamlit's own theme is therefore NOT available to it as
CSS custom properties in the installed version (checked directly, not
assumed): grepping the shipped frontend bundle
(.venv/Lib/site-packages/streamlit/static/static/js/*.js) for "--st-",
"--text-color" and "--background-color" finds zero occurrences in
streamlit==1.60.0 -- the "--st-*" variables documented in Streamlit's own
bundled skill file (.venv/.../developing-with-streamlit/references/
ccv2-theme-css-variables.md) are for CCv2 custom components specifically,
not the plain markdown/HTML path this app uses. `st.context.theme.type`
does exist, but Streamlit's own docstring for it warns the value "may be
incorrect during a change in theme" -- exactly the moment this fix is
supposed to work correctly -- so it is not used here either.

Given no reliable theme signal is available in Python or via a Streamlit-
supplied CSS variable, colour is switched with `@media (prefers-color-
scheme: dark)`, which tracks the browser/OS preference. .streamlit/
config.toml defines matching [theme.light] and [theme.dark] palettes so
Streamlit's OWN chrome uses the same colours and follows the same signal by
default ("Use system setting") -- the one gap is a user who manually
overrides Streamlit's own theme picker away from their OS preference, which
this CSS cannot see; there is no variable exposed to detect that case
either.
"""

CUSTOM_CSS = """
:root {
    /* Light palette (default). Dark overrides are the exact same
    variables, redefined inside the media query below -- everything that
    references var(--ink)/var(--paper)/etc. adapts with no further changes.
    --rule and --muted are DERIVED from --ink via color-mix rather than
    given their own light/dark pair, so they track whichever ink value is
    active automatically. */
    --ink: #14161A;
    --paper: #FBFBF9;
    --rule: color-mix(in srgb, var(--ink) 17%, transparent);
    --muted: color-mix(in srgb, var(--ink) 60%, var(--paper));

    --permitted: #2F6B4F;
    --blocked: #9B2C2C;
    --conflict: #8A6D1F;

    --font-sans: 'Inter', ui-sans-serif, system-ui, -apple-system, 'Sohne',
        'Helvetica Neue', Arial, sans-serif;
    --font-mono: ui-monospace, 'SF Mono', Menlo, 'Cascadia Mono', monospace;
}

@media (prefers-color-scheme: dark) {
    :root {
        /* Swapped, not two new colours -- --ink and --paper trade places,
        so contrast is symmetric in both modes by construction. */
        --ink: #FBFBF9;
        --paper: #14161A;

        /* Status colours re-tuned lighter so they hold contrast against a
        dark background -- a straight reuse of the light hues reads as too
        dark/muddy once the surface behind them is dark instead of light. */
        --permitted: #6FBF95;
        --blocked: #E08585;
        --conflict: #D9B65C;
    }
}

/* ---- base re-theme ------------------------------------------------- */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
    background: var(--paper) !important;
    color: var(--ink) !important;
    font-family: var(--font-sans) !important;
}
[data-testid="stHeader"] { border-bottom: 1px solid var(--rule); }
.block-container { padding-top: 2.5rem !important; max-width: 920px; }

h1, h2, h3, h4, h5, h6,
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {
    font-family: var(--font-sans) !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em;
    color: var(--ink) !important;
}
p, li, span, label, div { letter-spacing: 0; }

a, a:visited { color: var(--ink); text-decoration: underline; text-decoration-color: var(--rule); }
a:hover { text-decoration-color: var(--ink); }

/* ---- lede / help text ----------------------------------------------- */
.eu-lede { color: var(--muted); font-size: 0.95rem; max-width: 62ch; margin: 0.25rem 0 1.5rem; }
.eu-caption { color: var(--muted); font-size: 0.82rem; }

/* ---- product identity block -------------------------------------------*/
.eu-identity { display: flex; flex-wrap: wrap; gap: 0.3rem 1.25rem; margin: 0.35rem 0 1rem; }
.eu-identity-item { color: var(--muted); font-size: 0.85rem; }
.eu-identity-item strong { color: var(--ink); font-weight: 600; }

/* ---- navigation (Back / New screening) ---------------------------------*/
.eu-nav-row { margin-top: 1.75rem; margin-bottom: 0.25rem; }

/* ---- count strip ---------------------------------------------------- */
.eu-count-strip {
    display: flex;
    flex-wrap: wrap;
    gap: 0;
    border: 1px solid var(--rule);
    margin: 1rem 0 1.75rem;
}
.eu-count-cell {
    flex: 1 1 auto;
    padding: 0.6rem 1rem;
    border-right: 1px solid var(--rule);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    color: var(--muted);
}
.eu-count-cell:last-child { border-right: none; }
.eu-count-cell strong { display: block; font-size: 1.25rem; color: var(--ink); text-transform: none; letter-spacing: 0; }

/* ---- structure -------------------------------------------------------*/
.eu-hairline { border: none; border-top: 1px solid var(--rule); margin: 1.25rem 0; }
.eu-section-title {
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    font-size: 0.78rem;
    color: var(--muted);
    border-bottom: 1px solid var(--rule);
    padding-bottom: 0.4rem;
    margin: 2.25rem 0 1rem;
}
.eu-section-note { color: var(--muted); font-size: 0.85rem; margin: -0.5rem 0 1rem; }

/* ---- code / data typography ------------------------------------------*/
.eu-code, code, .stMarkdown code {
    font-family: var(--font-mono) !important;
    font-size: 0.88em;
    background: transparent !important;
    color: var(--ink) !important;
    padding: 0 !important;
}

/* ---- badges ------------------------------------------------------------*/
.eu-badge {
    display: inline-block;
    border: 1px solid var(--rule);
    color: var(--muted);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 0.05rem 0.4rem;
    margin-left: 0.35rem;
    border-radius: 0;
}
.eu-badge.warn { border-color: var(--conflict); color: var(--conflict); }
.eu-badge.blocked { border-color: var(--blocked); color: var(--blocked); }
.eu-badge.permitted { border-color: var(--permitted); color: var(--permitted); }

/* ---- tables (additives, substitutes, out-of-scope, horizon) -----------*/
.eu-table { width: 100%; border-collapse: collapse; margin: 0.5rem 0 1rem; font-size: 0.85rem; }
.eu-table th, .eu-table td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--rule); }
.eu-table th { color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.02em; }

/* ---- widgets: buttons, inputs, tabs, expanders, radio ------------------*/
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    background: var(--ink) !important;
    color: var(--paper) !important;
    border: 1px solid var(--ink) !important;
    border-radius: 0 !important;
    font-weight: 600 !important;
    letter-spacing: -0.005em;
    padding: 0.5rem 1.1rem !important;
    box-shadow: none !important;
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
    background: var(--paper) !important;
    color: var(--ink) !important;
}
.stButton > button:disabled {
    background: var(--paper) !important;
    color: var(--muted) !important;
    border-color: var(--rule) !important;
}

.stTextInput input, .stTextArea textarea, .stSelectbox [data-baseweb="select"] > div,
[data-testid="stFileUploaderDropzone"] {
    border-radius: 0 !important;
    border: 1px solid var(--rule) !important;
    background: var(--paper) !important;
    color: var(--ink) !important;
    box-shadow: none !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: var(--ink) !important;
    box-shadow: none !important;
}

[data-testid="stTabs"] [data-baseweb="tab-list"] {
    border-bottom: 1px solid var(--rule);
    gap: 1.5rem;
}
[data-testid="stTabs"] button[role="tab"] {
    color: var(--muted);
    font-weight: 600;
    background: transparent;
}
[data-testid="stTabs"] button[aria-selected="true"] {
    color: var(--ink);
    border-bottom: 2px solid var(--ink);
}

[data-testid="stExpander"] {
    border: 1px solid var(--rule) !important;
    border-radius: 0 !important;
    box-shadow: none !important;
    background: var(--paper) !important;
}
[data-testid="stExpander"] summary { font-weight: 600; color: var(--ink); }

div[role="radiogroup"] label { font-weight: 400; color: var(--ink); }

[data-testid="stStatusWidget"], [data-testid="stExpander"] > details {
    border-radius: 0 !important;
}

.stAlert { border-radius: 0 !important; }

hr { border-top: 1px solid var(--rule) !important; }
"""
