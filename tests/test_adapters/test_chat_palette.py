"""채팅 Palette 및 설계 명세 동기화 테스트.

T-1: 채팅 패널 파일 존재 확인 (Fusion AddIns 경로가 아닌 프로젝트 소스 기준)
T-2: FusionBridge /chat 라우트 등록 확인
T-5: DesignSpec 동기화 (pipeline/design_spec.py 단위 테스트)
"""

import json
import inspect
from pathlib import Path

import pytest


# ─── T-1: 채팅 팔레트 HTML 파일 (프로젝트 소스) ───


class TestChatPaletteFiles:
    """채팅 패널 HTML/CSS/JS 파일이 프로젝트에 존재하는지 확인한다."""

    ADDIN_DIR = Path(__file__).parent.parent.parent / "adapters" / "fusion360" / "addin"

    def test_chat_palette_dir_exists(self):
        assert (self.ADDIN_DIR / "chat_palette").is_dir()

    def test_index_html_exists(self):
        path = self.ADDIN_DIR / "chat_palette" / "index.html"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "AI 설계 어시스턴트" in content

    def test_style_css_exists(self):
        path = self.ADDIN_DIR / "chat_palette" / "style.css"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "#chat-history" in content
        assert ".message.user" in content
        assert ".message.assistant" in content

    def test_script_js_exists(self):
        path = self.ADDIN_DIR / "chat_palette" / "script.js"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "BRIDGE_URL" in content
        assert "/chat" in content

    def test_html_references_external_assets(self):
        """HTML이 외부 CSS/JS 파일을 참조하는지 확인."""
        path = self.ADDIN_DIR / "chat_palette" / "index.html"
        content = path.read_text(encoding="utf-8")
        assert "style.css" in content
        assert "script.js" in content

    def test_css_has_ui_features(self):
        """CSS에 UI 개선 요소 (스피너, 배지, 에러) 가 있는지 확인."""
        path = self.ADDIN_DIR / "chat_palette" / "style.css"
        content = path.read_text(encoding="utf-8")
        assert ".spinner" in content
        assert ".badge.pass" in content
        assert ".badge.fail" in content
        assert ".badge.warning" in content
        assert ".error" in content

    def test_js_has_ui_features(self):
        """JS에 스피너, 에러 처리, 입력 비활성화가 있는지 확인."""
        path = self.ADDIN_DIR / "chat_palette" / "script.js"
        content = path.read_text(encoding="utf-8")
        assert "showSpinner" in content
        assert "removeSpinner" in content
        assert "userInput.disabled" in content


# ─── T-2: FusionBridge /chat 라우트 ───


class TestChatEndpointRegistration:
    """FusionBridge HTTP 핸들러에 /chat 라우트가 있는지 확인."""

    def test_bridge_http_handler_has_chat_route(self):
        """BridgeHTTPHandler.do_POST에 /chat 경로가 있는지 소스 코드로 확인."""
        from adapters.fusion360.addin.FusionBridge import BridgeHTTPHandler

        source = inspect.getsource(BridgeHTTPHandler.do_POST)
        assert '"/chat"' in source

    def test_bridge_has_handle_chat_method(self):
        """BridgeHTTPHandler에 _handle_chat 메서드가 있는지 확인."""
        from adapters.fusion360.addin.FusionBridge import BridgeHTTPHandler

        assert hasattr(BridgeHTTPHandler, "_handle_chat")

    def test_bridge_has_cors_headers(self):
        """BridgeHTTPHandler._send_json에 CORS 헤더가 있는지 확인."""
        from adapters.fusion360.addin.FusionBridge import BridgeHTTPHandler

        source = inspect.getsource(BridgeHTTPHandler._send_json)
        assert "Access-Control-Allow-Origin" in source

    def test_bridge_has_options_handler(self):
        """BridgeHTTPHandler.do_OPTIONS가 있는지 확인."""
        from adapters.fusion360.addin.FusionBridge import BridgeHTTPHandler

        assert hasattr(BridgeHTTPHandler, "do_OPTIONS")


# ─── 오케스트레이터 웹 모드 ───


class TestOrchestratorWebMode:
    """orchestrator/core.py의 --web 모드 함수가 존재하는지 확인."""

    def test_run_web_server_function_exists(self):
        from orchestrator.core import _run_web_server
        assert callable(_run_web_server)

    def test_run_terminal_function_exists(self):
        from orchestrator.core import _run_terminal
        assert callable(_run_terminal)
