# 🎛️ TrackPad Controller — 크로스플랫폼 PRD

> **참고 레퍼런스:** [real-track-pad (dogoyster)](https://github.com/dogoyster/real-track-pad) — macOS 전용 트랙패드 존 컨트롤러  
> **목표:** 동일한 개념을 **Windows + macOS** 양쪽에서 동작하도록 확장한 애플리케이션 개발

> [!NOTE]
> **확정된 결정 사항**  
> - 기술 스택: **안 B — Electron + React + TypeScript**  
> - MVP 범위: **MIDI 출력 + 키보드 단축키 매핑 전부 포함**  
> - Windows 트랙패드: **구형 HID(상대좌표) + Precision Touchpad 모두 지원**

---

## 1. 제품 개요 (Product Overview)

### 1.1 한 줄 요약
트랙패드 표면을 가상의 구역(Zone)으로 나눠, 손가락 위치에 따라 MIDI 노트·단축키·매크로를 트리거하는 **크로스플랫폼 데스크탑 앱**.

### 1.2 배경 및 문제 정의
| 문제 | 내용 |
|------|------|
| 레퍼런스 한계 | 기존 real-track-pad는 macOS CGEvent / Core Graphics API에 강하게 종속되어 Windows에서 동작 불가 |
| 하드웨어 비용 | 물리 패드 컨트롤러(AKAI MPC, Launchpad 등) 구매 없이 트랙패드만으로 동일 기능 구현 |
| 범용성 부재 | 음악 제작 외에 프레젠테이션·게임·자동화 등 다양한 사용 목적 지원 필요 |

### 1.3 핵심 가치 제안
- **무료 & 오픈소스** — 하드웨어 컨트롤러 없이 트랙패드 하나로 충분
- **크로스플랫폼** — Windows 10/11 + macOS 12 Monterey 이상 동시 지원
- **확장 가능** — MIDI, 키보드 단축키, 커스텀 매크로를 플러그인 방식으로 추가

---

## 2. 사용자 타겟 (Target Users)

| 페르소나 | 설명 | 주요 니즈 |
|----------|------|-----------|
| 🎵 음악 프로듀서 | DAW(Ableton, FL Studio)를 노트북으로 운용 | MIDI 패드 대체, 루프 퀀타이즈, 노트 리피트 |
| 🎮 게이머 | 노트북 트랙패드로 빠른 매크로 실행 | 낮은 레이턴시, 영역별 단축키 |
| 📊 프레젠터 | 슬라이드 제어, 줌 제스처 커스터마이징 | 직관적 UI, 간단한 설정 |
| 🛠️ 개발자/파워유저 | 반복 CLI 명령·script 자동화 | 스크립트 연동, 오픈 API |

---

## 3. 핵심 기능 요구사항 (Core Features)

### 3.1 트랙패드 존 시스템 (Zone System)

| 기능 | 상세 |
|------|------|
| **존 그리드 레이아웃** | 2×2 / 3×3 / 4×4 등 커스텀 그리드 선택 |
| **자유형 존 커스텀** | 드래그로 임의 영역 분할 및 크기 조절 |
| **존별 액션 매핑** | 각 존에 MIDI Note / Key Shortcut / Script / Media Control 중 하나 지정 |
| **다중 레이어(뱅크)** | 숫자키(1~4)로 다른 존 레이아웃으로 즉시 전환 |
| **시각적 피드백** | 터치 감지 시 해당 존 하이라이트 애니메이션 |

### 3.2 입력 감지 엔진 (Input Engine)

| 기능 | Windows 구현 | macOS 구현 |
|------|--------------|------------|
| **절대 좌표 추적** | Raw Input API / Precision Touchpad HID | Core Graphics CGEvent Tap |
| **멀티터치** | Windows Pointer API (WM_POINTER) | NSTouch / MultitouchSupport |
| **압력 감지** | WM_POINTER pressure field | NSTouch force (Force Touch 지원 시) |
| **제스처 분리** | 시스템 제스처와 충돌 방지 (독립 드라이버 레이어) | Accessibility API + CGEvent 마스킹 |

> [!IMPORTANT]
> Windows의 경우 **Precision Touchpad** 드라이버를 사용하는 트랙패드에서만 절대 좌표 및 멀티터치가 지원됩니다. 구형 HID 트랙패드는 상대 좌표만 지원될 수 있습니다.

### 3.3 MIDI 출력 (MIDI Engine)

| 기능 | 상세 |
|------|------|
| **가상 MIDI 포트** | loopMIDI (Windows) / IAC Driver (macOS) 자동 감지 및 생성 |
| **MIDI Note On/Off** | 존 터치 시작 → Note On, 종료 → Note Off |
| **Velocity 감도** | 터치 압력 또는 속도를 MIDI velocity로 매핑 |
| **루프 퀀타이즈** | 1/8, 1/16 단위 자동 스냅 (BPM 연동) |
| **노트 리피트** | 누르고 있으면 BPM 기반 반복 트리거 |
| **CC 메시지** | 특정 존에서의 스와이프를 Continuous Controller로 매핑 |

### 3.4 키보드/매크로 출력

| 기능 | 상세 |
|------|------|
| **단축키 매핑** | 존 → 키 조합 (Ctrl+Z, Cmd+Shift+4 등) |
| **스크립트 실행** | PowerShell (Windows) / Shell Script (macOS) 실행 |
| **딜레이/시퀀스** | 여러 키 입력을 순서대로 지연 실행 |

### 3.5 메트로놈 & BPM

| 기능 | 상세 |
|------|------|
| **내장 메트로놈** | 소리/시각 비트 표시 |
| **BPM 입력** | 수동 입력 + 탭 템포(T키) |
| **MIDI Clock 동기** | 외부 DAW와 MIDI Clock 마스터/슬레이브 연동 |

### 3.6 세션 관리

| 기능 | 상세 |
|------|------|
| **자동 저장** | 마지막 모드·그리드·BPM·메트로놈 설정 복원 |
| **프리셋 시스템** | 여러 세션 파일(.tpz) 저장/불러오기 |
| **내보내기/가져오기** | JSON 기반 설정 파일로 다른 PC와 공유 |

---

## 4. 기술 아키텍처 (Technical Architecture)

### 4.1 기술 스택

```
┌─────────────────────────────────────────────────┐
│                    UI Layer                     │
│         Electron (React + TypeScript)           │
│    혹은 Python (PyQt6 / PySide6) 선택 가능     │
├─────────────────────────────────────────────────┤
│              Core Logic Layer (공통)            │
│         TypeScript / Python (크로스플랫폼)      │
│  - Zone Engine  - MIDI Engine  - Session Mgr   │
├───────────────┬─────────────────────────────────┤
│  Windows HAL  │         macOS HAL              │
│  Raw Input /  │  CGEvent Tap /                 │
│  WM_POINTER   │  NSTouch Framework             │
│  loopMIDI     │  IAC Driver (CoreMIDI)         │
│  SendInput    │  CGPostKeyboardEvent            │
└───────────────┴─────────────────────────────────┘
```

### 4.2 확정 기술 스택 — **안 B: Electron + React + TypeScript**

| 항목 | 기술 |
|------|------|
| **UI 프레임워크** | Electron 30+ + React 18 + TypeScript |
| **스타일링** | Tailwind CSS + shadcn/ui |
| **트랙패드 입력 (Win)** | Native C++ Addon (node-gyp) — WM_POINTER + Raw Input HID |
| **트랙패드 입력 (Mac)** | Native C++ Addon — CGEventTap + NSEvent |
| **MIDI** | `midi` (node-midi, RtMidi 래퍼) npm 패키지 |
| **키보드 출력** | `robotjs` / `uiohook-napi` |
| **패키징** | electron-builder (.exe 인스톨러 + .dmg) |
| **상태 관리** | Zustand |
| **IPC** | Electron ipcMain/ipcRenderer |

> [!TIP]
> Native Addon은 `node-gyp`으로 빌드하며, Windows에는 `windows-build-tools`, macOS에는 Xcode CLI 필요합니다. CI는 GitHub Actions의 `macos-latest` + `windows-latest` 매트릭스 빌드를 권장합니다.

### 4.3 플랫폼별 핵심 구현 포인트

#### Windows
```
1. Precision Touchpad (최신, 절대 좌표):
   - HID Usage Page 0x0D (Digitizer), Usage 0x05 확인
   - WM_POINTER 메시지로 절대 좌표 수신
   - POINTER_TOUCH_INFO.rcContact 로 존 계산
   - 멀티터치 동시 인식 가능

2. 구형 HID Touchpad (레거시, 상대 좌표):
   - Raw Input API로 HID 리포트 직접 파싱
   - 상대적 델타값 누적으로 가상 절대 좌표 추정
   - 앱 UI에 "레거시 모드" 표시, 정확도 한계 안내
   - WM_INPUT 메시지 핸들링

3. 가상 MIDI:
   - loopMIDI 설치 감지 → 없으면 앱 내 설치 가이드 표시
   - Windows MIDI Services (Win11 24H2+) 직접 사용 가능

4. UAC/권한:
   - Raw Input 등록은 일반 권한으로 가능
   - 전역 훅(SetWindowsHookEx)은 관리자 권한 필요 → 사용자 안내
```

#### macOS
```
1. 트랙패드 입력:
   - Accessibility 권한 요청 (CGEventTap)
   - NSEvent.addLocalMonitorForEvents 로 터치 이벤트 수신
   - MultitouchSupport.framework (비공개 API) 사용 시 주의

2. 가상 MIDI:
   - CoreMIDI IAC Driver 자동 활성화 확인
   - 없으면 AudioMIDISetup 열기 안내

3. 보안:
   - Apple 공증(Notarization) 필수 적용
   - Hardened Runtime + Entitlements 설정
```

---

## 5. UI/UX 설계 (Design Requirements)

### 5.1 메인 화면 구성

```
┌─────────────────────────────────────────────┐
│  🎛️ TrackPad Controller    [설정] [?] [─][□][×]│
├──────────────┬──────────────────────────────┤
│              │   BPM: [120] [TAP] 🎵         │
│  트랙패드   │   Layer: [1][2][3][4]         │
│  존 미러     │   Quantize: [OFF][1/8][1/16]  │
│  (실시간)    │   Note Repeat: [OFF][1/8][1/16]│
│              │                              │
│  ┌──┬──┬──┐ │   MIDI Out: [loopMIDI port 1]│
│  │  │  │  │ │   Device: [Precision TP ✓]   │
│  ├──┼──┼──┤ │                              │
│  │  │  │  │ │   [현재 Zone 설정 패널]       │
│  └──┴──┴──┘ │   Zone 2: C#3 / Vel: 100     │
│              │                              │
└──────────────┴──────────────────────────────┘
│ Status: Touchpad detected ✓ | MIDI: ✓ | 120 BPM  │
└─────────────────────────────────────────────┘
```

### 5.2 존 편집기 (Zone Editor)
- 드래그로 존 경계 조절
- 우클릭 → 컨텍스트 메뉴 (액션 타입 변경, 색상 변경, 삭제)
- 각 존에 커스텀 이름 & 색상 지정
- 터치 활성화 시 글로우 이펙트 애니메이션

### 5.3 설정 화면
- **일반**: 시작 시 자동 실행, 시스템 트레이 최소화
- **입력**: 터치 감도, 데드존 크기, 멀티터치 동시 인식 수
- **MIDI**: 기본 채널, 벨로시티 범위, Clock 출력
- **단축키**: 전역 단축키로 Layer 전환, 앱 토글

---

## 6. 단축키 기본값

| 키 | 기능 |
|----|------|
| `1` ~ `4` | Layer 1~4 전환 |
| `G` | 그리드 레이아웃 순환 (2×2 → 3×3 → 4×4) |
| `Q` | 루프 퀀타이즈 순환 (OFF → 1/8 → 1/16) |
| `N` | 노트 리피트 순환 (OFF → 1/8 → 1/16) |
| `T` | 탭 템포 (BPM 자동 계산) |
| `M` | 메트로놈 ON/OFF |
| `R` | 현재 세션 초기화 |
| `Esc` | 앱 포커스 해제 (패드 비활성화) |

---

## 7. 개발 단계 (Phased Roadmap)

### Phase 1 — MVP (4~6주) ✅ 전부 포함
- [ ] 트랙패드 입력 감지 (Windows Precision TP + 구형 HID / macOS)
- [ ] 고정 3×3 그리드 존 시스템
- [ ] **MIDI Note On/Off 출력** (loopMIDI / IAC Driver)
- [ ] **키보드 단축키 매핑** (존 → 키 조합 출력)
- [ ] 기본 시각 피드백 UI (터치 하이라이트)
- [ ] Layer 1~4 전환
- [ ] 세션 저장/복원 (JSON)
- [ ] 레거시 HID 모드 폴백 (Windows 구형 트랙패드)

### Phase 2 — Core Features (4~6주)
- [ ] 자유형 존 편집기 (드래그 리사이즈)
- [ ] 루프 퀀타이즈 + 노트 리피트
- [ ] BPM 탭 템포 + 내장 메트로놈
- [ ] 키보드 단축키·매크로 출력
- [ ] MIDI CC 매핑 (스와이프 → CC)

### Phase 3 — Polish & Platform (3~4주)
- [ ] Windows .exe 인스톨러 (NSIS/Inno Setup)
- [ ] macOS .dmg + Apple 공증
- [ ] MIDI Clock 마스터/슬레이브
- [ ] 프리셋 파일(.tpz) 내보내기/가져오기
- [ ] 다크/라이트 테마
- [ ] 다국어 (한국어/영어)

### Phase 4 — Advanced (추후)
- [ ] OSC 출력 지원
- [ ] 플러그인 API (서드파티 액션 추가)
- [ ] 화면 오버레이 모드 (항상 위 투명 표시)
- [ ] 프레셔 곡선 커스터마이징

---

## 8. 비기능 요구사항 (Non-Functional Requirements)

| 항목 | 목표 |
|------|------|
| **레이턴시** | 터치 → MIDI 출력 < 10ms |
| **CPU 사용률** | 대기 중 < 1%, 활성 중 < 5% |
| **메모리** | 실행 중 < 80MB RAM |
| **설치 크기** | < 50MB (런타임 포함) |
| **지원 OS** | Windows 10 21H2+, macOS 12 Monterey+ |
| **트랙패드** | Precision Touchpad (Win), 내장 Force Touch (Mac) 우선 지원 |

---

## 9. 제약사항 및 리스크

| 리스크 | 대응 방안 |
|--------|-----------|
| Windows 구형 HID 트랙패드 정확도 한계 | Raw Input 델타 누적 + "레거시 모드" 라벨로 사용자 안내, 정확도 낮음 명시 |
| macOS 비공개 MultitouchSupport.framework 의존 | CGEvent 기반 공개 API 우선 구현, 비공개 API는 선택적 활성화 |
| Electron 번들 크기 (~150MB) | electron-builder ASAR 압축 + 불필요 모듈 트리쉐이킹으로 최소화 |
| loopMIDI 사전 설치 필요 (Windows) | 앱 내 자동 감지 + 설치 안내 팝업, Win11 24H2+ 는 Windows MIDI Services 직접 활용 |
| node-gyp Native Addon 빌드 환경 복잡도 | prebuildify로 사전 빌드된 바이너리 배포, 일반 사용자는 npm install 만으로 동작 |

---

## 10. 성공 지표 (Success Metrics)

| 지표 | 목표 (Phase 1 이후 3개월) |
|------|--------------------------|
| GitHub Stars | 100+ |
| 다운로드 수 | 500+ (Win + Mac 합산) |
| 이슈 재현 버그율 | < 5% |
| MIDI 레이턴시 측정값 | p95 < 10ms |
| 사용자 피드백 CSAT | 4.0/5.0+ |

---

## 11. 프로젝트 구조 (예시)

```
trackpad-controller/
├── src/
│   ├── core/               # 플랫폼 독립 로직
│   │   ├── zone_engine.py  # 존 좌표 계산
│   │   ├── midi_engine.py  # MIDI 출력
│   │   ├── session.py      # 세션 저장/복원
│   │   └── quantize.py     # 루프 퀀타이즈
│   ├── platform/
│   │   ├── windows/
│   │   │   └── touchpad_win.py  # WM_POINTER / Raw Input
│   │   └── macos/
│   │       └── touchpad_mac.py  # CGEventTap / NSEvent
│   └── ui/
│       ├── main_window.py
│       ├── zone_editor.py
│       └── settings.py
├── presets/                # 예제 존 레이아웃
├── tests/
├── docs/
├── build/
│   ├── windows/            # NSIS 스크립트
│   └── macos/              # info.plist, entitlements
└── README.md
```

---

*PRD Version 0.1 | 2026-03-28 | 크로스플랫폼 TrackPad Controller*
