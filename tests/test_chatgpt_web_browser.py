"""Opt-in REAL Chrome against a local DOM fixture; ALL network is intercepted.
No ChatGPT account, request, credential, or generation quota is used.
Run: DRAW_BROWSER_TESTS=1 python -m pytest tests/test_chatgpt_web_browser.py -q
"""
import base64
import io
import os
from pathlib import Path
import pytest
from PIL import Image
from draw_cli import chatgpt_web as web

pytestmark = pytest.mark.skipif(os.environ.get("DRAW_BROWSER_TESTS") != "1", reason="opt-in local browser contract")

@pytest.fixture
def page(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(tmp_path / "fixture-profile"), channel="chrome", headless=True, accept_downloads=True)
        context.route("**/*", lambda route: route.abort())
        page = context.new_page()
        page.set_default_timeout(3000)
        yield page
        context.close()

def fixture(page, *, quota=False, guest=False):
    image = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(image, format="PNG")
    data = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
    response = "You have reached the image generation limit" if quota else (
        '<img alt="Generated image" src="' + data + '"><button onclick="saveImage()">Download image</button>')
    account = '<button>Log in</button>' if guest else '<button data-testid="accounts-profile-button">Account</button>'
    html = """<html><body>ACCOUNT
<div id="prompt-textarea" contenteditable="true"></div><main></main>
<script>
window.sends = 0;
function saveImage(){const a=document.createElement('a');a.href='DATA';a.download='original.png';document.body.append(a);a.click();a.remove();}
document.querySelector('#prompt-textarea').addEventListener('input',()=>{
 if(document.querySelector('[data-testid="send-button"]'))return;
 const b=document.createElement('button');b.dataset.testid='send-button';b.textContent='Send';
 b.onclick=()=>{window.sends++;const r=document.createElement('div');r.setAttribute('data-message-author-role','assistant');r.innerHTML=RESPONSE;document.querySelector('main').append(r);};document.body.append(b);
});
</script></body></html>"""
    import json
    html=html.replace("ACCOUNT", account).replace("DATA",data).replace("RESPONSE",json.dumps(response))
    page.route("https://chatgpt.com/", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto("https://chatgpt.com/")


def test_submit_once_original_download_and_jpeg(page, tmp_path):
    fixture(page)
    assert web.readiness(page) == "ready"
    raw, target = tmp_path / "original", tmp_path / "plane.jpg"
    web.download_image(page, "airplane made by apple", raw, 5)
    web.save_download(raw, target)
    assert page.evaluate("window.sends") == 1
    with Image.open(target) as result:
        assert result.format == "JPEG" and result.size == (256, 256)


def test_quota_stops_without_another_submission(page, tmp_path):
    fixture(page, quota=True)
    with pytest.raises(web.BrowserError, match="usage limit"):
        web.download_image(page, "airplane", tmp_path / "original", 5)
    assert page.evaluate("window.sends") == 1
    assert not (tmp_path / "original").exists()


def test_guest_composer_is_not_authenticated(page):
    fixture(page, guest=True)
    assert web.readiness(page) == "signin_required"
    with pytest.raises(web.BrowserError, match="sign-in required"):
        web.wait_ready(page, interactive=False, timeout=1)
    assert page.evaluate("window.sends") == 0


def test_title_only_human_check_is_not_misreported_as_login(page):
    page.route("https://chatgpt.com/", lambda route: route.fulfill(status=200, content_type="text/html", body="<title>Just a moment...</title><body></body>"))
    page.goto("https://chatgpt.com/")
    assert web.readiness(page) == "challenge_required"
    with pytest.raises(web.BrowserError) as exc:
        web.wait_ready(page, interactive=False, timeout=1)
    assert exc.value.code == 4
    assert "--headed" in str(exc.value)
