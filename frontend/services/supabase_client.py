import os
import logging
import threading
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse
import streamlit as st
from supabase import Client, create_client

logger = logging.getLogger('ats_resume_scorer')


try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / '.env')
except ImportError:
    pass


def _secret(key: str, section: str = 'supabase') -> str:
    """Read from env first, then fall back to st.secrets[section][key]."""
    val = os.getenv(key, '')
    if val:
        return val
    try:
        return st.secrets[section][key]
    except (KeyError, FileNotFoundError, AttributeError):
        return ''


SUPABASE_URL = _secret('SUPABASE_URL')
SUPABASE_ANON_KEY = _secret('SUPABASE_ANON_KEY')

def _is_local_url(url: str) -> bool:
    return (urlparse(url).hostname or '') in ('localhost', '127.0.0.1', '::1', '')


def _detected_app_url() -> str:
    """The URL the user is actually on, or '' when it can't be determined."""
    try:
        return (st.context.url or '').rstrip('/')
    except Exception:
        return ''


def _oauth_redirect_url() -> str:
    """Where the OAuth provider should send the user back.

    A hardcoded localhost fallback silently breaks deployed apps: the provider
    bounces the user to their own machine, so the code exchange happens against
    the wrong app and sign-in never completes. So prefer the URL the user is
    actually on, and only honour a configured localhost value while the app is
    itself being served locally.

    Evaluated per call rather than at import so it reflects the live session.
    """
    detected = _detected_app_url()
    configured = (
        os.getenv('AUTH_REDIRECT_URL')
        or _secret('redirect_uri', 'google_oauth')
        or ''
    ).rstrip('/')

    stale_localhost = _is_local_url(configured) and not _is_local_url(detected)
    if configured and not stale_localhost:
        return configured

    return detected or configured or 'http://localhost:8501'


def _missing_config() -> str | None:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return 'Supabase is not configured — set SUPABASE_URL and SUPABASE_ANON_KEY in .env or .streamlit/secrets.toml'
    return None


# ---------------------------------------------------------------------------
# PKCE state
# ---------------------------------------------------------------------------
# supabase-py holds exactly ONE PKCE code_verifier, in the auth client's
# in-memory storage under '<storage_key>-code-verifier', and writes a brand new
# one every time sign_in_with_oauth() is called (_get_url_for_provider). The
# client below is a process-wide singleton, so that slot is shared by every
# Streamlit session: if two sessions each build a sign-in URL, the second
# silently clobbers the first, and whichever user then comes back from Google
# gets "code challenge does not match previously saved code verifier" — even
# though they only ever clicked one button.
#
# So build the URL once per process, remember which verifier it belongs to, and
# hand that exact verifier to the exchange. The lock keeps this single-flight
# when several sessions render the sign-in page simultaneously.
_oauth_attempt: Dict[str, str] | None = None
_oauth_lock = threading.Lock()


def _verifier_key(client: Client) -> str:
    """Storage key supabase-py uses for the PKCE code_verifier."""
    return f'{client.auth._storage_key}-code-verifier'


def clear_oauth_attempt() -> None:
    """Forget the current sign-in URL so the next call mints a fresh one."""
    global _oauth_attempt
    with _oauth_lock:
        _oauth_attempt = None


@st.cache_resource
def get_client() -> Client | None:
    """Cached singleton — preserves PKCE state across Streamlit reruns."""
    if _missing_config():
        return None
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def _session_dict(session, user) -> Dict[str, Any]:
    return {
        'access_token':  session.access_token,
        'refresh_token': session.refresh_token,
        'user_id':       user.id,
        'email':         user.email,
    }


def sign_in_with_password(email: str, password: str) -> Dict[str, Any]:
    err = _missing_config()
    if err:
        return {'error': err}
    try:
        resp = get_client().auth.sign_in_with_password(
            {'email': email, 'password': password}
        )
        if not resp.session or not resp.user:
            return {'error': 'Invalid credentials'}
        return _session_dict(resp.session, resp.user)
    except Exception as exc:
        logger.warning(f'sign_in_with_password failed: {exc}')
        return {'error': _humanize(exc)}


def sign_up_with_password(email: str, password: str) -> Dict[str, Any]:
    err = _missing_config()
    if err:
        return {'error': err}
    try:
        resp = get_client().auth.sign_up({'email': email, 'password': password})
        if resp.session and resp.user:
            return _session_dict(resp.session, resp.user)
        if resp.user:
            return {'pending_confirmation': True, 'email': email}
        return {'error': 'Sign-up failed'}
    except Exception as exc:
        logger.warning(f'sign_up failed: {exc}')
        return {'error': _humanize(exc)}


def google_oauth_url(force_refresh: bool = False) -> Dict[str, Any]:
    """Build the Google sign-in URL.

    Cached per process, not per session — see the note above the PKCE state
    block. Rebuilding the URL anywhere mints a new code_verifier and breaks the
    challenge embedded in the link the user is about to click, so the URL is
    reused until something consumes or invalidates it.
    """
    err = _missing_config()
    if err:
        return {'error': err}

    global _oauth_attempt
    with _oauth_lock:
        if _oauth_attempt is not None and not force_refresh:
            return {'url': _oauth_attempt['url']}
        try:
            client = get_client()
            resp = client.auth.sign_in_with_oauth({
                'provider': 'google',
                'options': {'redirect_to': _oauth_redirect_url()},
            })
            # Read back the verifier the library just generated for this URL.
            verifier = client.auth._storage.get_item(_verifier_key(client)) or ''
            _oauth_attempt = {'url': resp.url, 'verifier': verifier}
            return {'url': resp.url}
        except Exception as exc:
            logger.warning(f'oauth url generation failed: {exc}')
            return {'error': _humanize(exc)}


def exchange_code_for_session(auth_code: str) -> Dict[str, Any]:
    """Called once after the OAuth provider redirects back with `?code=...`."""
    err = _missing_config()
    if err:
        return {'error': err}
    client = get_client()

    # Exchange against the verifier that belongs to the URL we handed out,
    # rather than whatever happens to be in the library's shared storage.
    with _oauth_lock:
        verifier = (_oauth_attempt or {}).get('verifier', '')
    if not verifier:
        verifier = client.auth._storage.get_item(_verifier_key(client)) or ''

    try:
        resp = client.auth.exchange_code_for_session({
            'auth_code': auth_code,
            'code_verifier': verifier,
            'redirect_to': _oauth_redirect_url(),
        })
        if not resp.session or not resp.user:
            clear_oauth_attempt()
            return {'error': 'OAuth exchange returned no session'}
        # The verifier is spent and the auth code consumed, so start clean —
        # the next sign-in must get a fresh challenge.
        clear_oauth_attempt()
        return _session_dict(resp.session, resp.user)
    except Exception as exc:
        logger.warning(f'exchange_code_for_session failed: {exc}')
        # Drop the attempt so the sign-in button re-mints a URL. A failure is
        # most often a stale link (e.g. one issued before a redeploy) and the
        # user just needs to click the freshly rendered button.
        clear_oauth_attempt()
        return {'error': _humanize(exc)}


def sign_out() -> None:
    if _missing_config():
        return
    try:
        get_client().auth.sign_out()
    except Exception as exc:
        logger.warning(f'sign_out failed: {exc}')
    finally:
        clear_oauth_attempt()


def _humanize(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    # supabase errors arrive as "<status>: {json blob}" — surface the human bit
    if 'code challenge does not match' in low or 'code verifier should be non-empty' in low:
        # Almost always a link issued before a redeploy/restart, or one that was
        # superseded. The button below has already been re-minted.
        return ('That Google sign-in link is no longer valid — click '
                '"Continue with Google" again to start a fresh sign-in.')
    if 'invalid_grant' in low or 'invalid login' in low:
        return 'Wrong email or password'
    if 'user already registered' in low or 'already been registered' in low:
        return 'An account with this email already exists — try signing in'
    if 'password should be at least' in low:
        return 'Password too short (Supabase default is 6 characters)'
    return msg