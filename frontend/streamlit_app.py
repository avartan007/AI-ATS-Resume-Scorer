import streamlit as st
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

# Put the repo root on sys.path so `from frontend.views import ...` resolves
# regardless of the directory streamlit was launched from.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _import_frontend(module_name: str):
    """Import one of our own submodules, surviving the watcher's eviction race.

    Streamlit's source-file watcher evicts modules from the *process-global*
    ``sys.modules``. It defers the removal to the start of a script run so the
    watcher thread can't mutate ``sys.modules`` mid-import — but each browser
    session runs that flush on its own thread, so with two sessions one can pop
    a module while the other is importing it. importlib then dies with a bare
    ``KeyError: 'frontend'`` from ``sys.modules.pop(spec.name)``.

    ``server.fileWatcherType = "none"`` (see .streamlit/config.toml) prevents it
    outright. This is a belt-and-braces retry for when that setting doesn't
    stick; after an eviction the module is simply gone, so a fresh import
    rebuilds it. Only KeyErrors naming our own package are retried, so a
    genuine bug elsewhere still surfaces immediately.
    """
    import importlib

    last_error: KeyError | None = None
    for _ in range(3):
        try:
            return importlib.import_module(module_name)
        except KeyError as exc:
            key = exc.args[0] if exc.args else None
            if not (isinstance(key, str) and key.startswith("frontend")):
                raise
            last_error = exc
    raise last_error


# ---------------------------------------------------------------------------
# Backend bootstrap
# ---------------------------------------------------------------------------
# Streamlit Community Cloud only runs this one process, so the FastAPI backend
# has to be started from here. On Streamlit Cloud we therefore spawn uvicorn
# ourselves and talk to it over 127.0.0.1.
#
# Locally, if you already started `uvicorn backend.main:app --port 8000`, the
# port is occupied, this detects it and does nothing extra.
DEFAULT_BACKEND_PORT = 8000
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _resolve_backend():
    """Return (base_url, local_port).

    ``local_port`` is None when the backend is hosted elsewhere, in which case
    this app must not try to spawn its own uvicorn child.
    """
    if os.getenv("BACKEND_URL"):
        url = os.getenv("BACKEND_URL")
    else:
        try:
            url = st.secrets["backend"]["url"]
        except (KeyError, FileNotFoundError):
            url = None

    if not url:
        port = int(os.getenv("BACKEND_PORT", DEFAULT_BACKEND_PORT))
        return f"http://127.0.0.1:{port}", port

    parsed = urlparse(url)
    if parsed.hostname in _LOCAL_HOSTS and parsed.port:
        return url, parsed.port
    return url, None


BACKEND_URL, BACKEND_PORT = _resolve_backend()

# Cold start downloads the embedding model and loads the NLP pipeline, so the
# first launch can take several minutes. Generous by default (and overridable
# for slow containers) — reporting a dead backend after too short a wait is
# worse than making the user wait a little longer.
BACKEND_STARTUP_TIMEOUT = int(os.getenv("BACKEND_STARTUP_TIMEOUT", "360"))


def _port_is_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _backend_ready() -> bool:
    try:
        return requests.get(f"{BACKEND_URL.rstrip('/')}/api/v1/health", timeout=3).ok
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# The child's output goes to a file (not DEVNULL) so a failed boot can be
# surfaced in the UI and in the Streamlit Cloud logs instead of failing silently.
BACKEND_LOG = os.path.join(tempfile.gettempdir(), f"ats-backend-{BACKEND_PORT}.log")


def _backend_log_tail(lines: int = 40) -> str:
    """Last meaningful lines of the backend log.

    Streamlit's own "missing ScriptRunContext" chatter is dropped so an actual
    traceback stays readable.
    """
    noise = (
        "missing ScriptRunContext",
        "Session state does not function when running a script",
        "to view a Streamlit app on a browser",
    )
    try:
        with open(BACKEND_LOG, "r", errors="replace") as fh:
            kept = [ln for ln in fh.readlines() if not any(n in ln for n in noise)]
    except OSError:
        return "(no backend log captured)"
    tail = [ln for ln in kept[-lines:] if ln.strip()]
    return "".join(tail).strip() or "(backend produced no output)"


def _ensure_backend() -> None:
    """Start the FastAPI backend in-process if nothing is listening yet.

    A lock file holding the child's PID stops Streamlit re-runs from spawning
    duplicate uvicorn processes while the first one is still loading its models,
    while still allowing a respawn if that child ever dies.
    """
    if BACKEND_PORT is None or _port_is_taken(BACKEND_PORT):
        return

    lock = os.path.join(tempfile.gettempdir(), f"ats-backend-{BACKEND_PORT}.lock")
    if os.path.exists(lock):
        try:
            if _pid_alive(int(open(lock).read().strip())):
                return
        except (OSError, ValueError):
            pass

    # cwd must be the repo root: the child does NOT inherit this file's
    # sys.path fix above, so `backend.main` only resolves if uvicorn starts in
    # the directory that contains the `backend` package.
    log = open(BACKEND_LOG, "ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=str(REPO_ROOT),
    )
    log.close()  # the child keeps its own copy of the descriptor
    with open(lock, "w") as fh:
        fh.write(str(proc.pid))


_ensure_backend()

# Configure page
st.set_page_config(
    page_title="ATS Resume Scorer",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Auth state. Populated by Supabase sign-in / sign-up / OAuth.
# All four are None when signed out, all four are set when signed in.
for key, default in [
    ("access_token", None),
    ("refresh_token", None),
    ("user_id", None),       # Supabase auth user id (uuid); also used by api_client
    ("user_email", None),
    ("auth_error", None),
    ("auth_info", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# If we just came back from Google OAuth, Supabase appends `?code=<authcode>`
# to the redirect URL. Exchange it for a session before rendering anything.
if (
    not st.session_state.access_token
    and "code" in st.query_params
):
    supabase_client = _import_frontend("frontend.services.supabase_client")
    result = supabase_client.exchange_code_for_session(st.query_params["code"])

    #Always clear the ?code= param so a refresh doesn't try to re-exchange.
    st.query_params.clear()
    if "error" in result:
        st.session_state.auth_error = f"Google sign-in failed: {result['error']}"
    else:
        st.session_state.access_token  = result["access_token"]
        st.session_state.refresh_token = result["refresh_token"]
        st.session_state.user_id       = result["user_id"]
        st.session_state.user_email    = result["email"]
        st.rerun()

#Load custom CSS
def load_css():
    try:
        css_path = Path(__file__).parent / 'assets' / 'styles.css'
        with open(css_path, 'r') as f:
            return f'<style>{f.read()}</style>'
    except FileNotFoundError:
        return ''

st.markdown(load_css(), unsafe_allow_html=True)

# Initialize session state for view management
if 'current_view' not in st.session_state:
    st.session_state.current_view = 'landing'

# Sidebar navigation
with st.sidebar:
    st.markdown("## Navigation")
    
    if st.button("🏠 Home", use_container_width=True):
        st.session_state.current_view = 'landing'
        st.rerun()
    
    if st.button("🎯 ATS Scorer", use_container_width=True):
        st.session_state.current_view = 'scorer'
        st.rerun()
    
    if st.button("📊 History", use_container_width=True):
        st.session_state.current_view = 'history'
        st.rerun()
    
    if st.button("📚 Resources", use_container_width=True):
        st.session_state.current_view = 'resources'
        st.rerun()
    
    st.markdown("---")
    st.markdown("### 👤 Account")

    supabase_client = _import_frontend("frontend.services.supabase_client")

    if st.session_state.access_token:
        # Signed-in state: show email + sign-out button.
        st.caption(f"Signed in as **{st.session_state.user_email}**")
        if st.button("Sign out", use_container_width=True):
            supabase_client.sign_out()
            for k in ("access_token", "refresh_token", "user_id", "user_email"):
                st.session_state[k] = None
            st.rerun()
    else:
        # Signed-out state: tabs for sign-in vs sign-up + Google OAuth button.
        if st.session_state.auth_error:
            st.error(st.session_state.auth_error)
            st.session_state.auth_error = None
        if st.session_state.auth_info:
            st.info(st.session_state.auth_info)
            st.session_state.auth_info = None

        tab_in, tab_up = st.tabs(["Sign in", "Sign up"])

        with tab_in:
            with st.form("signin_form", clear_on_submit=False):
                email = st.text_input("Email", key="signin_email")
                password = st.text_input("Password", type="password", key="signin_pw")
                submitted = st.form_submit_button("Sign in", use_container_width=True)
            if submitted:
                result = supabase_client.sign_in_with_password(email, password)
                if "error" in result:
                    st.session_state.auth_error = result["error"]
                else:
                    st.session_state.access_token  = result["access_token"]
                    st.session_state.refresh_token = result["refresh_token"]
                    st.session_state.user_id       = result["user_id"]
                    st.session_state.user_email    = result["email"]
                st.rerun()

        with tab_up:
            with st.form("signup_form", clear_on_submit=False):
                email_up = st.text_input("Email", key="signup_email")
                password_up = st.text_input("Password (min 6 chars)", type="password", key="signup_pw")
                submitted_up = st.form_submit_button("Create account", use_container_width=True)
            if submitted_up:
                result = supabase_client.sign_up_with_password(email_up, password_up)
                if "error" in result:
                    st.session_state.auth_error = result["error"]
                elif result.get("pending_confirmation"):
                    st.session_state.auth_info = (
                        f"Check your inbox — confirmation email sent to {result['email']}."
                    )
                else:
                    st.session_state.access_token  = result["access_token"]
                    st.session_state.refresh_token = result["refresh_token"]
                    st.session_state.user_id       = result["user_id"]
                    st.session_state.user_email    = result["email"]
                st.rerun()

        st.markdown("<div style='text-align:center; margin: 8px 0; color:#94a3b8;'>or</div>",
                    unsafe_allow_html=True)

        # Cached per process, not per session: every session shares one Supabase
        # client, hence one PKCE verifier slot. Minting a URL here would clobber
        # the challenge of a link another session is already showing. See the
        # PKCE note in frontend/services/supabase_client.py.
        oauth = supabase_client.google_oauth_url()
        if "error" in oauth:
            st.caption(f"Google sign-in unavailable: {oauth['error']}")
        else:
            st.link_button(
                "Continue with Google",
                url=oauth["url"],
                use_container_width=True,
            )

# Wait for the backend if it was just spawned (cold start downloads the
# embedding model and loads the NLP pipeline). On Streamlit Cloud this is the
# only process, so the backend is the uvicorn child we kicked off above.
if not _backend_ready():
    with st.spinner(
        "Starting the scoring engine — the first launch downloads the AI models, "
        "which takes a couple of minutes…"
    ):
        status = st.empty()
        started = time.time()
        while True:
            if _backend_ready():
                break
            waited = time.time() - started
            if waited >= BACKEND_STARTUP_TIMEOUT:
                status.empty()
                if BACKEND_PORT is None:
                    # Nothing was spawned: an external backend is configured.
                    st.error(
                        f"**No backend is answering at** `{BACKEND_URL}`.\n\n"
                        "This app is pointed at an *external* backend — a `[backend] "
                        "url` secret or a `BACKEND_URL` env var — rather than the one "
                        "bundled with it, and that server is not responding. This is "
                        "usually a Render service that is still deploying or has "
                        "crashed; deploy it (see the Render section of the README), "
                        "or remove the `[backend]` secret so the app runs its own "
                        "backend inside this container."
                    )
                    st.stop()
                st.error(
                    f"The backend process never became healthy after {int(waited)}s. "
                    "Its last lines are below. On the free-tier container this is "
                    "usually out-of-memory — a `Killed` line while loading the AI "
                    "models — see the 'Splitting the backend out' section of the "
                    "README. Paste the log below when reporting it."
                )
                st.code(_backend_log_tail(), language="log")
                st.stop()
            status.caption(f"Warming up… {int(waited)}s elapsed")
            time.sleep(2)
        status.empty()

# Main content area - render based on current view
if st.session_state.current_view == 'landing':
    # Import and render landing page
    landing = _import_frontend("frontend.views.landing")
    landing.render()

elif st.session_state.current_view == 'scorer':
    # Import and render scorer page
    scorer = _import_frontend("frontend.views.scorer")
    scorer.render()

elif st.session_state.current_view == 'history':
    # Import and render history page
    history = _import_frontend("frontend.views.history")
    history.render()

elif st.session_state.current_view == 'resources':
    # Import and render resources page
    resources = _import_frontend("frontend.views.resources")
    resources.render()
