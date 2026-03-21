/**
 * AI 설계 어시스턴트 — 채팅 Palette 클라이언트
 *
 * Fusion 360 HTML Palette 내에서 실행되며,
 * FusionBridge HTTP 서버(127.0.0.1:18080)와 통신한다.
 */

(function () {
    "use strict";

    var BRIDGE_URL = "http://127.0.0.1:18080";

    var chatHistory = document.getElementById("chat-history");
    var userInput = document.getElementById("user-input");
    var sendBtn = document.getElementById("send-btn");
    var statusDot = document.getElementById("connection-status");

    var isProcessing = false;

    // ─── 메시지 추가 ───

    function addMessage(role, content) {
        var div = document.createElement("div");
        div.className = "message " + role;
        div.innerHTML = formatContent(content);
        chatHistory.appendChild(div);
        chatHistory.scrollTop = chatHistory.scrollHeight;
        return div;
    }

    function formatContent(text) {
        if (typeof text !== "string") {
            text = JSON.stringify(text, null, 2);
        }

        // pass/fail 배지
        text = text.replace(/\u2705\s*/g, '<span class="badge pass">PASS</span> ');
        text = text.replace(/\u26a0\ufe0f\s*/g, '<span class="badge warning">WARNING</span> ');
        text = text.replace(/\u274c\s*/g, '<span class="badge fail">FAIL</span> ');

        // 코드 블록 (``` 또는 계획 블록)
        text = text.replace(/```([\s\S]*?)```/g, function (_, code) {
            return '<div class="plan-block">' + escapeHtml(code.trim()) + "</div>";
        });

        // 간단 이스케이프 (배지/코드블록 제외)
        // 이미 HTML을 삽입했으므로 나머지 부분만 처리
        return text;
    }

    function escapeHtml(str) {
        var div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    function showSpinner(text) {
        var container = document.createElement("div");
        container.className = "spinner-container";
        container.id = "loading-spinner";
        container.innerHTML =
            '<div class="spinner"></div>' +
            '<span class="spinner-text">' + escapeHtml(text || "처리 중...") + "</span>";
        chatHistory.appendChild(container);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function removeSpinner() {
        var spinner = document.getElementById("loading-spinner");
        if (spinner) spinner.remove();
    }

    // ─── 재시도 선택 버튼 (루프 최대 반복 도달 시) ───

    function showRetryButtons(messageDiv) {
        var btns = document.createElement("div");
        btns.className = "approval-buttons";

        var retryBtn = document.createElement("button");
        retryBtn.className = "approve";
        retryBtn.textContent = "계속 시도";
        retryBtn.onclick = function () {
            btns.remove();
            sendMessage("계속 시도해줘");
        };

        var skipBtn = document.createElement("button");
        skipBtn.textContent = "건너뛰기";
        skipBtn.onclick = function () {
            btns.remove();
            sendMessage("이 작업은 건너뛰고 다음 진행해줘");
        };

        var stopBtn = document.createElement("button");
        stopBtn.className = "cancel";
        stopBtn.textContent = "중단";
        stopBtn.onclick = function () {
            btns.remove();
            sendMessage("작업 중단해줘");
        };

        btns.appendChild(retryBtn);
        btns.appendChild(skipBtn);
        btns.appendChild(stopBtn);
        messageDiv.appendChild(btns);
    }

    // ─── 승인 버튼 ───

    function showApprovalButtons(messageDiv) {
        var btns = document.createElement("div");
        btns.className = "approval-buttons";

        var approveBtn = document.createElement("button");
        approveBtn.className = "approve";
        approveBtn.textContent = "승인";
        approveBtn.onclick = function () {
            btns.remove();
            sendMessage("승인");
        };

        var cancelBtn = document.createElement("button");
        cancelBtn.className = "cancel";
        cancelBtn.textContent = "취소";
        cancelBtn.onclick = function () {
            btns.remove();
            sendMessage("취소");
        };

        var modifyBtn = document.createElement("button");
        modifyBtn.textContent = "파라미터 변경";
        modifyBtn.onclick = function () {
            btns.remove();
            userInput.focus();
            userInput.placeholder = "변경할 파라미터를 입력하세요...";
        };

        btns.appendChild(approveBtn);
        btns.appendChild(cancelBtn);
        btns.appendChild(modifyBtn);
        messageDiv.appendChild(btns);
    }

    // ─── 선택 정보 ───

    function getSelectionInfo() {
        return fetch(BRIDGE_URL + "/selection")
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (data.selection && data.selection.length > 0) {
                    return data.selection;
                }
                return null;
            })
            .catch(function () { return null; });
    }

    var _TYPE_LABELS = {
        "BRepFace": "면",
        "BRepEdge": "엣지",
        "BRepBody": "바디",
        "BRepVertex": "꼭짓점"
    };

    function updateSelectionDisplay(selection) {
        var existing = document.getElementById("selection-info");
        if (existing) existing.remove();

        if (!selection || selection.length === 0) return;

        // 타입별 카운트
        var counts = {};
        selection.forEach(function (item) {
            var t = item.type || "기타";
            counts[t] = (counts[t] || 0) + 1;
        });

        var parts = [];
        Object.keys(counts).forEach(function (t) {
            var label = _TYPE_LABELS[t] || t;
            parts.push(label + " " + counts[t] + "개");
        });

        var div = document.createElement("div");
        div.className = "selection-info";
        div.id = "selection-info";
        div.innerHTML = '<span class="tag">' + escapeHtml(parts.join(", ") + " 선택됨") + "</span>";
        var inputArea = document.getElementById("input-area");
        inputArea.parentNode.insertBefore(div, inputArea);
    }

    // ─── 메시지 전송 ───

    function sendMessage(text) {
        if (!text || !text.trim() || isProcessing) return;

        text = text.trim();
        addMessage("user", text);
        userInput.value = "";
        userInput.placeholder = "설계 요청을 입력하세요...";
        isProcessing = true;
        sendBtn.disabled = true;
        userInput.disabled = true;
        userInput.placeholder = "처리 중...";

        showSpinner("AI가 처리 중...");

        // 선택 정보 포함하여 전송
        getSelectionInfo().then(function (selection) {
            var payload = { message: text };
            if (selection) {
                payload.selection = selection;
            }

            return fetch(BRIDGE_URL + "/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
        })
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            removeSpinner();

            var responseText = data.response || data.error || "응답을 받지 못했습니다.";
            var isError = data.status === "error" || (!data.response && data.error);
            var msgDiv = addMessage("assistant", responseText);
            if (isError) {
                msgDiv.classList.add("error");
            }

            // 루프 최대 반복 도달 패턴 감지 → 재시도/건너뛰기/중단 버튼
            var isRetryPrompt = (
                responseText.indexOf("완료되지 않았습니다") !== -1 &&
                responseText.indexOf("계속 시도") !== -1
            );
            if (isRetryPrompt) {
                showRetryButtons(msgDiv);
            }

            // 계획/확인 패턴 감지 시 승인 버튼 표시
            var planPatterns = [
                "계획", "진행할까", "실행할까", "할까요", "할게요",
                "만들겠습니다", "시작하겠습니다", "시작할까",
                "생성하겠습니다", "적용하겠습니다", "적용할까",
                "실행하겠습니다", "해석하겠습니다",
                "맞으면", "맞는지", "확인해주세요",
                "말씀해주세요", "말씀해 주세요",
            ];
            var hasPlan = planPatterns.some(function (p) {
                return responseText.indexOf(p) !== -1;
            });
            if (hasPlan && !isRetryPrompt) {
                showApprovalButtons(msgDiv);
            }

            // 중간 결과 표시
            if (data.tool_results && data.tool_results.length > 0) {
                data.tool_results.forEach(function (tr) {
                    var status = tr.status === "success" ? "pass" : "fail";
                    var badge = '<span class="badge ' + status + '">' +
                        (status === "pass" ? "PASS" : "FAIL") + "</span>";
                    addMessage("assistant", badge + " " + (tr.description || tr.action || ""));
                });
            }
        })
        .catch(function (err) {
            removeSpinner();
            var errDiv = addMessage("assistant", "통신 오류: " + err.message);
            errDiv.classList.add("error");
        })
        .finally(function () {
            isProcessing = false;
            sendBtn.disabled = false;
            userInput.disabled = false;
            userInput.placeholder = "설계 요청을 입력하세요...";
            userInput.focus();
        });
    }

    // ─── 연결 상태 확인 ───

    function checkConnection() {
        fetch(BRIDGE_URL + "/health")
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (data.fusion_running) {
                    statusDot.className = "status-dot connected";
                    statusDot.title = "연결됨";
                } else {
                    statusDot.className = "status-dot disconnected";
                    statusDot.title = "Fusion 미연결";
                }
            })
            .catch(function () {
                statusDot.className = "status-dot disconnected";
                statusDot.title = "서버 연결 실패";
            });
    }

    // ─── 이벤트 바인딩 ───

    sendBtn.addEventListener("click", function () {
        sendMessage(userInput.value);
    });

    userInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage(userInput.value);
        }
    });

    // 자동 높이 조절
    userInput.addEventListener("input", function () {
        this.style.height = "auto";
        this.style.height = Math.min(this.scrollHeight, 120) + "px";
    });

    // Fusion 선택 변경 폴링
    setInterval(function () {
        if (!isProcessing) {
            getSelectionInfo().then(updateSelectionDisplay);
        }
    }, 2000);

    // 연결 상태 주기적 확인
    checkConnection();
    setInterval(checkConnection, 10000);

    // 초기 메시지
    addMessage("assistant",
        "안녕하세요! AI 설계 어시스턴트입니다.\n\n" +
        "모델링, 시뮬레이션, 최적화 요청을 입력해주세요.\n" +
        "Fusion에서 면/엣지를 선택한 상태로 요청하면 선택된 객체를 기반으로 작업합니다."
    );
})();
