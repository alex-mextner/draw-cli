"""Real Chrome, isolated throwaway test profile, all requests intercepted.
Never attach to a user browser here. DRAW_BROWSER_TESTS=1 opts into local Chrome.
"""
import os
from pathlib import Path
import pytest
from PIL import Image
from draw_cli import app_session, chatgpt_web as web
from test_chatgpt_web_browser import fixture

pytestmark = pytest.mark.skipif(os.environ.get("DRAW_BROWSER_TESTS") != "1", reason="opt-in local Chrome contract")

@pytest.fixture
def external(tmp_path):
    from playwright.sync_api import sync_playwright
    profile=tmp_path/'fixture-browser'
    with sync_playwright() as p:
        context=p.chromium.launch_persistent_context(str(profile),channel="chrome",headless=True,
            accept_downloads=True,args=["--remote-debugging-port=0"])
        context.route("**/*",lambda route:route.abort())
        original=context.new_page()
        original.set_content('<h1>Keep this original tab unchanged</h1>')
        # The test creates this directory; it contains no user login/session.
        port=int((profile/'DevToolsActivePort').read_text().splitlines()[0])
        yield p,context,original,f'http://127.0.0.1:{port}'
        context.close()


def test_attach_disconnect_preserves_original_browser_and_tabs(external,tmp_path):
    p,owner,original,endpoint=external
    before=len(owner.pages)
    with app_session.attached_context(p,endpoint,artifacts=tmp_path) as borrowed:
        page=borrowed.new_page()
        page.set_content('<h1>Owned temporary tab</h1>')
        page.close()
    assert not original.is_closed()
    assert original.locator('h1').inner_text()=='Keep this original tab unchanged'
    assert len(owner.pages)==before
    assert app_session.discover(endpoint).startswith('ws://127.0.0.1:')


def test_attached_original_download_encodes_real_jpeg(external,tmp_path):
    p,owner,original,endpoint=external
    with web.browser_context(p,cdp_url=endpoint) as context:
        page=context.new_page()
        try:
            fixture(page)
            # Original download deliberately differs from the displayed preview.
            import io, base64
            buf=io.BytesIO()
            Image.new('RGB',(512,384),'white').save(buf,format='PNG')
            original_data='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
            page.evaluate("data => {window.saveImage=()=>{const a=document.createElement('a');a.href=data;a.download='original.png';a.click();};}",original_data)
            raw=tmp_path/'download'
            web.download_image(page,'airplane made by apple',raw,5,attached=True)
            web.save_download(raw,tmp_path/'airplane.jpg')
            assert page.evaluate('window.sends')==1
        finally:
            page.close()
    with Image.open(tmp_path/'airplane.jpg') as image:
        assert image.format=='JPEG' and image.size==(512,384)
    assert not original.is_closed()


def test_two_attachments_own_independent_tabs(external,tmp_path):
    p,owner,original,endpoint=external
    with app_session.attached_context(p,endpoint,artifacts=tmp_path/'one') as a:
        with app_session.attached_context(p,endpoint,artifacts=tmp_path/'two') as b:
            first,second=a.new_page(),b.new_page()
            first.set_content('<h1>First</h1>')
            second.set_content('<h1>Second</h1>')
            second.close()
        assert not first.is_closed()
        assert first.locator('h1').inner_text()=='First'
        assert not original.is_closed()
        first.close()
    assert not original.is_closed()


def test_unsupported_download_fails_without_clicking_again_and_restores_page(external,tmp_path):
    from draw_cli.ui_download import download_original_link
    p,owner,original,endpoint=external
    with web.browser_context(p,cdp_url=endpoint) as context:
        page=context.new_page()
        try:
            page.set_content("<button onclick='window.clicks++'>Download</button>")
            page.evaluate("() => {window.clicks=0;window.savedClick=HTMLAnchorElement.prototype.click;}")
            with pytest.raises(Exception):
                download_original_link(page,page.get_by_role('button'),tmp_path/'none',timeout_ms=300)
            assert page.evaluate('window.clicks')==1
            assert page.evaluate('window.savedClick===HTMLAnchorElement.prototype.click')
            assert page.evaluate("Object.keys(window).filter(k=>k.startsWith('__draw_original_')).length")==0
            assert not (tmp_path/'none').exists()
        finally:page.close()


def test_foreign_download_link_not_requested(external,tmp_path):
    from draw_cli.ui_download import download_original_link
    p,owner,original,endpoint=external
    with web.browser_context(p,cdp_url=endpoint) as context:
        page=context.new_page()
        requests=[]
        try:
            page.route('https://unrelated.invalid/**',lambda route: (requests.append(1),route.abort()))
            page.set_content("<button>Download</button>")
            page.evaluate("() => {document.querySelector('button').onclick=()=>{const a=document.createElement('a');a.href='https://unrelated.invalid/file.png';a.download='file.png';a.click();};}")
            with pytest.raises(web.BrowserError,match='supported original'):
                download_original_link(page,page.get_by_role('button'),tmp_path/'none',timeout_ms=1000)
            assert requests==[] and not (tmp_path/'none').exists()
        finally:page.close()
