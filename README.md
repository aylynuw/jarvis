# Jarvis

맥에서 돌아가는 음성 비서예요. "Hey Jarvis"라고 부르면 화면 위에서 HUD 패널이 내려오고, 말로 물어보면 Claude Code가 답해요.

- 학교 시간표와 급식 (NEIS 공개 API)
- Gmail 읽기와 초안 쓰기 (보내기, 삭제는 못 해요)
- Google Calendar 읽기와 일정 추가
- 미리 알림, 타이머, 앱 열기, 볼륨, 음악 제어
- 기억하기, 숙제 파일 보고 풀이 설명
- 대시보드 창과 3D 홀로그램 코어 (three.js)

## 필요한 것

- macOS (Apple Silicon 권장), Python 3.12, [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://claude.com/claude-code) CLI 로그인
- Gmail, Calendar, Drive를 쓰려면 claude.ai에서 해당 커넥터 연결

## 실행

```bash
cp .env.example .env     # 학교 코드, 학년, 반, 도시 입력
./run.sh                 # 메뉴 막대 아이콘 + "Hey Jarvis" 대기
./run.sh --dashboard     # 대시보드 창도 같이 열기
./run.sh --browser       # 브라우저에서 대시보드만
```

처음 실행하면 `.venv`를 만들고 의존성을 설치해요. 마이크 권한을 허용해 주세요.

## 구성

| 파일 | 하는 일 |
| --- | --- |
| `app.py` | 메뉴 막대 아이콘, 드롭다운 HUD 창, 대시보드 창 (pywebview) |
| `server.py` | 로컬 서버(127.0.0.1:8765)와 WebSocket |
| `jarvis.py` | 호출어 감지, 음성 인식(faster-whisper), Claude Code 호출, 음성 출력 |
| `tools_server.py` | Jarvis 전용 MCP 도구 (시간표, 급식, 맥 제어, 기억) |
| `ui/` | 대시보드와 HUD 화면, 3D 코어 |

개인 설정(`.env`), 기억(`data/`), 업로드한 파일은 깃에 올라가지 않아요.

three.js는 `ui/vendor/three`에 MIT 라이선스로 포함돼 있어요.
